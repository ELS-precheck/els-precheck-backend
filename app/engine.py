"""
몬테카를로 ELS 진단 엔진
"""
from __future__ import annotations
import time
import numpy as np


def _observation_indices(steps: int, nobs: int) -> list[int]:
    return [int(round(steps * (i + 1) / nobs)) for i in range(nobs)]


def _simulate_chunk(
        n: int, *, steps: int, nobs: int, obs: list[int], dt: float, T: float,
        vol_arr, L, r: float, coupon_annual: float,
        step_down_barriers, knock_in, rng,
):
    """청크(n개 경로) 시뮬레이션 → 경로별 (payoff, rtime, redeem_step)만 반환.
    payoff는 원금 1.0 기준 배수(1.06=+6%, 0.55=45%손실).
    무거운 배열(paths 등)은 이 함수 안에서만 살고 반환 후 사라진다."""

    na = len(vol_arr)

    # 1) 상관된 난수(촐레스키) → 2) GBM 경로 → 3) worst-of(가장 부진한 자산)
    Z = rng.standard_normal((n, steps, na))
    Zc = Z @ L.T
    logret = (r - 0.5 * vol_arr ** 2) * dt + vol_arr * np.sqrt(dt) * Zc
    paths = np.exp(np.cumsum(logret, axis=1))
    worst = paths.min(axis=2)

    redeemed = np.zeros(n, dtype=bool)
    payoff = np.zeros(n, dtype=float)
    rtime = np.full(n, T, dtype=float)
    redeem_step = np.full(n, -1, dtype=int)

    # 각 점검일: worst-of가 그날 배리어 이상이면 조기상환(쿠폰 주고 종료)
    # 마지막 점검일 = 만기. 그 배리어가 '만기 관문'.
    for k, o in enumerate(obs):
        yrs = T * (k + 1) / nobs
        hit = (~redeemed) & (worst[:, o - 1] >= step_down_barriers[k])
        payoff[hit] = 1.0 + coupon_annual * yrs
        rtime[hit] = yrs
        redeem_step[hit] = k
        redeemed[hit] = True


    # 남은 경로 = 만기 관문조차 못 넘음
    alive = ~redeemed
    if alive.any():
        fw = worst[alive][:, -1]           # 만기 시점 worst-of (< 마지막 배리어)
        if knock_in is None:
            # 노낙인: 만기 관문 미달이면 곧바로 손실
            payoff[alive] = fw
        else:
            # 낙인형: 낙인선을 한 번도 안 건드렸으면 원금+쿠폰, 건드렸으면 손실
            mn = worst[alive].min(axis=1)
            ki = mn < knock_in
            payoff[alive] = np.where(~ki, 1.0 + coupon_annual * T, fw)

    return payoff, rtime, redeem_step


def run_simulation(
    *,
    underlyings: list[str],
    coupon_annual: float,
    maturity_months: int,
    check_interval_months: int,
    step_down_barriers: list[float],
    knock_in: float | None,
    principal: int,
    vol: list[float],
    corr: list[list[float]],
    r: float = 0.032,
    num_paths: int = 100000,
    seed: int = 0,
    chunk_size: int = 20000,
) -> dict:
    """한 번의 진단 → 명세(/api/diagnose)의 data 필드 전부 반환."""
    t0 = time.perf_counter()

    T = maturity_months / 12.0
    nobs = len(step_down_barriers)

    # 시간 격자: 대략 주 단위. 점검일이 격자에 맞게 nobs 배수로 보정
    steps = max(nobs, int(round(maturity_months * 4.33)))
    steps = int(np.ceil(steps / nobs) * nobs)
    dt = T / steps

    vol_arr = np.asarray(vol, dtype=float)
    L = np.linalg.cholesky(np.asarray(corr, dtype=float))  # 청크마다 재사용(1회 계산)
    obs = _observation_indices(steps, nobs)

    rng = np.random.default_rng(seed)   # seed 고정 → 재현성

    # 청크 결과 누적(경로별 스칼라라 가벼움)
    payoff_parts, rtime_parts, step_parts = [], [], []
    remaining = num_paths
    while remaining > 0:
        n = min(chunk_size, remaining)
        p, rt, rs = _simulate_chunk(
            n, steps=steps, nobs=nobs, obs=obs, dt=dt, T=T,
            vol_arr=vol_arr, L=L, r=r, coupon_annual=coupon_annual,
            step_down_barriers=step_down_barriers, knock_in=knock_in, rng=rng,
        )
        payoff_parts.append(p); rtime_parts.append(rt); step_parts.append(rs)
        remaining -= n

    payoff = np.concatenate(payoff_parts)
    rtime = np.concatenate(rtime_parts)
    redeem_step = np.concatenate(step_parts)

    # ---- 집계 ----
    total_ret = payoff - 1.0
    loss_probability = float((payoff < 1.0).mean())

    early_mask = (redeem_step >= 0) & (redeem_step < nobs - 1)
    early_probability = float(early_mask.mean())
    maturity_probability = max(0.0, 1.0 - early_probability - loss_probability)

    early_by_step = []
    for k in range(nobs):
        month = check_interval_months * (k + 1)
        early_by_step.append({"month": int(month),
                              "prob": round(float((redeem_step == k).mean()), 4)})

    avg_years = float(rtime.mean())
    total_ret_mean = float(total_ret.mean())
    expected_return = total_ret_mean / avg_years if avg_years > 0 else total_ret_mean

    sorted_ret = np.sort(total_ret)
    n_tail = max(1, int(num_paths * 0.05))
    cvar_95 = float(sorted_ret[:n_tail].mean())

    lo = min(-0.6, float(total_ret.min()))
    hi = max(0.3, float(total_ret.max()))
    counts_arr, bins_arr = np.histogram(total_ret, bins=24, range=(lo, hi))

    return {
        "loss_probability": round(loss_probability, 4),
        "grade": classify_grade(loss_probability),
        "expected_return": round(expected_return, 4),
        "promised_coupon_annual": round(coupon_annual, 4),
        "cvar_95": round(cvar_95, 4),
        "early_redemption_probability": round(early_probability, 4),
        "outcome_split": {
            "early": round(early_probability, 4),
            "maturity": round(maturity_probability, 4),
            "loss": round(loss_probability, 4),
        },
        "early_redemption_by_step": early_by_step,
        "return_distribution": {
            "bins": [round(float(b), 4) for b in bins_arr],
            "counts": [int(c) for c in counts_arr],
        },
        "principal": int(principal),
        "expected_return_amount": int(round(principal * expected_return)),
        "meta": {"num_paths": int(num_paths),
                 "compute_ms": int(round((time.perf_counter() - t0) * 1000)),
                 "steps": int(steps)},
    }


def classify_grade(loss_probability: float) -> str:
    """원금손실 확률로 위험등급(임계값 튜닝 가능)."""
    if loss_probability < 0.05:
        return "저위험"
    if loss_probability < 0.10:
        return "중위험"
    return "고위험"