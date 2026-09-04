"""Claude API 연동 — 해설 생성"""
from __future__ import annotations
import json
import logging
import os

import anthropic

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
