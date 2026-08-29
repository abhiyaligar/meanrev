from fastapi import FastAPI
from .routers.alpaca import router

app = FastAPI(title="Meanrev Alpaca API", version="0.1.0")
app.include_router(router)
