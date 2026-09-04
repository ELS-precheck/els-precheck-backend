"""ELS 프리체크 백엔드 (FastAPI)"""
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, File, Request, UploadFile
from starlette.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError

from app.ai import ExtractionError, LLMError, extract_from_pdf, generate_explanation
from app.engine import run_simulation
from app.market_data import get_vol, get_corr
from app.models import DiagnoseRequest, ExplainRequest
from app.presets import get_presets

app = FastAPI(title="ELS 프리체크 API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "https://els-precheck-frontend.vercel.app",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(RequestValidationError)
def on_invalid(request: Request, exc: RequestValidationError):
    err = exc.errors()[0]
    field = err["loc"][-1] if err.get("loc") else None
    return fail("INVALID_INPUT", _to_korean(err), str(field), status=400)


# ---------- 공통 봉투 헬퍼 ----------
def ok(data: dict) -> dict:
    return {"ok": True, "data": data}


def fail(code: str, message: str, field=None, status: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"ok": False, "error": {"code": code, "message": message, "field": field}},
    )

# ---------- 검증 오류 ----------
_FIELD_MSG = {
    "num_paths": "경로 수는 1,000에서 200,000 사이여야 합니다.",
    "volatility_scale": "변동성 배수 값이 올바르지 않습니다.",
    "knock_in": "낙인선은 30~100% 사이여야 합니다.",
}


def _to_korean(err: dict) -> str:
    etype = err.get("type", "")
    field = err["loc"][-1] if err.get("loc") else None
    # 우리가 만든 검증(ValueError)은 이미 한국어 → "Value error, " 접두어만 제거
    if etype == "value_error":
        return err.get("msg", "").replace("Value error, ", "", 1)
    if etype == "extra_forbidden":
        return "허용되지 않는 항목이 포함되어 있습니다."
    if etype == "missing":
        return "필수 항목이 빠졌습니다."
    if field in _FIELD_MSG:
        return _FIELD_MSG[field]
    return "입력값을 확인해 주세요."


# ---------- 전역 예외 핸들러: 어떤 에러든 봉투로 ----------
@app.exception_handler(Exception)
def on_error(request: Request, exc: Exception):
    return fail("INTERNAL",
                "문제가 발생했어요. 계속되면 잠시 후 다시 시도해 주세요.",
                None, status=500)


@app.get("/api/health")
def health():
    return ok({"status": "healthy"})


@app.get("/api/presets")
def presets():
    return ok({"presets": get_presets()})


@app.post("/api/extract")
async def extract(file: UploadFile = File(...)):
    if file.content_type != "application/pdf" and not (file.filename or "").endswith(".pdf"):
        return fail("UNSUPPORTED_FILE", "PDF 파일만 업로드할 수 있어요.", "file", status=415)

    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(65536):
        size += len(chunk)
        if size > 10 * 1024 * 1024:
            return fail("FILE_TOO_LARGE", "10MB 이하 PDF만 올릴 수 있어요.", "file", status=413)
        chunks.append(chunk)
    content = b"".join(chunks)

    if not content.startswith(b"%PDF-"):
        return fail("UNSUPPORTED_FILE", "유효한 PDF 파일이 아닙니다.", "file", status=415)

    try:
        result = await run_in_threadpool(extract_from_pdf, content)
    except ExtractionError:
        return fail("EXTRACTION_FAILED",
                    "설명서에서 조건을 읽지 못했어요. 직접 입력으로 진행해 주세요.",
                    None, status=422)
    return ok(result)


@app.post("/api/explain")
def explain(req: ExplainRequest):
    profile = req.user_profile.model_dump() if req.user_profile else None
    try:
        result = generate_explanation(
            els_terms=req.els_terms.model_dump(),
            diagnosis=req.diagnosis.model_dump(),
            user_profile=profile,
        )
    except LLMError:
        return fail("LLM_UNAVAILABLE",
                    "해설을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.",
                    None, status=503)
    return ok(result)


@app.post("/api/diagnose")
def diagnose(req: DiagnoseRequest):
    t = req.els_terms

    # 1) 이 기초자산들의 변동성·상관 구하기
    vol = get_vol(t.underlyings)
    corr = get_corr(t.underlyings)

    # 2) overrides(조건 바꿔보기) 반영  ← 여기가 바뀐 부분
    ov = req.overrides
    scale = ov.volatility_scale if ov else 1.0
    vol = [v * scale for v in vol]
    knock_in = ov.knock_in if (ov and ov.knock_in is not None) else t.knock_in
    num_paths = req.num_paths or 100000

    # 3) 엔진 호출
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