"""Claude API 연동 — PDF 추출 + 해설 생성"""
from __future__ import annotations
import base64
import json
import logging
import os

import anthropic

from app.market_data import SUPPORTED_UNDERLYINGS, normalize_underlying

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")

_PROHIBITED = [
    "지금이 적기", "추천합니다", "권유합니다", "권유드립니다",
    "가입하세요", "투자하세요", "매수하세요", "매도하세요",
]


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


class LLMError(Exception):
    pass


class ExtractionError(Exception):
    pass


_FALLBACK_SUMMARY = "진단 수치를 바탕으로 이 상품의 위험 구조를 확인하세요."
_FALLBACK_EXPLANATION = "진단 수치를 바탕으로 위험 구조를 직접 확인해 주세요."


def _as_list(v) -> list[str]:
    if isinstance(v, list):
        return [s for s in v if isinstance(s, str)]
    if isinstance(v, str):
        return [v]
    return []


def _find_keyword(text: str) -> str | None:
    if not isinstance(text, str):
        return None
    return next((k for k in _PROHIBITED if k in text), None)


def _filter_prohibited(data: dict) -> dict:
    """권유 표현 필터. 위반 내용은 제거하거나 폴백으로 교체하고 로그를 남긴다."""
    # summary_line: 단일 문장 → 위반 시 폴백으로 교체
    kw = _find_keyword(data.get("summary_line", ""))
    if kw:
        logger.warning("권유 표현 감지 [summary_line]: keyword=%s", kw)
        data["summary_line"] = _FALLBACK_SUMMARY

    # explanation: 위반 문장만 drop 후 join (스키마상 배열로 받음)
    clean_exp = []
    for sentence in _as_list(data.get("explanation")):
        kw = _find_keyword(sentence)
        if kw:
            logger.warning("권유 표현 감지 [explanation]: keyword=%s", kw)
        else:
            clean_exp.append(sentence)
    data["explanation"] = " ".join(clean_exp).strip() or _FALLBACK_EXPLANATION

    # cautions: 위반 항목만 drop
    clean_cautions = []
    for c in _as_list(data.get("cautions")):
        kw = _find_keyword(c)
        if kw:
            logger.warning("권유 표현 감지 [cautions]: keyword=%s", kw)
        else:
            clean_cautions.append(c)
    data["cautions"] = clean_cautions

    return data


_EXPLAIN_SYSTEM = (
    "당신은 ELS(주가연계증권) 위험을 쉽게 설명하는 전문가입니다. "
    "투자자 눈높이에 맞는 명확하고 중립적인 해설을 한국어로 작성하세요. "
    "매수·매도·투자 권유·특정 상품 추천 등 투자 권유 표현은 절대 사용하지 마세요. "
    "진단 수치에 없는 새로운 수치나 확률을 만들어 내지 마세요."
)

_EXPLAIN_TOOL = {
    "name": "explain_els",
    "description": "ELS 위험 해설 결과를 구조화된 형태로 반환합니다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary_line": {"type": "string", "description": "결과 상단에 표시할 한 줄 요약"},
            "explanation": {
                "type": "array",
                "items": {"type": "string"},
                "description": "문단형 해설을 문장 단위로 나눈 배열 (3~5문장)",
            },
            "cautions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "주의사항 (1~3개, 진단 수치에 있는 구체적 수치만 인용)",
            },
            "disclaimer": {"type": "string", "description": "면책 문구"},
        },
        "required": ["summary_line", "explanation", "cautions", "disclaimer"],
    },
}

_EXPLAIN_PROMPT_TMPL = """아래 ELS 진단 결과를 {user_desc} 투자자에게 설명하세요.

## ELS 조건
{els_terms_json}

## 진단 수치
{diagnosis_json}

규칙:
- summary_line: 손실 확률과 투자자 성향 언급 포함, 1문장
- explanation: 쉬운 말로 쿠폰·손실 위험·기대수익 차이를 설명, 문장마다 배열 항목으로 분리
- cautions: 1~3개, 진단 수치에 있는 구체적 수치만 인용
- disclaimer: 항상 면책 문구 포함"""

_AGE_MAP = {"20_30s": "20~30대", "40_50s": "40~50대", "60s_plus": "60대 이상"}
_RISK_MAP = {"conservative": "안정추구형", "neutral": "중립형", "aggressive": "공격투자형"}


def _user_desc(user_profile: dict | None) -> str:
    if not user_profile:
        return "표준(중립)"
    parts = [
        _AGE_MAP.get(user_profile.get("age_band", ""), ""),
        _RISK_MAP.get(user_profile.get("risk_appetite", ""), ""),
    ]
    return " ".join(p for p in parts if p) or "표준(중립)"


def generate_explanation(els_terms: dict, diagnosis: dict, user_profile: dict | None) -> dict:
    """진단 결과 해설 생성. LLM 오류 시 LLMError 발생."""
    prompt = _EXPLAIN_PROMPT_TMPL.format(
        user_desc=_user_desc(user_profile),
        els_terms_json=json.dumps(els_terms, ensure_ascii=False, indent=2),
        diagnosis_json=json.dumps(diagnosis, ensure_ascii=False, indent=2),
    )
    try:
        msg = _get_client().messages.create(
            model=MODEL,
            max_tokens=1024,
            timeout=30.0,
            system=_EXPLAIN_SYSTEM,
            tools=[_EXPLAIN_TOOL],
            tool_choice={"type": "tool", "name": "explain_els"},
            messages=[{"role": "user", "content": prompt}],
        )
        tool_block = next(b for b in msg.content if b.type == "tool_use")
        result = tool_block.input
    except (anthropic.APIError, TypeError) as e:
        raise LLMError(str(e)) from e
    except StopIteration as e:
        raise LLMError("응답에서 해설 데이터를 찾을 수 없습니다.") from e

    result.setdefault("disclaimer", "본 해설은 투자권유가 아니라 정보 제공입니다.")
    return _filter_prohibited(result)


# ---------- PDF 추출 ----------

_EXTRACT_SYSTEM = (
    "당신은 ELS(주가연계증권) 상품설명서에서 핵심 조건을 정확히 추출하는 전문가입니다. "
    "문서에 명시된 내용만 사용하고, 없는 수치를 만들어 내지 마세요."
    "'이론가격 산출에 사용한 변동성' 표에 값이 있으면 vol로 추출하고, 없으면 null로 둡니다. "
    "step_down_barriers에는 만기 배리어까지 포함해 관찰 횟수만큼 넣습니다."
)

_EXTRACT_TOOL = {
    "name": "extract_els_terms",
    "description": "ELS 상품설명서 PDF에서 추출한 조건을 구조화된 형태로 반환합니다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "els_terms": {
                "type": "object",
                "properties": {
                    "underlyings": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "기초자산 이름 배열 (영문 표준명: S&P500, KOSPI200, EUROSTOXX50 등)",
                    },
                    "coupon_annual": {"type": "number", "description": "연 쿠폰 소수 (8% → 0.08)"},
                    "maturity_months": {"type": "integer", "description": "만기 개월 수"},
                    "check_interval_months": {"type": "integer", "description": "점검 주기 개월 수"},
                    "step_down_barriers": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "모든 관찰일의 배리어를 순서대로 담은 소수 배열 (90% → 0.90). 조기상환 배리어 뒤에 만기 상환 배리어를 마지막 원소로 반드시 포함한다. 배열 길이 = 만기 ÷ 점검주기이며, 마지막 값이 만기 배리어다.",
                    },
                    "vol": {
                        "type": ["array", "null"],
                        "items": {"type": "number"},
                        "description": "기초자산별 내재변동성 소수 배열 (36.29% → 0.3629). 설명서의 '이론가격 산출에 사용한 변동성' 또는 '기초자산 가격 변동성' 표에서 추출한다. 기초자산 순서와 일치. 문서에 없으면 null.",
                    },
                    "knock_in": {
                        "type": ["number", "null"],
                        "description": "낙인선 소수 (50% → 0.50). 낙인 없으면 null",
                    },
                    "principal": {
                        "type": ["integer", "null"],
                        "description": "투자 원금 원 단위. 문서에 명시된 경우만 기입, 없으면 null.",
                    },
                },
                "required": [
                    "underlyings", "coupon_annual", "maturity_months",
                    "check_interval_months", "step_down_barriers", "knock_in",
                ],
            },
            "confidence": {
                "type": "object",
                "properties": {
                    "underlyings": {"type": "number"},
                    "coupon_annual": {"type": "number"},
                    "maturity_months": {"type": "number"},
                    "step_down_barriers": {"type": "number"},
                    "knock_in": {"type": "number"},
                },
                "description": "필드별 추출 신뢰도 (0~1). 불확실할수록 낮게.",
            },
            "warnings": {
                "type": "array",
                "items": {"type": "string"},
                "description": "사람이 확인해야 할 항목 안내 문구 배열. 없으면 빈 배열.",
            },
        },
        "required": ["els_terms", "confidence", "warnings"],
    },
}

_EXTRACT_PROMPT = (
    "첨부된 ELS 상품설명서 PDF를 분석하여 조건을 추출하세요. "
    "문서에 없는 값은 만들지 말고, 불확실한 항목은 confidence를 낮게 설정하고 warnings에 안내 문구를 추가하세요."
)

_NORMALIZED_SUPPORTED = {normalize_underlying(u) for u in SUPPORTED_UNDERLYINGS}


def _sanity_check(terms: dict, warnings: list[str]) -> None:
    """추출 수치 범위 및 정합성 검증. 이상 항목은 warnings에 추가."""
    if not (0 < terms.get("coupon_annual", 0) < 1):
        warnings.append("연 쿠폰율 값을 확인해 주세요 (소수 형식 0~1 범위를 벗어남).")
    for b in terms.get("step_down_barriers", []):
        if not (0 < b <= 1.5):
            warnings.append("조기상환 배리어 값을 확인해 주세요 (소수 형식 0~1.5 범위를 벗어남).")
            break
    vol = terms.get("vol")
    if vol is not None:
        if len(vol) != len(terms.get("underlyings", [])):
            warnings.append("추출된 변동성 개수가 기초자산 개수와 다릅니다. 확인해 주세요.")
        elif any(not (0 < v <= 3.0) for v in vol):
            warnings.append("추출된 변동성 값이 유효 범위를 벗어났습니다. 확인해 주세요.")
    ki = terms.get("knock_in")
    if ki is not None and not (0 < ki < 1):
        warnings.append("낙인선 값을 확인해 주세요 (소수 형식 0~1 범위를 벗어남).")
    maturity = terms.get("maturity_months", 0)
    interval = terms.get("check_interval_months", 0)
    if maturity <= 0 or interval <= 0:
        warnings.append("만기·점검주기 값을 확인해 주세요.")
    else:
        expected = maturity // interval
        actual = len(terms.get("step_down_barriers", []))
        if actual != expected:
            warnings.append(
                f"조기상환 배리어 개수({actual}개)가 점검 횟수({expected}회)와 맞지 않습니다."
            )
    unknown = [
        u for u in terms.get("underlyings", [])
        if normalize_underlying(u) not in _NORMALIZED_SUPPORTED
    ]
    if unknown:
        warnings.append(f"인식되지 않은 기초자산이 있습니다. 확인해 주세요: {', '.join(unknown)}")


def extract_from_pdf(pdf_bytes: bytes) -> dict:
    """PDF에서 ELS 조건 추출. 실패 시 ExtractionError 발생."""
    try:
        b64 = base64.standard_b64encode(pdf_bytes).decode()
        msg = _get_client().messages.create(
            model=MODEL,
            max_tokens=1024,
            timeout=30.0,
            system=_EXTRACT_SYSTEM,
            tools=[_EXTRACT_TOOL],
            tool_choice={"type": "tool", "name": "extract_els_terms"},
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
                    },
                    {"type": "text", "text": _EXTRACT_PROMPT},
                ],
            }],
        )
        tool_block = next(b for b in msg.content if b.type == "tool_use")
        result = tool_block.input
    except (anthropic.APIError, TypeError) as e:
        raise ExtractionError(str(e)) from e
    except StopIteration as e:
        raise ExtractionError("응답에서 추출 데이터를 찾을 수 없습니다.") from e

    try:
        confidence = result.get("confidence")
        result["confidence"] = confidence if isinstance(confidence, dict) else {}
        warnings = result.get("warnings")
        result["warnings"] = warnings if isinstance(warnings, list) else []

        els_terms = result.get("els_terms")
        if not isinstance(els_terms, dict):
            raise ExtractionError("ELS 조건을 추출할 수 없는 문서입니다.")
        # Claude가 confidence/warnings를 els_terms 안에 중첩 반환하는 경우 정규화
        if "els_terms" in els_terms:
            result["confidence"] = els_terms.get("confidence", result["confidence"])
            result["warnings"] = els_terms.get("warnings", result["warnings"])
            els_terms = els_terms["els_terms"]
            result["els_terms"] = els_terms
        if not isinstance(els_terms, dict):
            raise ExtractionError("ELS 조건을 추출할 수 없는 문서입니다.")
        els_terms.setdefault("principal", None)

        _sanity_check(els_terms, result["warnings"])
    except ExtractionError:
        raise
    except Exception as e:
        raise ExtractionError("ELS 조건을 추출할 수 없는 문서입니다.") from e

    return result
