"""HTTP routes.

GET  /api/health          model + status
POST /api/extract         one bill image  -> Level 1 JSON (+ extras, checks, insights)
POST /api/level1/batch    many bill images -> Level 1 JSON per bill + ready-to-submit level1.csv text
POST /api/ask             questions about an extracted bill (Level 2)
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.services.csv_io import level1_cell, to_csv_text
from app.services.metrics import compute_metrics
from app.services.pipeline import IMAGE_EXTS, BillPipeline, BillResult

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_BATCH_FILES = 20

router = APIRouter()
_bills: dict[str, tuple[BillResult, bytes]] = {}  # token -> (extraction result, image bytes)


@lru_cache
def get_pipeline() -> BillPipeline:
    return BillPipeline(get_settings())


def _pipeline() -> BillPipeline:
    try:
        return get_pipeline()
    except RuntimeError as err:  # missing key / disallowed model
        raise HTTPException(500, str(err))


async def _read_image(file: UploadFile) -> tuple[str, bytes]:
    name = Path(file.filename or "bill.png")
    if name.suffix.lower() not in IMAGE_EXTS:
        raise HTTPException(400, f"{name.name}: please upload a bill image ({', '.join(sorted(IMAGE_EXTS))}).")
    data = await file.read()
    if not data:
        raise HTTPException(400, f"{name.name}: the file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"{name.name}: image is larger than 15 MB.")
    return name.stem, data


def _store(result: BillResult, data: bytes) -> str:
    token = hashlib.sha256(data).hexdigest()[:16]
    _bills[token] = (result, data)
    return token


class AskRequest(BaseModel):
    token: str
    questions: list[str] = Field(min_length=1, max_length=20)


@router.get("/health")
def health():
    s = get_settings()
    return {"status": "ok", "level1": "local OCR (RapidOCR)", "model": s.model_name,
            "fallback_model": s.fallback_model if s.anthropic_api_key else None}


@router.post("/extract")
async def extract(file: UploadFile = File(...)):
    bill_id, data = await _read_image(file)
    result = _pipeline().extract(bill_id, data)
    if result.error:
        raise HTTPException(502, f"Could not read the bill: {result.error}")
    return {"token": _store(result, data), **result.to_dict(),
            "insights": compute_metrics(result.level1, result.extras)}


@router.post("/level1/batch")
async def level1_batch(files: list[UploadFile] = File(...)):
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(400, f"Upload at most {MAX_BATCH_FILES} bills at a time.")
    images = [await _read_image(f) for f in files]
    pipeline = _pipeline()
    results, rows = [], []
    for bill_id, data in sorted(images):
        res = pipeline.extract(bill_id, data)  # failed bills still get an all-null row
        results.append({"token": _store(res, data), **res.to_dict()})
        rows.append({"bill_id": bill_id, "json": level1_cell(res.level1)})
    return {"results": results, "level1_csv": to_csv_text(["bill_id", "json"], rows),
            "failed": [r["bill_id"] for r in results if r["error"]]}


@router.post("/ask")
def ask(req: AskRequest):
    if req.token not in _bills:
        raise HTTPException(404, "Bill not found. Please upload it again.")
    result, data = _bills[req.token]
    questions = [(f"Q{i + 1}", q.strip()) for i, q in enumerate(req.questions) if q.strip()]
    if not questions:
        raise HTTPException(400, "Ask at least one question.")
    answers = _pipeline().answer(result, questions, data, deterministic=False)  # free-form questions
    return {"answers": [{"question": q, "answer": answers[qid]} for qid, q in questions]}
