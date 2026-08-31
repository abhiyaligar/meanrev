# How To Use — Meanrev Autonomous Agent

**Project:** Alpaca AI Trading Agents Hackathon (LabLab.ai) — `Aug 28 11:00 ET → Sep 4 11:00 ET`, scoring `Aug 31 9:30 ET` with **$100k paper** dedicated account.

---

## Single Command `meanrev` — Like `claude` / `codex` (Copy-Paste)

**Install once** (creates `meanrev` in `venv/Scripts/meanrev.exe` + wrappers `meanrev.bat`/`meanrev`):
```bash
pip install -e .   # from repo root, creates meanrev
meanrev --help     # same as python -m backend.cli --help
```

**Copy-paste for scoring vs dry-run:**

```bash
# Auto mode — autonomous, no HITL (for Aug 31 9:30 ET scoring window, unattended)
# .env: EXECUTION_MODE=auto  HITL_ENABLED=false
meanrev --mode auto --thread-id scoring-0831 --symbol SPY
# → ₳  prompt → type natural language or /status
# → to run unattended in background (Windows):
#   start /B meanrev --mode auto --thread-id scoring-0831

# === Scheduler — autonomous every 5min when 09:30-16:00 ET open (Phase 12b, recommended for scoring) ===
# .env: SCHEDULER_ENABLED=true  SCHEDULER_INTERVAL_MIN=5  SCHEDULER_THREAD_ID=scoring-0831  SCHEDULER_PROMPT="Do Research On BTC/USD And Propose a Order"
meanrev --scheduler --thread-id scoring-0831
# → immediate tick on start if market open, then every 5min (jitter 30s), skip if closed till 09:30, persists to logs/scheduler.json
# → to run in background (Windows):
#   start /B meanrev --scheduler --thread-id scoring-0831
# → to test 1 tick without waiting (no live orders):
#   meanrev --scheduler --dry-run --once
#   python -m backend.scheduler.runner --dry-run --once

# HITL mode — human approval for every order (for dry-run, large/risky orders, SPXW)
# .env: EXECUTION_MODE=hitl  HITL_ENABLED=true  (or override via --mode hitl)
meanrev --mode hitl --thread-id dry-run-01 --symbol AAPL
# In REPL: buy 10 AAPL + 30d call
# → execution pauses: Order pending approval: buy 10 AAPL (approved) — risk rule: position ok ...
# → choose approve / edit / reject via questionary (or via API Command(resume=...))

# Dry-run (no live orders, only log)
meanrev --mode hitl --thread-id dry-run-01 --dry-run
# In REPL: /report 50 reports/dry-run.md  → 5-section report + P&L

# Alternatives if meanrev not on PATH (same behavior)
python -m backend.cli --mode auto --thread-id scoring-0831
.\meanrev.bat --mode auto --thread-id scoring-0831   # Windows wrapper
./meanrev --mode hitl --thread-id dry-run-01          # Unix wrapper
```

---

## 1. Quick Start (Copy → Configure → Run)

```bash
# 1. Copy template (never commit .env)
cp backend/.env.example backend/.env
# Edit backend/.env: set ALPACA_API_KEY/SECRET (paper), LLM_PROVIDER + LLM_MODEL_* + OPENROUTER/GROQ keys

# 2. Install (creates `meanrev` single command like claude/codex)
pip install -r requirements.txt   # or venv\Scripts\activate + pip install
pip install -e .                  # creates `meanrev` command (venv\Scripts\meanrev.exe on Windows)

# 3. Start API (broker read surface)
venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --port 8000
# -> http://localhost:8000/docs, http://localhost:8000/api/v1/account

# 4. Start CLI — single command `meanrev` like claude/codex (after pip install -e .)
meanrev --help
meanrev --mode auto --thread-id scoring-0831
# Or via python -m: venv\Scripts\python.exe -m backend.cli
# Or via wrappers: .\meanrev.bat --help  (Windows)  /  ./meanrev --help  (Unix)
# -> ₳  prompt — type /help
```

**Never touch `backend/.env` via git** — it is `gitignored`, only `.env.example` is tracked.

---

## 2. CLI Flags — `python -m backend.cli [flags]`

| Flag | Allowed | Default | What it does |
| :--- | :--- | :--- | :--- |
| `--mode` | `auto` \| `hitl` | from `EXECUTION_MODE` in `.env` (`auto`) | `auto`: `risk=approved` → direct `broker/client.submit_order` (unattended, for scoring). `hitl`: `risk=approved` → `interrupt({"action":"place_order",...})` → pause graph, wait for human `Command(resume={"decisions":[{"type":"approve|edit|reject"}]})` via `InMemorySaver` + `thread_id` |
| `--thread-id` | any string | `cli` | LangGraph `thread_id` for `InMemorySaver` checkpointer persistence (prior regime continuity, HITL resume). Use same ID to resume a paused thread. |
| `--symbol` | e.g., `AAPL`, `SPY` | `AAPL` | Default symbol for quick market checks in REPL (used by `get_market_snapshot` prefill) |
| `--dry-run` | flag, no value | off | If set, `execution` does `dry_run_no_hitl` only — no live `submit_order`, only `submit_order` tool dry-run + log |
| `--scheduler` | flag | off | **Phase 12b:** run autonomous loop (`scheduler/runner.py`) — every `SCHEDULER_INTERVAL_MIN` (default 5) when `09:30-16:00 ET` open (else `scheduler_skip_closed` till `next_open`), immediate tick on start, `logs/scheduler.json` persistence, `APScheduler` jitter 30/coalesce/misfire 300 or `asyncio` fallback |
| `--once` | flag (with `--scheduler`) | off | With `--scheduler`, run single tick then exit (for tests): `meanrev --scheduler --dry-run --once` |
| `--help` | — | — | Shows help |

**Examples:**
```bash
python -m backend.cli --help
python -m backend.cli --mode auto --thread-id scoring-0831 --symbol SPY
python -m backend.cli --mode hitl --thread-id dry-run-01 --dry-run
```

**Mode selection logic:** `core/config.py` reads `EXECUTION_MODE` + `HITL_ENABLED` from `.env`. CLI `--mode` overrides for this run (monkey-patches `get_settings()`). `hitl` requires `HITL_ENABLED=true` and a `thread_id` to resume.

---

## 3. REPL — Slash vs Natural Language

**At the `₳ ` prompt:**

| Input | Routed to | What happens |
| :--- | :--- | :--- |
| `/help` | `cli/commands.handle_help` | Lists all slash commands + prompt/model docs |
| `/status` | `handle_status` | `broker/client.get_account()` → `cash/portfolio/buying_power/options_level` + `paused` (`logs/.paused` exists) + `HITL mode` + last `broker.jsonl` line, via `rich` |
| `/positions [SYMBOL]` | `handle_positions` | `get_positions(symbol)` → table `symbol qty avg_entry mkt_value uPL` (e.g., `/positions`, `/positions AAPL`) |
| `/report [lines] [path]` | `handle_report` | `agents/reporting.reporting_agent(export_path="reports/report.md")` → 5-section `catalyst→technicals→risk→execution→P&L` Markdown + `rich` print + writes `report.md` + `report.json` for submission |
| `/pause [reason]` | `handle_pause` | `questionary.confirm` → writes `backend/logs/.paused` → `risk` rejects all next trades until `/resume` |
| `/resume [reason]` | `handle_resume` | `questionary.confirm` → deletes `logs/.paused` → `risk.clear_pause()` |
| `/quit` or `/exit` | — | Exit CLI (also `Ctrl+C` twice) |
| `be more conservative today` | `strategy.apply_instruction` via `repl._apply_instruction_hook` | Sets `state["strategy_conservatism"]=0.5` → next `strategy` `qty = min(equity*0.01/ATR, equity*0.15/price)*0.5`, `stop` wider |
| `go more aggressive` | same | `conservatism=1.5` → larger `qty`, tighter `stop` |
| `explain last trade` | same | Sets `state["strategy_explain"]` → next turn returns last `strategy.rationale` without new order |
| Any other text (e.g., `What's the outlook for AAPL?`) | `graph/build.build_graph().invoke({"messages":[{"role":"user","content":text}]}, config={"configurable":{"thread_id":...}})` | `research` (news + `alpaca_cli_*` + `mcp_*` → broker fallback, no mock) → `strategy` (market+options + `No data available...` on empty) → `risk` (deterministic) → `execution` (HITL or auto) with `rich.live.Live` streaming `research→strategy→risk→execution` while persisting to `logs/broker.jsonl`; `count_tokens` + `enforce_token_limit` keeps prompt `<1000` |

**Live streaming (11b):** `repl._run_graph_with_streaming` uses `rich.live.Live` + `rich.table.Table` to show each step as it completes; same events are `log_event` to `broker.jsonl` for audit.

---

## 4. `.env` Flags — Single Source via `backend/.env.example` → `core/config.py`

Copy `backend/.env.example` to `backend/.env` and fill **only `.env`**:

| Group | Env Var | Example / Default | Used by |
| :--- | :--- | :--- | :--- |
| **Alpaca Paper** | `ALPACA_API_URL` | `https://paper-api.alpaca.markets/v2` | `broker/client` (normalizes `/v2`) |
| | `ALPACA_API_KEY` | `YOUR_PAPER_API_KEY_HERE` | `TradingClient(paper=True)` |
| | `ALPACA_API_SECRET` | `YOUR_PAPER_SECRET_HERE` | `TradingClient` |
| **LLM Provider** | `LLM_PROVIDER` | `openrouter` (`openrouter`\|`groq`\|`modal`) | `core/config.get_settings().llm_provider` + `get_model_id()` |
| **OpenRouter** | `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | `create_agent(model="openrouter:anthropic/claude-3.5-sonnet")` fallback via `init_chat_model` if `langchain-openrouter` missing |
| **Groq** | `GROQ_API_KEY`, `GROQ_BASE_URL` | `https://api.groq.com/openai/v1` | `groq:llama-3.1-...` |
| **Modal** | `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`, `MODAL_ENVIRONMENT` | `main` | `modal` serverless |
| **Fallback** | `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | — | Direct if not using gateway |
| **Models (compulsory, no hardcoded defaults)** | `LLM_MODEL_MARKET_RESEARCH` | `anthropic/claude-3.5-sonnet` | `research.get_model_id("research")` |
| | `LLM_MODEL_STRATEGY` | `openai/gpt-4o` | `strategy.get_model_id("strategy")` |
| | `LLM_MODEL_REPORTING` | `openai/gpt-4o-mini` | `reporting.get_model_id("reporting")` |
| **MCP Server** | `MCP_SERVER_URL` | *(empty — fallback)* | `mcp/client.is_mcp_configured()` — SSE URL e.g. `http://localhost:3000/sse`; wires `mcp_get_*` tools into research agent |
| | `ALPACA_MCP_SERVER_URL` | alias | same as `MCP_SERVER_URL` |
| | `MCP_SERVER_COMMAND` | *(empty)* | stdio command e.g. `npx -y @alpacahq/alpaca-mcp-server`; alternative to URL |
| | `ALPACA_MCP_COMMAND` | alias | same as `MCP_SERVER_COMMAND` |
| **Alpaca CLI** | *(no env, auto-detected)* | `alpaca` binary | `tools/alpaca_cli_tool._run_alpaca_cli` runs `alpaca account --json` etc. with 8s timeout + broker fallback |
| **Scheduler** | `SCHEDULER_ENABLED` | `false` | `core/config` — if `true` + `meanrev --scheduler`, autonomous ticks; `false` = manual REPL only |
| | `SCHEDULER_INTERVAL_MIN` | `5` | `scheduler/runner.IntervalTrigger(minutes=5, jitter=30)` — clamp 1..60 |
| | `SCHEDULER_THREAD_ID` | `scheduler` | `InMemorySaver` thread for ticks (prior regime) — use `scoring-0831` for scoring |
| | `SCHEDULER_PROMPT` | `Do Research On BTC/USD And Propose a Order` | Prompt for each tick (e.g. `Analyze SPY and propose a trade with options`) |
| **Risk (deterministic)** | `RISK_MAX_POSITION_PCT` | `0.15` | `risk.check_position_limit` → `notional/equity <=15%` else `approved_scaled` |
| | `RISK_MAX_EXPOSURE_PCT` | `0.60` | `check_exposure` → `gross/equity <=60%` |
| | `RISK_DAILY_DRAWDOWN_PCT` | `0.03` | `check_drawdown` → `(equity-peak)/peak < -3%` → writes `logs/.paused` |
| | `RISK_PEAK_EQUITY` | empty (auto) | `track_account_state` peak, else `portfolio_value` |
| **Execution** | `EXECUTION_MODE` | `auto` (`auto`\|`hitl`) | `execution_agent` → `auto` direct submit, `hitl` → `interrupt` |
| | `HITL_ENABLED` | `false` | Must be `true` + `EXECUTION_MODE=hitl` + `thread_id` for HITL to trigger |
| **Infra** | `REDIS_URL` | `redis://localhost:6379/0` | `broker/rate_limit` Redis Lua for `25/min` multi-process, fallback to `InMemory` |

**Change models without touching code:** Edit `LLM_MODEL_*` in `.env` (e.g., `LLM_MODEL_STRATEGY=openai/gpt-4o` → `anthropic/claude-3.5-sonnet`), restart CLI; `core/utils.get_model_id()` reads at startup.

---

## 5. API — `http://localhost:8000` (FastAPI)

| Method | Path | Auth | In | Out | Flag-related |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `GET` | `/` | none | — | `{"name":"Meanrev Alpaca API","docs":"/docs"}` | — |
| `GET` | `/health` | none | — | `{"status":"ok","version":"0.1.0"}` | — |
| `GET` | `/api/v1/account` | via `.env` paper | — | `{"connected":true,"account":{...},"ts":...}` | `ALPACA_*` |
| `GET` | `/api/v1/positions?symbol=AAPL` | via `.env` | `symbol?` | `{"count":n,"positions":[...]}` | — |
| `GET` | `/api/v1/orders?status=open&limit=50&symbols=AAPL,SPY` | via `.env` | `status,limit,symbols` | `{"count":n,"orders":[...]}` | throttled `25/min` + `tenacity` |
| `GET` | `/api/v1/clock` | via `.env` | — | `{"is_open":bool,"clock":{...}}` | — |
| `GET` | `/get_account` | deprecated | — | alias to `/api/v1/account` | — |

**Rate limit:** `25/min` leaky bucket (Redis Lua if `REDIS_URL` set, else `InMemory`), `30s` hard timeout via `ThreadPoolExecutor`, `tenacity` `wait_exponential_jitter(0.5,8)` on `429/5xx/timeout`.

**System prompts:** All from `backend/core/system_prompt.py` + `backend/System_Prompt.py` (`RESEARCH/STRATEGY/REPORTING/RISK/EXECUTION/CLI`), fetched via `get_system_prompt(agent)`, never hardcoded in agents.

---

## 6. Typical Flows

**Dry-run before scoring (no P&L risk):**
```bash
python -m backend.cli --mode hitl --thread-id dry-run-01 --dry-run
# In REPL: be more conservative today → /status → /report 20 reports/dry-run.md
# Or scheduler dry-run 1 tick:
python -m backend.scheduler.runner --dry-run --once
# meanrev --scheduler --dry-run --once
```

**Scoring window (autonomous, unattended):**
```bash
# Fresh $100k paper account, .env: EXECUTION_MODE=auto HITL_ENABLED=false
# Recommended: scheduler (every 5min when market open, persists to logs/scheduler.json)
# .env: SCHEDULER_ENABLED=true SCHEDULER_INTERVAL_MIN=5 SCHEDULER_THREAD_ID=scoring-0831
meanrev --scheduler --thread-id scoring-0831
# → immediate tick if 09:30-16:00 ET open, then every 5min, skip if closed till next_open
# → to run in background (Windows):
#   start /B meanrev --scheduler --thread-id scoring-0831
# Alternative: manual REPL loop (older, requires terminal stay open)
python -m backend.cli --mode auto --thread-id scoring-0831 &
# Logs to backend/logs/broker.jsonl + scheduler.json {last_run, next_run, run_count}, report via /report or API
curl http://localhost:8000/api/v1/account | jq .account.portfolio_value
Get-Content backend\logs\broker.jsonl | findstr scheduler
```

**Human approval for large order:**
```bash
python -m backend.cli --mode hitl --thread-id order-123
# In REPL: buy 100 AAPL + 30d call
# → execution pauses: Order pending approval: buy 100 AAPL (approved) ...
# → choose approve/edit/reject via questionary or Command(resume=...)
```

**Export for submission:**
```bash
# In CLI: /report 100 reports/report.md
# Or directly:
venv/Scripts/python.exe -c "from backend.agents.reporting import reporting_agent; print(reporting_agent({}, export_path='reports/report.md')['exported_to'])"
# → reports/report.md + reports/report.json (5-section: catalyst→technicals→risk→execution→P&L)
```

---

## 7. Troubleshooting

| Symptom | Flag to check |
| :--- | :--- |
| `401 Set ALPACA_API_KEY` | `ALPACA_API_KEY/SECRET` in `backend/.env` (copy from `.env.example`) |
| `LLM not configured — stub` | `OPENROUTER_API_KEY` + `LLM_MODEL_*` in `.env` + `LLM_PROVIDER=openrouter` |
| `LLM model for 'research' not set` | `LLM_MODEL_MARKET_RESEARCH` missing in `.env` — compulsory, no default |
| `paused: true, risk rejected` | `logs/.paused` exists → `/resume` or `rm backend/logs/.paused` |
| `429 Rate limit exceeded` | `25/min` bucket — wait `retry_after` seconds or set `REDIS_URL` for multi-worker |
| `__pycache__` showing in `git status` | Already fixed in `.gitignore:48` `**/__pycache__/` — run `git status` again |
| `No data available for this symbol/timeframe` (research→strategy hold) | Expected no-mock behavior — unknown/weekend symbol returns empty `fetch_ohlcv` → `strategy` HOLD 0 → `risk: no_trade`. Use real symbol e.g. `SPY` during market hours or check `logs/broker.jsonl` for `market_data_no_data` |
| `source: alpaca_cli_fallback` + `alpaca CLI binary not found` | Normal when `alpaca` CLI not installed — fallback to broker works. Install via `pip install alpaca-trade-api` and verify `alpaca --version` to get `source: alpaca_cli` |
| `source: mcp_fallback` + `mcp_not_configured` | Normal when `MCP_SERVER_URL` empty — fallback to broker works. Set `MCP_SERVER_URL=http://localhost:3000/sse` and run `npx @alpacahq/alpaca-mcp-server` for `source: mcp` |
| `scheduler_skip_closed next_open 09:30` | Normal when market closed — scheduler waits till `09:30 ET` open, logs `scheduler_skip_closed` + persists `next_run`. Check `Get-Content backend\logs\scheduler.json` or `is_open` via `/api/v1/clock` |
| `scheduler_skip_duplicate` | Normal when restarting within 5min — `logs/scheduler.json` `last_run` < `interval*0.8` ago, tick skipped for idempotence |

*All flags are single source via `backend/.env.example` → `backend/core/config.py` (`get_settings()`) → `backend/core/utils.get_model_id()` / `backend/core/system_prompt.get_system_prompt()`.*
