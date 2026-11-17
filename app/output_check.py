"""해설 출력 검사 — AI가 만든 해설을 사용자에게 보내기 전에 거르는 단계.

검사 항목
1. 권유 표현: 금지 표현이 들어간 문장을 뺀다.
2. 근거 없는 수치: 해설 속 숫자가 요청으로 받은 ELS 조건·진단 수치(엔진 계산값)에서
   나온 값인지 확인하고, 아니면 그 문장을 뺀다. 반올림한 값(6.02 → "약 6%")은 허용한다.
3. 면책 문구: 비어 있거나 위 검사에 걸리면 기본 문구로 바꾼다.

걸린 문장은 빼고(요약은 대체 문구로 교체), 감지 내용은 로그로 남긴다.

한계: 숫자가 엔진 값 중 하나인지만 확인하고, 어느 항목의 값인지(손실 확률인지
조기상환 확률인지)까지는 보지 않는다. 권유 표현은 목록에 있는 표현만 잡는다.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal

from app.market_data import normalize_underlying

logger = logging.getLogger(__name__)

PROHIBITED = [
    "지금이 적기", "추천합니다", "권유합니다", "권유드립니다",
    "가입하세요", "투자하세요", "매수하세요", "매도하세요",
]

DEFAULT_DISCLAIMER = "본 해설은 투자권유가 아니라 정보 제공입니다."
FALLBACK_SUMMARY = "진단 수치를 바탕으로 이 상품의 위험 구조를 확인하세요."
FALLBACK_EXPLANATION = "진단 수치를 바탕으로 위험 구조를 직접 확인해 주세요."

# 해설에서 쓰일 수 있는 고정 수치: CVaR(95%)의 95, 하위 5%의 5, 원금 100%의 100
_FIXED_NUMBERS = (5, 95, 100)

# 해설 인용 대상이 아닌 진단 응답 항목 (분포 막대·실행 메타 정보)
_SKIP_KEYS = {"return_distribution", "meta"}

# 숫자 한 덩어리: 숫자 + 선택적 소수부 + 선택적 작은 단위(천·백·십) + 선택적 큰 단위(조·억·만) + 선택적 %.
# "1억 5,000만", "52만 5천"처럼 큰 단위로 이어지는 덩어리는 합쳐서 한 금액으로 본다.
# 큰 단위 글자로 시작하는 낱말(조건·조기·만기·만큼 등)은 단위로 읽지 않는다.
# 영문자 바로 뒤에 붙은 숫자(KOSPI200 등)는 _TICKER_RE로 지수명 전체를 따로 검사한다.
_SEG_RE = re.compile(
    r"(?<![A-Za-z0-9.,])(-?)(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?"
    r"\s*(천|백(?!분)|십)?"
    r"\s*(조(?!건|기|정|사|합)|억(?!제)|만(?!기|큼|약|일|족|료|들|나|에|으|이))?"
    r"\s*(%|퍼센트)?")
_SMALL = {"천": 1_000, "백": 100, "십": 10}
_BIG = {"조": 10 ** 12, "억": 10 ** 8, "만": 10 ** 4}
_TICKER_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z&]*\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Allowed:
    percent: list[float]   # %로 쓴 숫자와 비교할 값
    plain: list[float]     # 그 밖의 숫자와 비교할 값
    names: frozenset[str]  # 입력 기초자산의 표준명 (KOSPI200 등)


def _as_list(v) -> list[str]:
    if isinstance(v, list):
        return [s for s in v if isinstance(s, str)]
    if isinstance(v, str):
        return [v]
    return []


def _collect_numbers(obj, out: list[float]) -> None:
    """dict/list/str 안의 숫자를 모두 모은다 (_SKIP_KEYS 제외)."""
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, str):
        out.extend(float(d) for d in re.findall(r"\d+(?:\.\d+)?", obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k not in _SKIP_KEYS:
                _collect_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_numbers(v, out)


def build_allowed_numbers(els_terms: dict, diagnosis: dict, user_desc: str = "") -> Allowed:
    """해설에 등장해도 되는 수치와 기초자산 이름."""
    base: list[float] = []
    _collect_numbers(els_terms, base)
    _collect_numbers(diagnosis, base)
    _collect_numbers(user_desc, base)  # "40~50대 중립형" 같은 투자자 설명

    # 조건에서 바로 따라 나오는 값: 만기·평가 시점(개월/년), 평가 횟수, 회차
    months = [els_terms.get("maturity_months"), els_terms.get("check_interval_months")]
    months += [s.get("month") for s in (diagnosis.get("early_redemption_by_step") or [])
               if isinstance(s, dict)]
    months = [float(m) for m in months if isinstance(m, (int, float))]
    nobs = len(els_terms.get("step_down_barriers") or [])
    derived = [m / 12 for m in months] + [float(k) for k in range(1, nobs + 1)]
    derived.append(float(len(els_terms.get("underlyings") or [])))

    def _with_abs(values) -> list[float]:
        out = set()
        for v in values:
            out.add(round(v, 6))
            out.add(round(abs(v), 6))  # "-69.74%"를 "69.74% 손실"로 쓰는 경우
        return sorted(out)

    # 퍼센트 표기: 비율 값(확률·쿠폰·배리어·변동성 등, 절댓값 3 이하)을 100배한 값만
    percent = _with_abs([x * 100 for x in base if abs(x) <= 3] + list(_FIXED_NUMBERS))
    # 그 밖의 표기: 개월·년·회차·금액·지수명 숫자 등. 소수 비율값(0.0602)은 그대로 쓰지 않으므로 제외
    plain = _with_abs([x for x in base if not (abs(x) <= 3 and x != int(x))] + derived)
    names = frozenset(normalize_underlying(u) for u in (els_terms.get("underlyings") or [])
                      if isinstance(u, str))
    return Allowed(percent, sorted(set(plain) | set(percent)), names)


def _segment(m: re.Match) -> tuple[float, float, int, bool]:
    """덩어리 하나 → (값, 반올림 허용 오차, 큰 단위 크기, 퍼센트 여부)."""
    sign, int_part, frac, small, big, pct = m.groups()
    decimals = len(frac) - 1 if frac else 0
    mult = _SMALL.get(small, 1) * _BIG.get(big, 1)
    value = float(Decimal(sign + int_part.replace(",", "") + (frac or ""))) * mult
    tol = 0.5 * (10 ** -decimals) * mult
    return value, tol, _BIG.get(big, 1), pct is not None


def _quantities(text: str):
    """문장 속 수량을 (표기, 값, 허용 오차, 퍼센트 여부)로 돌려준다. 이어진 금액 단위는 합친다."""
    matches = [m for m in _SEG_RE.finditer(text) if m.group(0).strip()]
    i = 0
    while i < len(matches):
        m = matches[i]
        value, tol, big, is_pct = _segment(m)
        start, end = m.start(), m.end()
        # "1억 5,000만", "52만 5천": 큰 단위 뒤에 더 작은 단위의 덩어리가 공백만 두고 이어지면 합친다
        while (not is_pct and big > 1 and i + 1 < len(matches)
               and not text[end:matches[i + 1].start()].strip()):
            nxt = matches[i + 1]
            n_value, n_tol, n_big, n_pct = _segment(nxt)
            if n_pct or nxt.group(1) or n_big >= big:
                break
            value, tol, big, end = value + n_value, n_tol, n_big, nxt.end()
            i += 1
        yield text[start:end].strip(), value, tol + 1e-9, is_pct
        i += 1


def find_unsupported_number(text: str, allowed: Allowed) -> str | None:
    """근거 없는 수치나 입력에 없는 지수명이 있으면 그 표기를, 없으면 None을 돌려준다."""
    if not isinstance(text, str):
        return None
    text = text.replace("−", "-")
    # 영문자에 붙은 숫자(KOSPI200, S&P500)는 지수명 전체가 입력 기초자산과 같아야 통과
    for t in _TICKER_RE.finditer(text):
        if normalize_underlying(t.group(0)) not in allowed.names:
            return t.group(0)
    for shown, value, tol, is_pct in _quantities(text):
        pool = allowed.percent if is_pct else allowed.plain
        if not any(abs(a - value) <= tol for a in pool):
            return shown
    return None


def find_prohibited(text: str) -> str | None:
    if not isinstance(text, str):
        return None
    return next((k for k in PROHIBITED if k in text), None)


def _violation(text: str, allowed: Allowed) -> str | None:
    kw = find_prohibited(text)
    if kw:
        return f"권유 표현 '{kw}'"
    num = find_unsupported_number(text, allowed)
    if num:
        return f"근거 없는 수치 '{num}'"
    return None


def check_explanation(data: dict, els_terms: dict, diagnosis: dict, user_desc: str = "") -> dict:
    """해설 응답을 검사해 걸린 내용을 빼거나 대체 문구로 바꾼다."""
    allowed = build_allowed_numbers(els_terms, diagnosis, user_desc)

    # summary_line: 단일 문장 → 위반 시 대체 문구
    reason = _violation(data.get("summary_line", ""), allowed)
    if reason:
        logger.warning("출력 검사 [summary_line]: %s", reason)
        data["summary_line"] = FALLBACK_SUMMARY

    # explanation: 위반 문장만 빼고 합침 (스키마상 문장 배열로 받음)
    clean_exp = []
    for sentence in _as_list(data.get("explanation")):
        reason = _violation(sentence, allowed)
        if reason:
            logger.warning("출력 검사 [explanation]: %s", reason)
        else:
            clean_exp.append(sentence)
    data["explanation"] = " ".join(clean_exp).strip() or FALLBACK_EXPLANATION

    # cautions: 위반 항목만 뺌
    clean_cautions = []
    for c in _as_list(data.get("cautions")):
        reason = _violation(c, allowed)
        if reason:
            logger.warning("출력 검사 [cautions]: %s", reason)
        else:
            clean_cautions.append(c)
    data["cautions"] = clean_cautions

    # disclaimer: 비었거나 위반이면 기본 문구
    disclaimer = data.get("disclaimer")
    if not isinstance(disclaimer, str) or not disclaimer.strip():
        data["disclaimer"] = DEFAULT_DISCLAIMER
    else:
        reason = _violation(disclaimer, allowed)
        if reason:
            logger.warning("출력 검사 [disclaimer]: %s", reason)
            data["disclaimer"] = DEFAULT_DISCLAIMER

    return data
