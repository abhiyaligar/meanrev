# Meanrev Backend – Alpaca Hackathon Simple API

Simple FastAPI that **creates a connection** to Alpaca Trading API (paper).

Built for **Alpaca AI Trading Agents Hackathon 2026** (lablab.ai, 28 Aug–4 Sept 2026, $6,300).

## Stack

- Python 3.12, FastAPI 0.141.1, Uvicorn
- `alpaca-py==0.44.0` (`TradingClient`)
- `pydantic-settings`, `python-dotenv`

## Env (your exact 3 vars)

```
ALPACA_API_URL=https://paper-api.alpaca.markets/v2
ALPACA_API_KEY=PKM42NNKIXODL6X4BCQAXD7CY6
ALPACA_API_SECRET=FevbXE3jBFBFUqHwa173JNv3q82ksARzK48tnipqKcvx
```
Paper account verified LIVE: `PA3WKUKN51YI` | $100k | options_level 3.

## Setup

```bash
cp backend/.env.example backend/.env
# edit backend/.env: set your 3 ALPACA vars (paper keys)
pip install -r requirements.txt
```

Get **paper** keys: https://app.alpaca.markets/paper/dashboard/overview → Generate new keys (Paper).

## Run

```bash
# from repo root (recommended)
uvicorn backend.app.main:app --reload --port 8000

# or from backend folder
cd backend
uvicorn app.main:app --reload --port 8000
```

Open:
- Swagger: http://localhost:8000/docs
- Endpoint: http://localhost:8000/get_account

## Endpoint

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/get_account` | **Creates `TradingClient(api_key, secret_key, paper=True)` from your 3 .env vars and calls `get_account()`** |

### Example

```bash
curl http://localhost:8000/get_account

# 200:
# {"connected": true, "account": {"id":"4a916b34...","account_number":"PA3WKUKN51YI","status":"ACTIVE","cash":"100000","portfolio_value":"100000","options_approved_level":3, ...}}
# 401/502 if keys invalid
```

## How it works (alpaca-py)

```python
from alpaca.trading.client import TradingClient

# uses your .env: ALPACA_API_URL, ALPACA_API_KEY, ALPACA_API_SECRET, paper=True
client = TradingClient(api_key=ALPACA_API_KEY, secret_key=ALPACA_API_SECRET, paper=True)
acct = client.get_account()
```

Ref: https://alpaca.markets/sdks/python/getting_started.html#api-keys

## Project structure

```
backend/
  .env                  # your 3 vars (gitignored)
  .env.example          # template
  cli/
    __main__.py         # REPL entrypoint
    repl.py             # prompt_toolkit loop, command routing
    commands.py         # /status, /report, /pause handlers
  agents/
    research.py         # Market Research — Claude 3.5 Sonnet
    strategy.py         # Strategy — GPT-4o
    risk.py             # Risk Management — deterministic rules (v1)
    execution.py        # Execution — throttled Alpaca client
    reporting.py        # Reporting — log → summary
  graph/
    state.py            # shared LangGraph state schema (pydantic)
    build.py            # wires agents/ into the graph
  broker/
    client.py           # thin alpaca-py wrapper
    rate_limit.py       # token bucket / backoff
  data/
    market.py           # OHLCV + indicators (pandas-ta)
    news.py             # sentiment/news fetching
  core/
    config.py           # pydantic-settings, reads .env
    logging.py          # structured JSON-line logger
    models.py           # shared pydantic models (TradeDecision, RiskVerdict)
  logs/                 # gitignored, JSON-line output (.gitkeep)
  app/                  # legacy FastAPI — explicitly removed in v1 per DOC.md §2, kept for reference
    main.py
    config.py
    alpaca_client.py
    routers/alpaca.py
```
