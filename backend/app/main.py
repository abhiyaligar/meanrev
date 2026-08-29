from fastapi import FastAPI

from .routers.alpaca import router as alpaca_router
from .routers.broker import router as broker_router

app = FastAPI(
    title="Meanrev Alpaca API",
    version="0.1.0",
    description="Autonomous AI Trading Agent — broker read surface (paper trading, throttled). See DOC.md §1: CLI-only in v1, no UI.",
)

# Legacy single endpoint (deprecated, kept for backward compat)
app.include_router(alpaca_router)
# Broker read surface — /api/v1/*
app.include_router(broker_router)


@app.get("/", tags=["system"])
def root():
    return {"name": app.title, "docs": "/docs"}


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "version": app.version}
