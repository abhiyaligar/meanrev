# Tests — Meanrev Autonomous Agent

**Run:** `pytest` · `pytest --cov` · `pytest -k risk`  
**Config:** `pyproject.toml` [tool.pytest.ini_options] — `asyncio_mode=auto`, `markers unit/integration`, `filterwarnings ignore`.

## What we test (and why)

| Layer | File | What | Why it matters |
| :--- | :--- | :--- | :--- |
| **Risk** | `tests/test_risk.py` (18 tests) | `check_position_limit` at exactly `max_position_pct` (via `get_settings().risk_max_position_pct`), below/above, scaled qty sign, qty0/price0/equity0; `check_exposure` at `max_exposure_pct` with existing exposure; `check_drawdown` at `threshold-0.0001` (not triggered) vs `threshold+0.001` (triggered) + `peak 0` + `equity>peak`; `SPXW` 0d→close, 2d→lag, 10d→no-lag; `track_account_state` missing equity→cash, peak from settings, unrealized sum; `evaluate_risk` `hold→no_trade`, `approved` (50% of limit), `approved_scaled` (2× limit), `rejected` after scale+exposure, `drawdown→paused`, `paused→reject all`, `SPXW buy→reject`, `sell preserves -qty`, `JSON string parsing` | Every order passes this. Thresholds read from `.env` via fixtures, not hardcoded `0.15`. |
| **Broker** | `tests/test_broker.py` (8 tests) | `TokenBucket` `capacity/refill` via constants, consume 5→remaining, exhaust→retry_after, refill after sleep; `backoff_delay` 0→0.4-0.6 jitter, cap 8s; `is_retryable` 429/502/503/timeout vs 400; `retry 429 then success` via `tenacity`; `not retry on 400`; `get_positions 404→[]`; `get_orders clamp 0→1, 999→500 + filter symbols`; `submit_order validation`; `HTTP 401/429/502` via `TestClient` | 25/min bucket + 429/5xx/timeout is where paper money is lost if wrong. |
| **Data no-mock** | `tests/test_data_no_mock.py` (7 tests) | `fetch_ohlcv UNKNOWN→empty DataFrame` (no mock bars), `fetch_option_chain→[]`, `fetch_news→[]`, `get_macro_calendar→[]`, `ensure_options_in_decision` injects `No data available` error leg (not mock call), `set_stop_loss BTC→CryptoStopNotSupported` | Verifies `499d125` no-mock fix — unknown symbols never fabricate. |
| **Scheduler** | `tests/test_scheduler.py` (4 tests) | `tick closed→skip + next_run=09:30`, `tick open→invoke graph + state run_count 1`, `duplicate within 5min→skip`, `persistence roundtrip` `logs/scheduler.json` | 12b autonomous loop must sleep when closed and not duplicate after crash. |
| **Graph/CLI** | `tests/test_graph_cli.py` (3 tests) | `graph conditional` `hold→no_trade→skip execution` via stub, `apply_instruction` conservative `0.5`/aggressive `1.5`/explain, `repl _apply_instruction_hook` | Wiring of 5 agents + CLI routing is integration risk. |

Total **~40 tests**, ~`4` files, no useless `assert True`.

## Fixtures (no hardcodes)

`tests/conftest.py` — `settings` (`get_settings()`), `risk_thresholds` (`max_position_pct`, `max_exposure_pct`, `drawdown_pct` from settings), `equity=100_000`, `account` (portfolio_value/cash/buying_power), `account_drawdown` (`peak * (1 - threshold - 0.001)`), `positions_single`, `orders_open`, `clock_open/closed`, `bucket_fresh` (capacity/refill from `rate_limit` constants), `mock_broker` (patches `get_account/positions/orders/clock`).

```python
# Example — position at exactly threshold (never hardcoded 0.15)
max_pct = risk_thresholds["max_position_pct"]
qty_exact = equity * max_pct / price
ok, adj, msg = check_position_limit("AAPL", qty_exact, price, equity)
assert ok
```

## Commands

```bash
pytest -q
pytest --cov=backend --cov-branch --cov-report=term-missing
pytest -k "risk or broker" -q
pytest -n auto          # xdist parallel
pytest --lf             # last failures
```

## Coverage

`pyproject.toml` `tool.coverage.run` → `source=backend, branch=true, omit=logs/__pycache__/venv`.  
Goal: `broker` + `agents/risk.py` ≥ high branch coverage; other modules opportunistically.

## Not tested (intentional)

* Live Alpaca network (always mocked, only dry-run against `PA3WKUKN51YI` before scoring).
* Real MCP SSE connection (`MCP_SERVER_URL` requires `npx` server).
* `backend/app` legacy alias beyond smoke `TestClient` 401/429/502.
* `PostgreSQL/TimescaleDB`, HTML renderer — v2 deferred.

*Tests are the gate noted in `docs/TESTING.md §5`: no `broker`/`risk` change lands without `pytest` green.*
