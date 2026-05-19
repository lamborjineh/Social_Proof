"""
SocialProof — FastAPI Application Entry Point
Run with:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload

Interactive docs:  http://localhost:8000/docs
Health check:      http://localhost:8000/health
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from config import logger, CORS_ORIGINS
from core.model_registry import ModelRegistry
from database.models import init_mysql_schema
from routers.dashboard   import router as dashboard_router
from routers.mindmap      import router as mindmap_router
from routers.user_mindmap import router as user_mindmap_router
from routers import (
    analyze_router,
    lessons_router,
    quiz_router,
    auth_router,
    admin_router,
)

# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    logger.info("SocialProof API v3.3 starting — running migrations and pre-loading models…")

    try:
        init_mysql_schema()
        logger.info("DB migrations complete.")
    except Exception as e:
        logger.error(f"DB migration error: {e}")

    try:
        ModelRegistry.nlp()
        ModelRegistry.embed()
        logger.info("spaCy + SentenceTransformer loaded.")
    except Exception as e:
        logger.error(f"Model pre-loading error (spaCy/embed): {e}")

    try:
        ModelRegistry.nli()
        logger.info("NLI model pre-loaded.")
    except Exception as e:
        logger.warning(f"NLI pre-warm skipped (will load on first request): {e}")

    try:
        from retrieval.retriever import get_retriever
        get_retriever()
        logger.info("BGE-M3 Retriever pre-loaded.")
    except Exception as e:
        logger.warning(f"Retriever pre-warm skipped (will load on first request): {e}")

    # ── [Workmate 2] Pre-warm EasyOCR so the first image request isn't slow ──
    try:
        from pipeline.image_input import preload_ocr
        preload_ocr()
        logger.info("EasyOCR model pre-loaded.")
    except Exception as e:
        logger.warning(f"OCR pre-warm skipped (will load on first image request): {e}")

    yield
    # ── Shutdown ─────────────────────────────────────────────────────────────
    logger.info("SocialProof API shutting down.")
    from routers.analyze import _executor
    _executor.shutdown(wait=True)
    logger.info("ThreadPoolExecutor shut down.")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title       = "SocialProof NLP Analysis API",
    description = "Media and Information Literacy credibility analysis pipeline",
    version     = "3.3.0",
    lifespan    = lifespan,
)

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title       = app.title,
        version     = app.version,
        description = app.description,
        routes      = app.routes,
    )
    schema.setdefault("components", {})
    schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type":         "http",
            "scheme":       "bearer",
            "bearerFormat": "JWT",
        }
    }
    schema["security"] = [{"BearerAuth": []}]
    app.openapi_schema = schema
    return schema

app.openapi = custom_openapi

# M-1: Narrowed from ["*"] to explicit safe lists
app.add_middleware(
    CORSMiddleware,
    allow_origins     = CORS_ORIGINS,
    allow_credentials = True,
    allow_methods     = ["GET", "POST", "PUT", "DELETE"],
    allow_headers     = ["Authorization", "Content-Type"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(analyze_router)
app.include_router(lessons_router)
app.include_router(quiz_router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(dashboard_router)
app.include_router(mindmap_router)
app.include_router(user_mindmap_router)

# ── Frontend ──────────────────────────────────────────────────────────────────
# IMPORTANT: static mount must come before any catch-all HTML routes,
# otherwise /{page}.html matches /pages/styles.css first and returns 404.
app.mount("/pages", StaticFiles(directory="pages"), name="pages")

# ── [Workmate 1] Serve admin-uploaded media (quiz images/videos uploaded via /admin/upload)
# Use an absolute path anchored to this file so the correct directory is found
# regardless of the working directory uvicorn is launched from.
_assets_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), "assets", "uploads")
os.makedirs(_assets_dir, exist_ok=True)
app.mount("/assets/uploads", StaticFiles(directory=_assets_dir), name="uploads")

@app.get("/")
async def serve_index():
    return FileResponse("pages/index.html")

@app.get("/{page}.html")
async def serve_page(page: str):
    """
    C-5: Sanitise the page parameter before building the file path.
    Only simple identifier names (letters, digits, underscores) are allowed.
    This prevents path traversal attacks like /../../etc/passwd.html.
    """
    safe = os.path.basename(page)
    # Only allow plain identifiers — no slashes, dots, or other traversal chars
    if not safe.replace("-", "_").replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid page name.")
    # admin.html no longer exists — admin panel is embedded in dashboard.html
    if safe == "admin":
        return RedirectResponse(url="/dashboard.html", status_code=301)
    return FileResponse(f"pages/{safe}.html")


# ── Dev entry point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
