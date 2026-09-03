"""ELS 프리체크 백엔드 (FastAPI)"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError

from app.engine import run_simulation
from app.market_data import get_vol, get_corr
from app.models import DiagnoseRequest
from app.presets import get_presets

app = FastAPI(title="ELS 프리체크 API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

@app.exception_handler(RequestValidationError)
def on_invalid(request: Request, exc: RequestValidationError):
    err = exc.errors()[0]                      # 첫 번째 에러만 사용
    msg = err.get("msg", "입력값을 확인해 주세요.")
    field = err["loc"][-1] if err.get("loc") else None
    return fail("INVALID_INPUT", str(msg), str(field), status=400)


# ---------- 공통 봉투 헬퍼 ----------
def ok(data: dict) -> dict:
    return {"ok": True, "data": data}


def fail(code: str, message: str, field=None, status: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"ok": False, "error": {"code": code, "message": message, "field": field}},
    )


# ---------- 전역 예외 핸들러: 어떤 에러든 봉투로 ----------
@app.exception_handler(Exception)
def on_error(request: Request, exc: Exception):
    return fail("INTERNAL",
                "문제가 발생했어요. 계속되면 잠시 후 다시 시도해 주세요.",
                None, status=500)


@app.get("/")
def root():
    return ok({"service": "ELS 프리체크 API", "status": "running"})


@app.get("/api/presets")
def presets():
    return ok({"presets": get_presets()})


@app.post("/api/diagnose")
def diagnose(req: DiagnoseRequest):
    t = req.els_terms
    ov = req.overrides or {}

    # 1) 이 기초자산들의 변동성·상관 구하기
    vol = get_vol(t.underlyings)
    corr = get_corr(t.underlyings)

    # 2) '조건 바꿔보기' 슬라이더: 변동성 배수 반영
    scale = ov.get("volatility_scale", 1.0)
    vol = [v * scale for v in vol]

    # 3) 낙인 슬라이더가 왔으면 덮어쓰기
    knock_in = ov.get("knock_in", t.knock_in)

    # 4) 경로 수: 안 주면 10만
    num_paths = req.num_paths or 100000

    # 5) 엔진 호출
    result = run_simulation(
        underlyings=t.underlyings,
        coupon_annual=t.coupon_annual,
        maturity_months=t.maturity_months,
        check_interval_months=t.check_interval_months,
        step_down_barriers=t.step_down_barriers,
        knock_in=knock_in,
        principal=t.principal,
        vol=vol,
        corr=corr,
        num_paths=num_paths,
    )
    return ok(result)