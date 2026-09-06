"""시장 데이터 수집 → CSV 캐시 (로컬 1회 실행).
변동성 우선순위: 시장 내재변동성지수(자동 yfinance + 수동 입력) > 과거 실현변동성."""
from __future__ import annotations
import datetime as dt
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

OUT_DIR = Path(__file__).resolve().parent.parent / "app" / "data"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 지수 가격 티커 (실현변동성·상관 계산용)
PRICE_TICKERS = {
    "KOSPI200": "^KS11", "S&P500": "^GSPC", "EUROSTOXX50": "^STOXX50E",
    "NIKKEI225": "^N225", "HSCEI": "^HSCE",
}

# 내재변동성지수 — 자동(yfinance 티커 있는 것)
VOL_INDEX_YF = {
    "S&P500": "^VIX",
    "EUROSTOXX50": "^V2TX",   # VSTOXX (야후 티커 불안정할 수 있음)
}

# 내재변동성지수 — 수동 입력 (2026-09-04 기준, 공개 소스). 지수값 ÷ 100.
VOL_INDEX_MANUAL = {
    "KOSPI200": 0.3933,    # VKOSPI 39.33
    "NIKKEI225": 0.2719,   # 닛케이225 변동성지수 27.19
    "HSCEI": 0.1716,       # VHSI 17.16 (HSCEI 대용)
}


def get_series(ticker: str) -> pd.Series:
    try:
        df = yf.download(ticker, period="1y", progress=False, auto_adjust=True)
        if df is None or df.empty:
            return pd.Series(dtype=float)
        s = df["Close"].dropna().squeeze()
        s.index = pd.to_datetime(s.index).tz_localize(None)
        return s
    except Exception as e:
        print(f"    (에러: {str(e)[:50]})")
        return pd.Series(dtype=float)


def main():
    # 1) 가격 → 실현변동성 + 상관
    closes = {}
    for name, tk in PRICE_TICKERS.items():
        s = get_series(tk)
        if len(s) > 30:
            closes[name] = s
            print(f"[가격 OK] {name}: {len(s)} rows")
        else:
            print(f"[가격 SKIP] {name}")

    realized = {}
    for name, s in closes.items():
        r = np.log(s / s.shift(1)).dropna()
        r = r[r.abs() <= 0.25]
        realized[name] = float(r.std() * np.sqrt(252))

    # 2) 내재변동성 — 자동(yfinance)
    implied = {}
    for name, tk in VOL_INDEX_YF.items():
        s = get_series(tk)
        if len(s) > 5:
            implied[name] = round(float(s.iloc[-1]) / 100.0, 4)
            print(f"[내재 OK] {name}: {tk} = {implied[name]}")
        else:
            print(f"[내재 SKIP] {name}: {tk} (야후에 없음)")

    # 3) 내재변동성 — 수동 입력
    for name, v in VOL_INDEX_MANUAL.items():
        if v is not None:
            implied[name] = round(float(v), 4)
            print(f"[내재 수동] {name} = {implied[name]}")

    # 4) 최종 변동성: 내재 우선, 없으면 실현
    rows = []
    for name in closes:
        if name in implied:
            rows.append({"asset": name, "vol": implied[name], "source": "implied"})
        elif name in realized:
            rows.append({"asset": name, "vol": round(realized[name], 4), "source": "realized"})
    vol_df = pd.DataFrame(rows)

    # 5) 상관계수 (과거 수익률)
    rets = {n: np.log(s / s.shift(1)).dropna() for n, s in closes.items()}
    ret_df = pd.DataFrame(rets).dropna()
    corr_df = ret_df.corr().round(4)

    # 자동수집 자산: yfinance 시계열의 마지막 실제 거래일
    # 수동입력 자산: 값을 확인한 명시적 기준일 상수
    VOL_INDEX_ASOF = {  # 수동 내재변동성값의 실제 기준일
        "KOSPI200": "2026-09-04",
        "NIKKEI225": "2026-09-04",
        "HSCEI": "2026-09-04",
    }

    # vol_df 만들 때 asset별 asof 컬럼을 이미 채운다고 가정하고,
    # 아래처럼 각 행 기준일을 개별 기록:
    #   - 자동수집: price_df.index[-1].strftime("%Y-%m-%d")
    #   - 수동입력: VOL_INDEX_ASOF[asset]
    # (source 컬럼으로 자동/수동 구분해서 분기)

    vol_df.to_csv(OUT_DIR / "vol.csv", index=False)
    corr_df.to_csv(OUT_DIR / "corr.csv")

    # 전역 asof.txt는 "가장 오래된 입력 기준일"로 = 가장 보수적
    global_asof = min(vol_df["asof"])
    (OUT_DIR / "asof.txt").write_text(global_asof)

    print("\n=== 최종 변동성 (source 표기) ===")
    print(vol_df.to_string(index=False))
    print("\n=== 상관계수 ===")
    print(corr_df.to_string())
    print(f"\n저장 완료 (기준일 {asof})")


if __name__ == "__main__":
    main()