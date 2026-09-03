"""요청/응답 모델 + 입력 검증 (명세 0-6 규칙)"""
from pydantic import BaseModel, field_validator, model_validator


class ElsTerms(BaseModel):
    underlyings: list[str]
    coupon_annual: float
    maturity_months: int
    check_interval_months: int
    step_down_barriers: list[float]
    knock_in: float | None = None
    principal: int = 10_000_000

    # 기초자산: 1~3개, 빈 문자열 불가
    @field_validator("underlyings")
    @classmethod
    def _check_underlyings(cls, v):
        if not (1 <= len(v) <= 3):
            raise ValueError("기초자산은 1~3개여야 합니다.")
        if any(not s.strip() for s in v):
            raise ValueError("기초자산 이름이 비어 있습니다.")
        return v

    # 쿠폰: 0 ~ 0.5
    @field_validator("coupon_annual")
    @classmethod
    def _check_coupon(cls, v):
        if not (0 <= v <= 0.5):
            raise ValueError("쿠폰은 0~50% 사이여야 합니다.")
        return v

    # 만기: 6~60, 6의 배수
    @field_validator("maturity_months")
    @classmethod
    def _check_maturity(cls, v):
        if not (6 <= v <= 60) or v % 6 != 0:
            raise ValueError("만기는 6개월 단위(6~60)여야 합니다.")
        return v

    # 점검주기: 1~12
    @field_validator("check_interval_months")
    @classmethod
    def _check_interval(cls, v):
        if not (1 <= v <= 12):
            raise ValueError("점검 주기는 1~12개월이어야 합니다.")
        return v

    # 낙인선: null 또는 0.3~1.0
    @field_validator("knock_in")
    @classmethod
    def _check_knock_in(cls, v):
        if v is not None and not (0.3 <= v <= 1.0):
            raise ValueError("낙인선은 30~100% 사이여야 합니다.")
        return v

    # 원금: 1만 ~ 100억
    @field_validator("principal")
    @classmethod
    def _check_principal(cls, v):
        if not (10_000 <= v <= 10_000_000_000):
            raise ValueError("투자금액을 확인해 주세요.")
        return v

    # 필드 여러 개를 같이 봐야 하는 규칙(서로 관계 있는 것)
    @model_validator(mode="after")
    def _check_cross(self):
        # 점검주기가 만기의 약수인가
        if self.maturity_months % self.check_interval_months != 0:
            raise ValueError("점검 주기가 만기와 맞지 않습니다.")
        # 배리어 개수 = 만기 ÷ 점검주기
        nobs = self.maturity_months // self.check_interval_months
        if len(self.step_down_barriers) != nobs:
            raise ValueError("배리어 개수가 점검 횟수와 다릅니다.")
        # 각 배리어 0.3 ~ 1.2
        if any(not (0.3 <= b <= 1.2) for b in self.step_down_barriers):
            raise ValueError("배리어 값은 30~120% 사이여야 합니다.")
        return self


class DiagnoseRequest(BaseModel):
    els_terms: ElsTerms
    overrides: dict | None = None
    num_paths: int | None = None