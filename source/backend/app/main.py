"""FastAPI entry point.  Run:  uvicorn app.main:app --reload --port 8000"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Utility Bill Decoder API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/")
def root():
    return {
        "message": "Utility Bill Decoder API is running",
        "docs": "http://localhost:8000/docs",
        "frontend": "http://localhost:3000",
        "health": "http://localhost:8000/api/health",
    }



def _openapi_with_file_pickers() -> dict:
    """Swagger UI shows a text box for list[UploadFile] under OpenAPI 3.1; mark items as binary files."""
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    for component in schema.get("components", {}).get("schemas", {}).values():
        for prop in component.get("properties", {}).values():
            if prop.get("contentMediaType") == "application/octet-stream":
                prop.pop("contentMediaType")
                prop["format"] = "binary"
            items = prop.get("items", {})
            if items.get("type") == "string" and items.get("contentMediaType") == "application/octet-stream":
                prop["items"] = {"type": "string", "format": "binary"}
    app.openapi_schema = schema
    return schema


app.openapi = _openapi_with_file_pickers
