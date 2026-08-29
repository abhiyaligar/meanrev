"""
Alpaca router - Single endpoint /get_account for Meanrev
Uses .env vars: ALPACA_API_URL, ALPACA_API_KEY, ALPACA_API_SECRET
Docs: https://alpaca.markets/sdks/python/getting_started.html
"""

from fastapi import APIRouter, HTTPException
from ..alpaca_client import create_trading_client, AlpacaConnectionError

router = APIRouter()

@router.get("/get_account")
def get_account():
    try:
        client = create_trading_client()
        acct = client.get_account()
        data = acct.model_dump() if hasattr(acct, "model_dump") else acct.dict()  # type: ignore
        return {"connected": True, "account": data}
    except AlpacaConnectionError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail={"error": str(e), "type": type(e).__name__})
