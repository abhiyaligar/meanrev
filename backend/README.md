# Meanrev Backend – Alpaca Hackathon Simple API

Simple FastAPI that **creates a connection** to Alpaca Trading API (paper).

Built for **Alpaca AI Trading Agents Hackathon 2026** (lablab.ai, 28 Aug–4 Sept 2026, $6,300).

## Stack

- Python 3.12, FastAPI 0.141.1, Uvicorn
- `alpaca-py==0.44.0` (`TradingClient`)
- `pydantic-settings`, `python-dotenv`

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
# {"connected": true, "account": {"id":"4a916b34...","account_number":"PA**********","status":"ACTIVE","cash":"100000","portfolio_value":"100000","options_approved_level":3, ...}}
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
  .env                  # your vars (gitignored) — Alpaca + LLM + MCP (see .env.example)
  .env.example          # template (ALPACA_API_*, LLM_PROVIDER, LLM_MODEL_*, RISK_MAX_*, EXECUTION_MODE, MCP_SERVER_URL/COMMAND)
  cli/
    __main__.py         # REPL entrypoint (meanrev via pyproject.toml)
    repl.py             # prompt_toolkit loop, rich.live streaming research→strategy→risk→execution, instruction hook
    commands.py         # /status, /positions, /report, /pause, /resume handlers
  agents/
    research.py         # Market Research — Claude 3.5 Sonnet, _RESEARCH_TOOLS 9 (3 news + 3 alpaca_cli + 3 mcp) Phase 12
    strategy.py         # Strategy — GPT-4o, ensure_options_in_decision, compute_sizing ATR, token <1000, no-mock fallback
    risk.py             # Risk Management — deterministic rules (v1) 15%/60%/3% + logs/.paused breaker + SPXW
    execution.py        # Execution — throttled Alpaca client, auto vs hitl interrupt
    reporting.py        # Reporting — log → summary 5-section + export reports/report.md
  graph/
    state.py            # shared LangGraph state schema (pydantic, dict compat)
    build.py            # wires agents/ into StateGraph (research→strategy→risk→execution) + InMemorySaver + stub fallback
  broker/
    client.py           # thin alpaca-py wrapper (get_account/positions/orders/clock/submit_order market/limit/stop/options+crypto, 25/min + tenacity)
    rate_limit.py       # token bucket / backoff (Redis Lua or InMemory)
  data/
    market.py           # OHLCV + indicators (pandas-ta) no-mock (empty → No data available...), VWAP 1m/5m/1h/1d, fetch_option_chain Greeks, BTC/ETH derived
    news.py             # sentiment/news fetching no-mock (empty → No data available...)
  tools/                # LangChain @tool — 25 total (12b: 21→25 with order management)
    broker_tools.py     # get_account, get_positions, get_orders, get_clock, submit_order, set_stop_loss, modify_order, cancel_order, cancel_all_orders (9, 5 HITL)
    market_tools.py     # get_ohlcv, get_market_snapshot, get_option_chain, align_timeframes_tool, detect_arbitrage (5)
    news_tools.py       # fetch_news, get_macro_calendar, extract_keywords (3)
    alpaca_cli_tool.py  # Phase 12: alpaca_cli_account/positions/orders/clock via subprocess "alpaca --json" + broker fallback (4)
    mcp_tools.py        # Phase 12: mcp_get_account/positions/orders/clock via MCP bridge + broker fallback (4)
    __init__.py         # TOOLS (25), BROKER_TOOLS, BROKER_WRITE_TOOLS (5), ALPACA_CLI_TOOLS, MCP_TOOLS exports
  mcp/                  # Phase 12 MCP bridge
    client.py           # is_mcp_configured, get_mcp_tools, aget_mcp_tools, mcp_server_info
    server_config.example.json  # {mcpServers: {alpaca: {command: "npx @alpacahq/alpaca-mcp-server"}}}
    __init__.py
  scheduler/            # Phase 12b autonomous loop
    runner.py           # tick() guard is_open else skip + build_graph invoke + scheduler_tick log; run_scheduler() APScheduler IntervalTrigger 5min jitter30 coalesce misfire300 or asyncio fallback; --scheduler/--once
    market_hours.py     # is_market_open() via get_clock() TTL 60s, seconds_until_next_open()
    state.py            # logs/scheduler.json {last_run, next_run, run_count, thread_id} atomic
    __init__.py         # re-exports tick, run_scheduler
  core/
    config.py           # pydantic-settings, reads .env — LLM_PROVIDER, LLM_MODEL_* compulsory, RISK_MAX_*, EXECUTION_MODE/HITL_ENABLED, MCP_SERVER_URL/COMMAND, SCHEDULER_* (ENABLED/INTERVAL/THREAD/PROMPT)
    logging.py          # structured JSON-line logger (_redact, log_event default=str → logs/broker.jsonl + scheduler_tick/skip)
    models.py           # shared pydantic models (TradeDecision, RiskVerdict)
    system_prompt.py    # central SYSTEM_PROMPT registry get_system_prompt(agent)
    utils.py            # get_model_id, handle_tool_errors, normalize_symbol, clamp_limit, count_tokens, TTLCache
  logs/                 # gitignored, JSON-line output + scheduler.json {last_run/next_run} + .paused breaker flag
  app/                  # legacy FastAPI — kept for /api/v1 broker surface (active, not removed)
    main.py             # FastAPI with /api/v1/account/positions/orders/clock + /health
    routers/broker.py   # 4 GETs under /api/v1
```

**Phase 12 tools wired:** `research` now uses 9 tools (news + CLI + MCP); all 25 tools respect `25/min` + `30s` (CLI `8s`) and return `No data available...` on empty (never mock). Scheduler `meanrev --scheduler` ticks every `SCHEDULER_INTERVAL_MIN` when `09:30-16:00 ET` open, else `scheduler_skip_closed`. See `docs/Agent_Architecture.md §11` + `docs/Backend_Architecture.md §4/5` + `docs/API_REFERENCE.md §7/8` + `docs/How_To_use.md §2/6`.
