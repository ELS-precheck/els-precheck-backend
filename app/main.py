"""ELS 프리체크 백엔드 (FastAPI) — 스켈레톤"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="ELS 프리체크 API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.get("/")
def root():
    return {"ok": True, "data": {"service": "ELS 프리체크 API", "status": "running"}}


@app.get("/api/presets")
def presets():
    return {"ok": True, "data": {"presets": []}}


@app.post("/api/diagnose")
def diagnose(body: dict):
    return {"ok": True, "data": {"loss_probability": 0.0, "grade": "중위험"}}