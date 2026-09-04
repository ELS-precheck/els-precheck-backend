"""
시장 데이터 (변동성·상관) 공급 모듈.
지금은 역사적 근사치로 두고 이후 과거 가격 CSV로 교체한다.
- 변동성: 그 지수가 1년에 대략 ±몇 % 출렁이는지 (클수록 위험)
- 상관: 지수들이 얼마나 같이 움직이는지
"""
from __future__ import annotations

_VOL_TABLE = {
    "KOSPI200": 0.18,
    "KOSPI": 0.18,
    "S&P500": 0.16,
    "SPX": 0.16,
    "EUROSTOXX50": 0.20,
    "SX5E": 0.20,
    "NIKKEI225": 0.20,
    "NKY": 0.20,
    "HSCEI": 0.28,
    "H지수": 0.28,
    "HSI": 0.26,
    "TESLA": 0.55,
    "TSLA": 0.55,
    "NVIDIA": 0.50,
    "NVDA": 0.50,
}
DEFAULT_VOL = 0.22
DEFAULT_PAIR_CORR = 0.50    # 자산 간 기본 상관

def _normalize(name: str) -> str:
    return name.upper().replace(" ", "").replace("_", "")


def get_vol(underlyings: list[str]) -> list[float]:
    return [_VOL_TABLE.get(_normalize(n), DEFAULT_VOL) for n in underlyings]


def get_corr(underlyings: list[str], pair_corr: float = DEFAULT_PAIR_CORR) -> list[list[float]]:
    n = len(underlyings)
    return [[1.0 if i == j else pair_corr for j in range(n)] for i in range(n)]

