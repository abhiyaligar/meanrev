# API Reference

**Project:** Autonomous AI Trading Agent — Alpaca AI Trading Agents Hackathon (LabLab.ai)  
**Version:** v1 — Broker Read + Execution Surface (auto/hitl)  
**Base URL:** `http://localhost:8000` (local) — all broker endpoints under `/api/v1`  
**Auth:** None on HTTP — server uses `ALPACA_API_KEY` / `ALPACA_API_SECRET` from environment (gitignored, never logged). Paper trading enforced (`paper=True`).  
**Rate Limit:** 25 req/min leaky bucket (Redis-backed Lua + `InMemory` fallback) shared across all `/api/v1` calls + `tenacity` exponential backoff + jitter on 429/5xx/timeout (30s hard timeout).  
**Scoring Window:** Mon Aug 31, 9:30 a.m. ET → Fri Sep 4, 9:30 a.m. ET  
**Last Updated:** Phase 12b — Autonomous scheduler ( `scheduler/runner.py` `IntervalTrigger` when `09:30-16:00 ET` open, `logs/scheduler.json` persistence) + order tools 21→25 + MCP/CLI; prior: Phase 12 MCP/CLI 21

---

## Table of Contents

- [1. Overview](#1-overview)
- [2. Active Endpoints](#2-active-endpoints)
- [3. Schemas](#3-schemas)
  - [3.1 GET /api/v1/account](#31-get-apiv1account--in-and-out)
  - [3.2 GET /api/v1/positions](#32-get-apiv1positions--in-and-out)
  - [3.3 GET /api/v1/orders](#33-get-apiv1orders--in-and-out)
  - [3.4 GET /api/v1/clock](#34-get-apiv1clock--in-and-out)
- [4. Error Catalog](#4-error-catalog)
- [5. Rate Limiting and Retry](#5-rate-limiting-and-retry)
- [6. cURL Examples](#6-curl-examples)
- [7. File Map](#7-file-map)
- [8. Tool Surface (25 LangChain Tools)](#8-tool-surface-langchain--phase-12-additions)
- [8b. Reserved HTTP — Not Implemented in v1](#8b-reserved-http--not-implemented-in-v1)
- [9. Platform Note — Cash-Settled Index Options Settlement Lag](#9-platform-note--cash-settled-index-options-settlement-lag)
- [10. Related Documentation](#10-related-documentation)

---

## 1. Overview

v1 exposes a **read-only broker surface** that proxies Alpaca Trading API through a single throttled wrapper (`backend/broker/client.py`) plus LangChain tool surface (25 tools: 9 broker [4 read + 5 write/manage] + 5 market + 3 news + 4 Alpaca CLI + 4 MCP). No write HTTP endpoints (`POST /orders`, `DELETE /orders`) are active in v1 to keep the scoring window safe — writes go via `broker_tools.submit_order` (HITL-protected) and `execution_agent` (`submit_order` throttled).

All responses are JSON with a `ts` ISO-8601 timestamp. Errors use a shared shape. Every call is logged as a JSON line to `backend/logs/broker.jsonl` (never secrets).

Legacy endpoint `GET /get_account` is retained as a deprecated alias for backward compatibility and will be removed in v2. Use `GET /api/v1/account`.

---

## 2. Active Endpoints

| Method | Path | Description | In Schema | Out Schema | Errors |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GET | `/health` | Liveness check | — | `{"status": "ok", "version": "0.1.0"}` | — |
| GET | `/api/v1/account` | Paper account, buying power, equity | — | `AccountResponse` — see §3.1 | 401, 429, 502 |
| GET | `/api/v1/positions` | Open positions + unrealized P&L | `symbol?: string` query | `PositionsResponse` — see §3.2 | 401, 429, 502 |
| GET | `/api/v1/orders` | Recent orders/fills | `status?: open\|closed\|all (default open)`, `limit?: int 1..500 (default 50)`, `symbols?: string` comma list e.g. `AAPL,SPY` | `OrdersResponse` — see §3.3 | 401, 429, 502 |
| GET | `/api/v1/clock` | Market open/close | — | `ClockResponse` — see §3.4 | 401, 429, 502 |
| GET | `/get_account` | **Deprecated** — alias to `/api/v1/account` | — | `{"connected": true, "account": {...}}` | 401, 502 |

All `GET`s are idempotent. No request body.

---

## 3. Schemas

### 3.1 GET /api/v1/account — In and Out

**In Schema:** None. Reads server-side credentials.

**Out Schema — 200 `AccountResponse`:**
```
{
  "connected": true,
  "account": {
    "id": "4a916b34-...",
    "account_number": "PA**********",
    "status": "ACTIVE",
    "currency": "USD",
    "cash": "100000",
    "portfolio_value": "100000",
    "buying_power": "400000",
    "options_approved_level": 3,
    "options_buying_power": "100000",
    "trading_blocked": false,
    // ... full alpaca-py Account dump
  },
  "ts": "2026-08-29T10:00:00.000Z"
}
```

**Errors:**
- `401 {"detail": "Set ALPACA_API_KEY and ALPACA_API_SECRET in backend/.env"}` — missing credentials
- `429 {"detail": {"error": "Rate limit exceeded. Retry after 2.38s", "type": "RateLimit", "retry_after": 2.38}}` — bucket empty (25/min)
- `502 {"detail": {"error": "...", "type": "APIError"}}` — upstream Alpaca error

---

### 3.2 GET /api/v1/positions — In and Out

**In Schema — Query Params:**
- `symbol?: string` — e.g. `?symbol=AAPL` returns single position or `[]` if none. Omit for all positions.

**Out Schema — 200 `PositionsResponse`:**
```
{
  "count": 1,
  "positions": [
    {
      "symbol": "AAPL",
      "qty": "10",
      "avg_entry_price": "140.0",
      "market_value": "1500.0",
      "cost_basis": "1400.0",
      "unrealized_pl": "100.0",
      "unrealized_plpc": "0.07",
      // ... full alpaca-py Position dump
    }
  ],
  "symbol_filter": "AAPL" | null,
  "ts": "2026-08-29T10:00:00.000Z"
}
```

**Errors:** Same 401/429/502 as above. `404` for missing symbol is normalized to `200 {"count": 0, "positions": []}`.

---

### 3.3 GET /api/v1/orders — In and Out

**In Schema — Query Params:**
- `status?: string` — `open` (default) | `closed` | `all` — maps to `QueryOrderStatus`
- `limit?: int` — 1..500, default 50, clamped
- `symbols?: string` — comma list, e.g. `?symbols=AAPL,SPY` — filtered post-fetch

**Out Schema — 200 `OrdersResponse`:**
```
{
  "count": 1,
  "orders": [
    {
      "id": "ord-1",
      "symbol": "AAPL",
      "side": "buy",
      "qty": "10",
      "type": "market",
      "status": "open",
      "created_at": "2026-08-29T10:00:00Z",
      "filled_at": null,
      // ... full alpaca-py Order dump
    }
  ],
  "status_filter": "open",
  "limit": 50,
  "symbols_filter": "AAPL,SPY" | null,
  "ts": "2026-08-29T10:00:00.000Z"
}
```

**Errors:** Same 401/429/502. Validation error for `status` outside `open|closed|all` → `422 Unprocessable Entity` from FastAPI.

---

### 3.4 GET /api/v1/clock — In and Out

**In Schema:** None.

**Out Schema — 200 `ClockResponse`:**
```
{
  "is_open": true,
  "clock": {
    "is_open": true,
    "timestamp": "2026-08-29T10:00:00Z",
    "next_open": "2026-08-30T09:30:00Z",
    "next_close": "2026-08-29T16:00:00Z"
  },
  "ts": "2026-08-29T10:00:00.000Z"
}
```

**Errors:** Same 401/429/502.

---

## 4. Error Catalog

| Status | Type | When | Body |
| :--- | :--- | :--- | :--- |
| 401 | `AlpacaConnectionError` | Missing `ALPACA_API_KEY`/`ALPACA_API_SECRET` | `{"detail": "Set ALPACA_API_KEY..."}` |
| 429 | `RateLimit` / `BrokerRateLimitError` | Leaky bucket empty (25/min) or upstream 429 — retry after `retry_after` seconds | `{"detail": {"error": "Rate limit exceeded...", "type": "RateLimit", "retry_after": 2.3}}` |
| 502 | `APIError` / `Exception` | Upstream 5xx, timeout after 3 retries with backoff+jitter, or unexpected | `{"detail": {"error": "...", "type": "APIError"}}` |
| 422 | `ValidationError` | Bad query param (e.g. `status=foo`, `limit=9999` without clamp) | FastAPI default `{"detail": [...]}` |

All errors are logged to `broker.jsonl` without secrets.

---

## 5. Rate Limiting and Retry

- **Bucket:** capacity 25, refill 25/60 per second (≈0.416/s). Shared singleton `backend/broker/rate_limit.py:bucket`.
- **429 handling:** bucket empty → immediate `BrokerRateLimitError` → `429` with `retry_after`. Upstream 429/5xx/timeout → retry up to 3 times with `backoff = 0.5 * 2^attempt ±20% jitter`, capped at 8s.
- **Header:** No `X-RateLimit` header in v1 — use `retry_after` in `429` body.

---

## 6. cURL Examples

```
# health
curl http://localhost:8000/health

# account
curl http://localhost:8000/api/v1/account

# positions (all, or filtered)
curl http://localhost:8000/api/v1/positions
curl "http://localhost:8000/api/v1/positions?symbol=AAPL"

# orders
curl "http://localhost:8000/api/v1/orders"
curl "http://localhost:8000/api/v1/orders?status=closed&limit=10"
curl "http://localhost:8000/api/v1/orders?status=all&limit=50&symbols=AAPL,SPY"

# clock
curl http://localhost:8000/api/v1/clock

# openapi
curl http://localhost:8000/openapi.json | jq '.paths | keys'
# ["/get_account","/api/v1/account","/api/v1/positions","/api/v1/orders","/api/v1/clock","/health"]
```

---

## 7. File Map

```
backend/
  broker/
    client.py       # throttled wrapper: get_account(), get_positions(), get_orders(), get_clock(), submit_order() market/limit/stop/options+crypto
    rate_limit.py   # TokenBucket, with_rate_limit(), backoff_delay(), is_retryable_exception() 25/min
  tools/            # LangChain @tool wrappers — 25 total (Phase 12b 21→25)
    broker_tools.py      # get_account, get_positions, get_orders, get_clock, submit_order, set_stop_loss, modify_order, cancel_order, cancel_all_orders (9, 5 HITL-protected)
    market_tools.py      # get_ohlcv, get_market_snapshot, get_option_chain, align_timeframes_tool, detect_arbitrage (5)
    news_tools.py        # fetch_news, get_macro_calendar, extract_keywords (3)
    alpaca_cli_tool.py   # Phase 12: alpaca_cli_account/positions/orders/clock via subprocess "alpaca --json" + broker fallback (4)
    mcp_tools.py         # Phase 12: mcp_get_account/positions/orders/clock via MCP bridge + broker fallback (4)
    __init__.py          # TOOLS (25), BROKER_TOOLS, BROKER_WRITE_TOOLS (5), ALPACA_CLI_TOOLS, MCP_TOOLS, MARKET_TOOLS, NEWS_TOOLS
  mcp/              # Phase 12 MCP bridge
    client.py       # is_mcp_configured, get_mcp_tools, aget_mcp_tools (langchain_mcp_adapters), mcp_server_info
    server_config.example.json  # {mcpServers: {alpaca: {command: "npx @alpacahq/alpaca-mcp-server"}}}
    __init__.py     # re-exports get_mcp_tools, is_mcp_configured
  agents/
    research.py     # Phase 12: _RESEARCH_TOOLS 3→9 (news + 3 alpaca_cli + 3 mcp), create_agent with TOOL limits, prior_regime continuity
    strategy.py     # ensure_options_in_decision, compute_sizing ATR, token <1000, no-mock fallback
    risk.py         # check_position_limit/exposure/drawdown/SPXW, logs/.paused breaker
    execution.py    # auto vs hitl interrupt + submit_order, dry_run
    reporting.py    # log→report 5-section + export
  data/
    market.py       # fetch_ohlcv (Stock+Crypto BTC/USD, derived BTC/ETH), VWAP 1m/5m/1h/1d, RSI/MACD/EMA/BB/ATR, option chain no-mock
    news.py         # fetch_news/macro no-mock (returns [] + news_no_data log)
  scheduler/        # Phase 12b autonomous loop
    runner.py       # tick() market guard is_open else skip + build_graph invoke + logs/scheduler.json persist; run_scheduler() APScheduler (jitter 30, coalesce, misfire 300) or asyncio fallback; immediate tick on start; --scheduler/--once CLI
    market_hours.py # is_market_open() via broker/client.get_clock() TTL 60s cache, seconds_until_next_open()
    state.py        # load/save scheduler state logs/scheduler.json {last_run, next_run, run_count, thread_id, interval_min} atomic
    __init__.py     # re-exports run_scheduler, tick
  core/
    models.py       # AccountResponse, PositionsResponse, OrdersResponse, ClockResponse, ErrorResponse
    logging.py      # JSON-line logger → logs/broker.jsonl + scheduler_tick/skip logs, _redact, log_event default=str
    config.py       # LLM_PROVIDER, LLM_MODEL_*, RISK_MAX_*, EXECUTION_MODE/HITL_ENABLED, MCP_SERVER_URL/COMMAND, SCHEDULER_ENABLED/INTERVAL_MIN/THREAD_ID/PROMPT
    utils.py        # get_model_id, handle_tool_errors, normalize_symbol, clamp_limit, TTLCache
  app/              # FastAPI host — active in v1 for /api/v1 broker surface (not removed)
    main.py         # FastAPI(title="Meanrev Alpaca API", version="0.1.0") + include_router(broker)
    routers/
      broker.py     # 4 GETs under /api/v1 (active)
      alpaca.py     # legacy GET /get_account — deprecated alias, remove in v2
  logs/
    broker.jsonl    # gitignored, structured audit trail (includes source: alpaca_cli vs fallback, mcp vs fallback, market_data_no_data, scheduler_tick/skip)
    scheduler.json  # gitignored, scheduler persistence {last_run, next_run, run_count, thread_id} + .paused breaker
```

---

## 8. Tool Surface (LangChain) — Phase 12 Additions

Beyond HTTP, agents use 25 LangChain `@tool` wrappers (all throttled/fallback, no mock) + scheduler `tick()` calls `build_graph` directly:

| Group | Tools | Source file | Notes |
| :--- | :--- | :--- | :--- |
| Broker | `get_account`, `get_positions`, `get_orders`, `get_clock` | `broker_tools.py` | Read via `broker/client.py` 25/min |
| Write | `submit_order` | `broker_tools.py` | `market/limit` + extensions `stop/options/crypto` (HITL) — dry_run stub, execution_agent does live throttled |
| **Stop/Manage** | `set_stop_loss`, `modify_order`, `cancel_order`, `cancel_all_orders` | `broker_tools.py` → `broker/client.py:submit_order/cancel_order_by_id/replace_order_by_id/cancel_orders` | Live throttled; `set_stop_loss` auto-qty via position (slash-insensitive), blocks crypto stop with hint; `modify_order` qty/limit/stop/trail; `cancel_*` by UUID |
| Market | `get_ohlcv`, `get_market_snapshot`, `get_option_chain`, `align_timeframes_tool`, `detect_arbitrage` | `market_tools.py` | `fetch_ohlcv` + indicators + Greeks; `detect_arbitrage(pairs, threshold_pct)` dynamic |
| News | `fetch_news`, `get_macro_calendar`, `extract_keywords` | `news_tools.py` | `No data available...` on empty (no mock) |
| **Alpaca CLI** | `alpaca_cli_account`, `alpaca_cli_positions`, `alpaca_cli_orders`, `alpaca_cli_clock` | `alpaca_cli_tool.py` | `subprocess alpaca --json` 8s + broker fallback; Phase 12 bonus |
| **MCP** | `mcp_get_account`, `mcp_get_positions`, `mcp_get_orders`, `mcp_get_clock` | `mcp_tools.py` | `mcp/client.aget_mcp_tools` when `MCP_SERVER_URL` set + broker fallback; Phase 12 bonus |

Research agent wires 9 of these (`fetch_news`, `get_macro_calendar`, `extract_keywords` + 3 CLI + 3 MCP) — see `Agent_Architecture.md §11`.

## 8b. Reserved HTTP — Not Implemented in v1

No write **HTTP** endpoints are active. When specified, they will live under `/api/v1` with full In/Out schemas:

- `POST /api/v1/orders` — submit market/limit/options orders
- `DELETE /api/v1/orders/{order_id}` — cancel
- `GET /api/v1/assets/{symbol}` — asset metadata

These remain empty until you explicitly approve a write surface. This keeps paper P&L safe during the scoring window. Writes are currently only via `submit_order` tool / `execution_agent`.

---

## 9. Platform Note — Cash-Settled Index Options Settlement Lag

`GET /api/v1/account` equity on the morning after SPXW/XSP expiry may be understated — settlement posts as an overnight journal entry, sometimes not until ~10:00 a.m. ET next day. Prefer closing index options before expiry so P&L is realized; treat morning-after equity as provisional. Logged distinctions between closed vs held-to-settlement apply.

---

## 10. Related Documentation

- [Documentation Hub](./README.md) — Central map of all system documentation
- [Backend Engine Guide](../backend/README.md) — Setup, directory structure, and engine operating modes
- [Agent Architecture](./Agent_Architecture.md) — LangGraph agent specifications, prompt limits, and tool wiring
- [Backend Architecture](./Backend_Architecture.md) — 7-layer architecture, tech stack, and state pipelines
- [How To Use](./How_To_use.md) — Operational runbook, REPL commands, and autonomous scheduler
- [Testing Strategy](./TESTING.md) — Test suites, coverage requirements, and verification procedures

---

*Source of truth: DOC.md §5 backend file architecture, §7 safeguards, and the approved broker plan (25 req/min, backoff+jitter, paper-only).*
