"""출력 검사 테스트 — 실행: python -m pytest tests -q"""
import copy

from app.output_check import (
    DEFAULT_DISCLAIMER, FALLBACK_EXPLANATION, FALLBACK_SUMMARY,
    check_explanation, find_unsupported_number, build_allowed_numbers,
)

# 포트폴리오 5쪽 예시 상품 (KOSPI200, 쿠폰 11.5%, 3년, 6개월 평가, 낙인 35%)
ELS_TERMS = {
    "underlyings": ["KOSPI200"],
    "coupon_annual": 0.115,
    "maturity_months": 36,
    "check_interval_months": 6,
    "step_down_barriers": [0.85, 0.85, 0.85, 0.80, 0.75, 0.70],
    "knock_in": 0.35,
    "principal": 10_000_000,
    "vol": None,
    "corr": None,
}
DIAGNOSIS = {
    "loss_probability": 0.0602,
    "grade": "중위험",
    "expected_return": 0.0525,
    "promised_coupon_annual": 0.115,
    "cvar_95": -0.6974,
    "early_redemption_probability": 0.8746,
    "outcome_split": {"early": 0.8746, "maturity": 0.0651, "loss": 0.0602},
    "early_redemption_by_step": [
        {"month": 6, "prob": 0.7159}, {"month": 12, "prob": 0.0812},
        {"month": 18, "prob": 0.0401}, {"month": 24, "prob": 0.0215},
        {"month": 30, "prob": 0.0159}, {"month": 36, "prob": 0.0702},
    ],
    "return_distribution": {"bins": [-0.6, -0.55, 0.3], "counts": [120, 80, 3000]},
    "principal": 10_000_000,
    "expected_return_amount": 525_000,
    "meta": {"num_paths": 100000, "compute_ms": 812, "steps": 156},
}
USER_DESC = "40~50대 중립형"

# 포트폴리오 5쪽에 실린 실제 해설 문장
SLIDE_EXPLANATION = [
    "이 상품은 KOSPI200 지수를 기초자산으로 하며, 연 11.5%의 쿠폰(수익률)을 제시하고 있습니다.",
    "6개월마다 조기상환 평가가 이루어지며, 첫 평가일(6개월 시점)에 조기상환될 확률이 71.59%로 가장 높게 나타나 전체 조기상환 확률은 87.46%에 달합니다.",
    "반면 만기까지 상환되지 않고 지수가 지속 하락해 손실이 발생할 확률은 6.02%로 계산되었으며, 만기에 원금 손실 없이 상환될 확률은 6.51%입니다.",
    "쿠폰으로 제시된 연 11.5%는 조기상환 또는 만기상환 조건을 충족했을 때 받는 수익률이며, 실제 시뮬레이션 기준 기대수익률은 연 5.25%로 쿠폰보다 낮게 산출되어 손실 가능성이 기대수익률에 반영되어 있음을 보여줍니다.",
    "손실이 발생하는 구간에서는 원금 손실 폭이 매우 커질 수 있어, 손실 시나리오만 따로 떼어 평균적인 손실 정도를 본 CVaR(95%) 값은 -69.74%로 나타났습니다.",
]


def _response(**kw):
    base = {
        "summary_line": "이 ELS는 손실 확률이 약 6.02%로 산출되어 중위험 등급으로 분류되며, 중립형 투자자가 감내 가능한 수준인지 점검이 필요합니다.",
        "explanation": list(SLIDE_EXPLANATION),
        "cautions": ["원금 손실 시 손실 폭이 평균 약 70%에 이를 수 있습니다."],
        "disclaimer": "스텝다운형 ELS 전용 진단입니다. 본 해설은 투자권유가 아니라 정보 제공입니다.",
    }
    base.update(kw)
    return base


def _check(resp):
    return check_explanation(copy.deepcopy(resp), ELS_TERMS, DIAGNOSIS, USER_DESC)


def test_slide_example_passes_unchanged():
    resp = _response()
    out = _check(resp)
    assert out["summary_line"] == resp["summary_line"]
    assert out["explanation"] == " ".join(SLIDE_EXPLANATION)
    assert out["cautions"] == resp["cautions"]
    assert out["disclaimer"] == resp["disclaimer"]


def test_rounded_values_are_allowed():
    allowed = build_allowed_numbers(ELS_TERMS, DIAGNOSIS, USER_DESC)
    for text in ["손실 확률은 약 6%입니다.", "기대수익률은 연 5.3%입니다.",
                 "만기는 3년입니다.", "36개월 동안 6회 평가합니다.",
                 "1,000만 원 투자 시 기대수익은 약 52.5만 원입니다.",
                 "기대수익은 525,000원입니다.", "40~50대 중립형 투자자"]:
        assert find_unsupported_number(text, allowed) is None, text


def test_invented_numbers_are_detected():
    allowed = build_allowed_numbers(ELS_TERMS, DIAGNOSIS, USER_DESC)
    for text in ["손실 확률은 약 12.3%입니다.", "조기상환 확률은 90%입니다.",
                 "기대수익은 약 80만 원입니다.", "평균 손실은 -55.1%입니다."]:
        assert find_unsupported_number(text, allowed) is not None, text


def test_sentence_with_invented_number_is_dropped():
    bad = "과거 유사 상품의 평균 손실률은 42.7%였습니다."
    out = _check(_response(explanation=SLIDE_EXPLANATION[:2] + [bad]))
    assert bad not in out["explanation"]
    assert SLIDE_EXPLANATION[0] in out["explanation"]


def test_histogram_and_meta_are_not_citable():
    # 분포 막대 개수(3000)나 실행 시간(812)은 해설 인용 대상이 아님
    out = _check(_response(cautions=["3000개 경로에서 손실이 났습니다.", "계산에 812ms 걸렸습니다."]))
    assert out["cautions"] == []


def test_summary_with_invented_number_falls_back():
    out = _check(_response(summary_line="손실 확률은 약 15%로 높은 편입니다."))
    assert out["summary_line"] == FALLBACK_SUMMARY


def test_prohibited_expression_still_dropped():
    bad = "지금이 적기이니 가입하세요."
    out = _check(_response(explanation=[SLIDE_EXPLANATION[0], bad]))
    assert bad not in out["explanation"]


def test_all_sentences_dropped_falls_back():
    out = _check(_response(explanation=["손실 확률은 33.3%입니다.", "투자하세요."]))
    assert out["explanation"] == FALLBACK_EXPLANATION


def test_disclaimer_missing_or_empty_gets_default():
    resp = _response()
    del resp["disclaimer"]
    assert _check(resp)["disclaimer"] == DEFAULT_DISCLAIMER
    assert _check(_response(disclaimer="  "))["disclaimer"] == DEFAULT_DISCLAIMER
    assert _check(_response(disclaimer="이 상품을 추천합니다."))["disclaimer"] == DEFAULT_DISCLAIMER


def test_percent_must_match_percent_values():
    allowed = build_allowed_numbers(ELS_TERMS, DIAGNOSIS, USER_DESC)
    # 0.85(배리어)나 회차 1을 "1%"로, 0.0602를 "0%"로 반올림한 것처럼 통과하면 안 됨
    for text in ["손실 확률은 1%입니다.", "손실 확률은 0%입니다."]:
        assert find_unsupported_number(text, allowed) is not None, text
    for text in ["배리어는 85%에서 70%까지 낮아집니다.", "낙인 35%", "하위 5% 구간",
                 "원금의 100%", "CVaR(95%)", "코스피200 지수", "0.5년"]:
        assert find_unsupported_number(text, allowed) is None, text
