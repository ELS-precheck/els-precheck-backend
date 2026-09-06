"""시장 데이터 공급: 변동성·상관.
우선순위: (1) 요청의 vol/corr(PDF 내재변동성 등) → main.py에서 처리
          (2) 여기: CSV(내재/실현변동성, app/data) → (3) 자산군별 보수적 기본값.
서버는 런타임에 외부 API를 부르지 않고 CSV만 읽는다(배포 안정성)."""
from __future__ import annotations
from pathlib import Path
import csv

_DATA_DIR = Path(__file__).resolve().parent / "data"

# 자산군별 최후 기본값 (CSV·PDF 모두 없을 때). 위험 과소평가 방지를 위해 보수적으로.
DEFAULT_INDEX_VOL = 0.25
DEFAULT_STOCK_VOL = 0.45
DEFAULT_PAIR_CORR = 0.50

_STOCK_HINTS = {"TESLA", "TSLA", "NVIDIA", "NVDA", "APPLE", "AAPL", "삼성전자", "SAMSUNG"}

# ai.py 등에서 "지원 기초자산" 판정에 사용 (표준명 + 별칭)
SUPPORTED_UNDERLYINGS = {
    "KOSPI200", "KOSPI", "S&P500", "SPX", "EUROSTOXX50", "SX5E",
    "NIKKEI225", "NKY", "HSCEI", "H지수", "HSI",
    "TESLA", "TSLA", "NVIDIA", "NVDA",
}


def _normalize(name: str) -> str:
    return name.upper().replace(" ", "").replace("_", "")

def normalize_underlying(name: str) -> str:
    """외부(ai.py 등)에서 쓰는 공개 정규화 함수."""
    return _normalize(name)

def _load_vol() -> dict[str, float]:
    p = _DATA_DIR / "vol.csv"
    out: dict[str, float] = {}
    if p.exists():
        with open(p, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                out[_normalize(row["asset"])] = float(row["vol"])
    return out


def _load_corr() -> dict[str, dict[str, float]]:
    p = _DATA_DIR / "corr.csv"
    mat: dict[str, dict[str, float]] = {}
    if p.exists():
        with open(p, encoding="utf-8") as f:
            rd = csv.reader(f)
            names = [_normalize(h) for h in next(rd)[1:]]  # 첫 칸은 인덱스
            for row in rd:
                mat[_normalize(row[0])] = {names[i]: float(row[i + 1]) for i in range(len(names))}
    return mat


_VOL = _load_vol()
_CORR = _load_corr()


def get_asof() -> str | None:
    """데이터 기준일(투명성 표시용)."""
    p = _DATA_DIR / "asof.txt"
    return p.read_text(encoding="utf-8").strip() if p.exists() else None


def _default_vol(name: str) -> float:
    return DEFAULT_STOCK_VOL if _normalize(name) in _STOCK_HINTS else DEFAULT_INDEX_VOL


def get_vol(underlyings: list[str]) -> list[float]:
    """CSV(내재/실현변동성) 우선, 없으면 자산군별 보수적 기본값."""
    return [_VOL.get(_normalize(n), _default_vol(n)) for n in underlyings]


def get_corr(underlyings: list[str], pair_corr: float = DEFAULT_PAIR_CORR) -> list[list[float]]:
    """모든 자산이 CSV에 있으면 실데이터 부분행렬(유효한 상관행렬),
    하나라도 없으면 균일 기본상관(양의정부호 보장)."""
    keys = [_normalize(n) for n in underlyings]
    n = len(keys)
    all_in = all(k in _CORR and all(k2 in _CORR[k] for k2 in keys) for k in keys)
    out = [[1.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                out[i][j] = _CORR[keys[i]][keys[j]] if all_in else pair_corr
    return out