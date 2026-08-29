# API Reference

**Project:** Autonomous AI Trading Agent — Alpaca AI Trading Agents Hackathon (LabLab.ai)  
**Version:** v1 — Broker Read + Execution Surface (auto/hitl)  
**Base URL:** `http://localhost:8000` (local) — all broker endpoints under `/api/v1`  
**Auth:** None on HTTP — server uses `ALPACA_API_KEY` / `ALPACA_API_SECRET` from environment (gitignored, never logged). Paper trading enforced (`paper=True`).  
**Rate Limit:** 25 req/min leaky bucket (Redis-backed Lua + `InMemory` fallback) shared across all `/api/v1` calls + `tenacity` exponential backoff + jitter on 429/5xx/timeout (30s hard timeout).  
**Scoring Window:** Mon Aug 31, 9:30 a.m. ET → Fri Sep 4, 9:30 a.m. ET  
**Last Updated:** Phase 9 — Strategy token counter + options + ATR sizing, Risk `RISK_MAX_*` + circuit breaker, Execution `auto` vs `hitl` + `submit_order` market/limit/stop/options

---

## 1. Overview

v1 exposes a **read-only broker surface** that proxies Alpaca Trading API through a single throttled wrapper (`backend/broker/client.py`). No write endpoints (`POST /orders`, `DELETE /orders`) are active in v1 to keep the scoring window safe.

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
    client.py       # throttled wrapper: get_account(), get_positions(), get_orders(), get_clock()
    rate_limit.py   # TokenBucket, with_rate_limit(), backoff_delay(), is_retryable_exception()
  core/
    models.py       # AccountResponse, PositionsResponse, OrdersResponse, ClockResponse, ErrorResponse
    logging.py      # JSON-line logger → logs/broker.jsonl
  app/              # FastAPI host — active in v1 for /api/v1 broker surface (not removed)
    main.py         # FastAPI(title="Meanrev Alpaca API", version="0.1.0") + include_router(broker)
    routers/
      broker.py     # 4 GETs under /api/v1 (active)
      alpaca.py     # legacy GET /get_account — deprecated alias, remove in v2
  logs/
    broker.jsonl    # gitignored, structured audit trail
```

---

## 8. Reserved — Not Implemented in v1

No write endpoints are active. When specified, they will live under `/api/v1` with full In/Out schemas:

- `POST /api/v1/orders` — submit market/limit/options orders
- `DELETE /api/v1/orders/{order_id}` — cancel
- `GET /api/v1/assets/{symbol}` — asset metadata

These remain empty until you explicitly approve a write surface. This keeps paper P&L safe during the scoring window.

---

## 9. Platform Note — Cash-Settled Index Options Settlement Lag

`GET /api/v1/account` equity on the morning after SPXW/XSP expiry may be understated — settlement posts as an overnight journal entry, sometimes not until ~10:00 a.m. ET next day. Prefer closing index options before expiry so P&L is realized; treat morning-after equity as provisional. Logged distinctions between closed vs held-to-settlement apply.

---

*Source of truth: DOC.md §5 backend file architecture, §7 safeguards, and the approved broker plan (25 req/min, backoff+jitter, paper-only). This doc updated from "No active endpoints" to active read surface after your approval.*
