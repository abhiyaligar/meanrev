from alpaca.trading.client import TradingClient
from .config import get_settings

class AlpacaConnectionError(Exception):
    pass

def create_trading_client():
    s = get_settings()
    key = s.get_key()
    secret = s.get_secret()
    if not key or not secret:
        raise AlpacaConnectionError("Set ALPACA_API_KEY and ALPACA_API_SECRET in backend/.env")
    url = s.alpaca_api_url.rstrip("/")
    if url.endswith("/v2"):
        url = url[:-3]
    # url_override needed to honor user's ALPACA_API_URL, paper=True for hackathon
    return TradingClient(api_key=key, secret_key=secret, paper=True, url_override=url)
