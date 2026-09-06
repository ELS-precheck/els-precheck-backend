"""프리셋 3종 — 실제 존재했던/판매 중인 ELS 구조.
등급 라벨은 사람이 박지 않고 expected_grade를 엔진에서 실시간 산출한다.
일부 프리셋은 발행 당시 시장을 재현하기 위해 els_terms에 vol을 직접 담는다.
"""
from app.engine import run_simulation
from app.market_data import get_vol, get_corr

_PRESET_META = [
    {"id": "developed2", "label": "선진국 2지수 안정형",
     "one_line": "S&P500·유로스톡스50 기반. 낮은 배리어로 원금 방어에 무게를 둔, 현재 가장 흔한 구조",
     "data_note": "현재 시장 변동성 기준",
     "els_terms": {"underlyings": ["S&P500", "EUROSTOXX50"], "coupon_annual": 0.05,
         "maturity_months": 36, "check_interval_months": 6,
         "step_down_barriers": [0.90, 0.90, 0.85, 0.85, 0.80, 0.75],
         "knock_in": 0.45, "principal": 10_000_000}},

    {"id": "developed3", "label": "선진국+일본 3지수 고쿠폰형",
     "one_line": "쿠폰을 높인 대신 기초자산이 3개로 늘어 손실 확률도 유의미해진 구조",
     "data_note": "현재 시장 변동성 기준",
     "els_terms": {"underlyings": ["S&P500", "EUROSTOXX50", "NIKKEI225"], "coupon_annual": 0.07,
         "maturity_months": 36, "check_interval_months": 6,
         "step_down_barriers": [0.90, 0.90, 0.90, 0.85, 0.85, 0.80],
         "knock_in": 0.50, "principal": 10_000_000}},

    {"id": "hscei2021", "label": "홍콩 H지수 ELS (2021년형)",
     "one_line": "2021년 대량 판매돼 2024년 대규모 원금손실이 난 실제 구조. 발행 시점 시장으로 재현",
     "data_note": "2021년 상반기 발행 당시 시장 변동성·상관 기준(근사)",
     "els_terms": {"underlyings": ["HSCEI", "S&P500", "EUROSTOXX50"], "coupon_annual": 0.065,
                   "maturity_months": 36, "check_interval_months": 6,
                   "step_down_barriers": [0.90, 0.90, 0.85, 0.85, 0.80, 0.65],
                   "knock_in": 0.50, "principal": 10_000_000,
                   "vol": [0.28, 0.22, 0.22],
                   "corr": [[1.0, 0.55, 0.55],  # 2021 발행 당시 크로스리전 상관(근사)
                            [0.55, 1.0, 0.55],
                            [0.55, 0.55, 1.0]]}},
]

_cache = None


def get_presets() -> list[dict]:
    global _cache
    if _cache is None:
        out = []
        for p in _PRESET_META:
            t = p["els_terms"]
            # 프리셋에 vol/corr가 있으면 그걸 쓰고(발행 당시 재현), 없으면 현재 시장값
            vol = t.get("vol") or get_vol(t["underlyings"])
            corr = t.get("corr") or get_corr(t["underlyings"])
            diag = run_simulation(
                underlyings=t["underlyings"], coupon_annual=t["coupon_annual"],
                maturity_months=t["maturity_months"], check_interval_months=t["check_interval_months"],
                step_down_barriers=t["step_down_barriers"], knock_in=t["knock_in"],
                principal=t["principal"], vol=vol, corr=corr, num_paths=20000)
            out.append({**p, "expected_grade": diag["grade"]})
        _cache = out
    return _cache