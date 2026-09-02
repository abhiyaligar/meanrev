from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers.alpaca import router as alpaca_router
from .routers.broker import router as broker_router
from .routers.scheduler import router as scheduler_router

app = FastAPI(
    title="Meanrev Alpaca API",
    version="0.1.0",
    description="Autonomous AI Trading Agent — broker read surface (paper trading, throttled). See DOC.md §1: CLI-only in v1, no UI.",
)

# CORS for frontend dev (5173 → 8000) — allow preflight OPTIONS from vite
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Legacy single endpoint (deprecated, kept for backward compat)
app.include_router(alpaca_router)
# Broker read surface — /api/v1/*
app.include_router(broker_router)
# Scheduler buffered surface — /api/v1/scheduler/*
app.include_router(scheduler_router)


@app.get("/", tags=["system"])
def root():
    return {"name": app.title, "docs": "/docs"}


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "version": app.version}
