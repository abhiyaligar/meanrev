# Testing Strategy

**Project:** Autonomous AI Trading Agent — Alpaca AI Trading Agents Hackathon (LabLab.ai)  
**Scoring Window:** Mon Aug 31, 9:30 a.m. ET → Fri Sep 4, 9:30 a.m. ET  
**Related:** `Backend_Architecture.md §14` and `DOC.md §5`, `PHASES.md Phase 13`

---

## 1. Goals

- Protect the two places a silent bug costs paper money or disables safety: broker handling and risk enforcement.
- Keep prompt iteration measurable with regression tests that gate changes before the scoring window.
- Preserve auditability: every test that touches broker or risk fixtures uses the same account and risk state shapes as production logging.

---

## 2. Layers

### 2.1 Unit — Broker

- **Files:** `backend/broker/rate_limit.py`, `backend/broker/client.py`, `backend/app/routers/broker.py`
- **Cases:**
  - Token bucket refill math at 25 per minute, capacity 25, consume, remaining, and retry-after calculation.
  - Jitter range for `backoff_delay` and cap at 8 seconds.
  - Retry classification via `is_retryable_exception` including 429, 502, 503, 504, timeout, and rate-limit strings and status codes.
  - Missing position maps to empty list rather than exception.
  - `get_orders` clamps limit to 1 through 500 and filters comma-separated `symbols` post-fetch.
  - HTTP mapping: missing credentials to 401, bucket empty or upstream 429 to 429 with `retry_after`, other upstream errors to 502, and bad `status` query to 422.
- **Harness:** `pytest` with `fastapi.testclient.TestClient` against `backend/app/main.py` and `unittest.mock.MagicMock` for `TradingClient`. Bucket is reset per test via `bucket.reset_for_tests()`.

### 2.2 Unit — Risk

- **File:** `backend/agents/risk.py` (deterministic rules engine, v1)
- **Cases:**
  - Per-position size limits — rejection or scaling with rule cited.
  - Portfolio exposure caps — aggregate notion relative to equity.
  - Daily drawdown circuit breaker — threshold such as negative three percent of total equity triggers auto-pause and requires explicit CLI resume. Verified that no further orders are submitted while paused.
  - Account state inputs — unrealized and realized profit and loss, margin usage, cash balance, and drawdown versus threshold.
  - Cash-settled index options (SPXW, XSP) — prefer close-before-expiry, flag settlement lag in verdict, and do not treat morning-after equity as authoritative.

### 2.3 Agent Output — Prompt Regression

- **Files:** `backend/agents/research.py`, `backend/agents/strategy.py`
- **Tool:** DeepEval with pytest inside the Python workspace.
- **Cases:**
  - Token count below one thousand for both research and strategy prompts.
  - Output schema conformance including `TradeDecision` and `RiskVerdict` shapes.
  - Coverage of options in every strategy decision per hackathon requirement.
  - Sizing, stop, and target tied to volatility such as ATR.

### 2.4 Prompt Comparison

- **Tool:** Promptfoo as a separate Node CLI, never imported into the application.
- **Use:** Side-by-side comparison of prompt variants for research and strategy. Results are reviewed out of band and do not become a runtime dependency.

### 2.5 Integration — Graph and CLI Wiring

- **Files:** `backend/graph/state.py`, `backend/graph/build.py`, `backend/cli/commands.py`, `backend/cli/repl.py`, `backend/cli/__main__.py`, `backend/data/market.py`, `backend/data/news.py`
- **Cases:**
  - Graph wiring of the five agents with the conditional risk branch: approved goes to execution, rejected or scaled stops or is handled without invoking execution.
  - CLI to graph instruction routing including natural-language bias such as “be more conservative” and slash commands `status`, `positions`, `report`, `pause`, and `resume`.
  - Broker to execution connectivity and data to research and strategy feature flow including multi-timeframe VWAP and indicators.
  - Live streaming order of research to strategy to risk to execution.
- **Harness:** Mocked broker and in-memory bucket with a shared state fixture.

---

## 3. Configuration

### 3.1 pytest

- `pytest.ini` or `pyproject.toml` sets:
  - `asyncio_mode = auto` via `pytest-asyncio`
  - Parallel workers via `pytest-xdist -n auto`
  - Repeat and rerun flags for flaky network-adjacent tests: `pytest-repeat` and `pytest-rerunfailures`
- **Commands:**
  - `pytest`
  - `pytest --cov=backend --cov-branch --cov-report=term-missing`
  - `pytest -n auto`
  - `pytest --lf` for last failures during prompt iteration

### 3.2 Coverage

- Minimum branch coverage enforced for `backend/broker` and `backend/agents/risk.py`. Other modules are allowed to grow opportunistically within the compressed timeline.
- Coverage is reported with `pytest-cov` and excludes `logs`, `venv`, `__pycache__`, and generated report artifacts.

### 3.3 Promptfoo

- Run as `npx promptfoo eval` from its own directory with a config that points at the research and strategy prompt files.
- Outputs are stored outside `backend/logs` and are not checked into the audit trail.

---

## 4. Fixtures and Data

- **Account fixtures:** Paper account dumps with `cash`, `portfolio_value`, `buying_power`, `options_approved_level`, `trading_blocked`, unrealized and realized profit and loss, margin usage, and drawdown.
- **Position fixtures:** Single and multi-position lists with `symbol`, `qty`, `avg_entry_price`, `market_value`, `unrealized_pl`, and `unrealized_plpc`.
- **Order fixtures:** Open, closed, and mixed order lists with `id`, `symbol`, `side`, `qty`, `type`, `status`, `created_at`, and `filled_at`.
- **Clock fixtures:** `is_open`, `timestamp`, `next_open`, `next_close`.
- **Rate-limit fixtures:** Preloaded bucket states including full, partially drained, and empty with deterministic `retry_after`.

---

## 5. CI and Local Workflow

- **Pre-commit:** `ruff` or `flake8` plus `black` formatting, then `pytest -q`.
- **Pre-scoring-window gate:** Full `pytest --cov=backend --cov-branch` must pass. Any change that touches `broker` or `risk` requires a passing run before the scoring window opens.
- **Scoring-window guard:** No new prompt is promoted without a passing DeepEval regression run.

---

## 6. What Is Not Tested in v1

- Live Alpaca network calls — always mocked. The only live run is the dry-run against the dedicated submission paper account before Monday August thirty first.
- Postgres with TimescaleDB and the after-the-fact HTML renderer — both are v2 and are deferred.
- The legacy `backend/app` alias beyond its smoke test as a deprecated path.

---

## 7. References

- `Backend_Architecture.md §14` and `§14.1`
- `Agent_Architecture.md §10` and `§11`
- `PHASES.md` Phases 13 through 15 for the testing, cleanup, and dry-run gates

---

*This strategy trades exhaustive coverage for enforced coverage where it matters: every order passes through the broker bucket and every trade passes through the risk verdict. Those two paths are never changed without a passing test.*
