"""프리셋 3종. expected_grade는 엔진 결과에서 자동 산출."""
from app.engine import run_simulation
from app.market_data import get_vol, get_corr

_PRESET_META = [
    {"id": "low", "label": "저위험",
     "one_line": "지수가 크게 안 떨어지면 안정적으로 수익",
     "els_terms": {"underlyings": ["KOSPI200", "S&P500"], "coupon_annual": 0.055,
         "maturity_months": 36, "check_interval_months": 6,
         "step_down_barriers": [0.85, 0.85, 0.80, 0.80, 0.75, 0.70],
         "knock_in": 0.45, "principal": 10_000_000}},
    {"id": "mid", "label": "중위험",
     "one_line": "쿠폰은 높지만 손실 확률도 무시 못 할 수준",
     "els_terms": {"underlyings": ["EUROSTOXX50", "NIKKEI225"], "coupon_annual": 0.075,
         "maturity_months": 36, "check_interval_months": 6,
         "step_down_barriers": [0.95, 0.90, 0.90, 0.85, 0.85, 0.80],
         "knock_in": 0.55, "principal": 10_000_000}},
    {"id": "high", "label": "고위험(2021 H지수 재현)",
     "one_line": "높은 쿠폰의 대가로 원금손실 위험이 큰 구조",
     "els_terms": {"underlyings": ["HSCEI", "EUROSTOXX50", "S&P500"], "coupon_annual": 0.085,
         "maturity_months": 36, "check_interval_months": 6,
         "step_down_barriers": [0.95, 0.95, 0.90, 0.90, 0.85, 0.70],
         "knock_in": 0.55, "principal": 10_000_000}},
]

_cache = None


def get_presets() -> list[dict]:
    global _cache
    if _cache is None:
        out = []
        for p in _PRESET_META:
            t = p["els_terms"]
            diag = run_simulation(**t, vol=get_vol(t["underlyings"]),
                                  corr=get_corr(t["underlyings"]), num_paths=20000)
            out.append({**p, "expected_grade": diag["grade"]})
        _cache = out
    return _cache