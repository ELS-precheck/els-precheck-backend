"""요청/응답 모델 + 검증"""
from pydantic import BaseModel


class ElsTerms(BaseModel):
    underlyings: list[str]
    coupon_annual: float
    maturity_months: int
    check_interval_months: int
    step_down_barriers: list[float]
    knock_in: float | None = None
    principal: int = 10_000_000


class DiagnoseRequest(BaseModel):
    els_terms: ElsTerms
    overrides: dict | None = None
    num_paths: int | None = None