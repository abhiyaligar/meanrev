# Backend Architecture

**Project:** Autonomous AI Trading Agent — Alpaca AI Trading Agents Hackathon (LabLab.ai)  
**Scoring Window:** Mon Aug 31, 9:30 a.m. ET → Fri Sep 4, 9:30 a.m. ET  
**Principle:** No UI, autonomous agent workflow only. Risk v1 is deterministic rule-based. ML surrogate risk is deferred to v2.  
**Last Updated:** Phase 12 — MCP Server + Alpaca CLI wired into research agent (TOOLS 12→21, `backend/mcp/` + `alpaca_cli_tool.py`/`mcp_tools.py`), no-mock data (`No data available...`); prior: Phase 11 `meanrev` single command, Phase 9 Strategy ATR sizing + token counter + HITL

---

## 1. Purpose and Scope

This document describes the backend architecture that powers the autonomous trading system. The backend is responsible for orchestration of specialized agents, broker connectivity, data ingestion and indicator computation, risk enforcement, execution safeguards, logging and reporting, and CLI interaction.

The system is designed as a single autonomous pipeline with distinct staged responsibilities rather than a generic service with unrelated feature folders. Every backend component exists to support the flow: research → strategy → risk → execution → reporting.

---

## 2. Architecture Principles

- **Autonomy first:** The system must be able to run unattended against a dedicated Alpaca paper account during the scoring window. Manual intervention is limited to CLI commands such as pause, resume, and status inspection.
- **No UI requirement:** Per Alpaca official guidance, a user interface is not evaluated. A web dashboard is intentionally excluded. Observability is provided through a rich CLI and structured logs.
- **Graph-centric decomposition:** The backend is organized around the agent graph. Each stage of the pipeline owns its concerns and communicates through a shared state schema.
- **Deterministic safety before intelligence:** Risk controls are rule-based and enforceable without model inference in v1. Intelligence layers (strategy and research) propose, risk disposes.
- **Auditability by default:** Every agent step is written to a structured JSON-line log. The reporting artifact is derived from that log, not from transient terminal output.
- **Throttled broker surface:** All Alpaca interactions pass through a single throttled client with rate limiting and retry semantics.

---

## 3. High-Level System Overview

The backend consists of six logical layers:

1.  **CLI Layer:** Interactive REPL for operator control and live observation.
2.  **Orchestration Layer:** LangGraph state machine that sequences agents.
3.  **Agent Layer:** Five specialized agents with distinct model assignments.
4.  **Broker Layer:** Thin Alpaca integration with rate limiting and backoff.
5.  **Data Layer:** Market data and news/sentiment ingestion plus indicator enrichment.
6.  **Core Layer:** Configuration, shared domain models, and structured logging.

Supporting infrastructure includes optional Redis for token-bucket rate limiting and optional PostgreSQL with TimescaleDB for historical persistence. In v1, flat-file JSON-line logs are the primary audit trail and persistence is deferred.

---

## 4. Technology Stack

| Layer | Component | Role and Notes |
| :--- | :--- | :--- |
| Interface | CLI with prompt_toolkit, rich, and questionary | Claude-Code-style REPL, supports natural language instructions and slash commands, streams agent activity live; questionary for interactive prompts (e.g., confirmations, selections) |
| Orchestration | LangChain and LangGraph | Graph-based state machine, each node is a specialized agent, shared state via pydantic schema |
| Market Research | `LLM_MODEL_MARKET_RESEARCH` (default `anthropic/claude-3.5-sonnet`) via OpenRouter / Groq / Modal | Macro catalyst monitoring, news and sentiment analysis, regime classification; model ID read from `.env.example` → `.env` selector |
| Strategy and Decision | `LLM_MODEL_STRATEGY` (default `openai/gpt-4o`) via OpenRouter / Groq / Modal | Signal synthesis combining sentiment with technical indicators, produces trade parameters; model ID read from `.env.example` → `.env` selector |
| Reporting | `LLM_MODEL_REPORTING` (default `openai/gpt-4o-mini`) via same gateway | Structured log → human-readable summary; model ID read from `.env.example` selector |
| LLM Provider Gateway | OpenRouter, Groq, or Modal | Provider abstraction — OpenRouter (unified), Groq (fast inference), or Modal (serverless GPU). Selected via `LLM_PROVIDER` env; models via `LLM_MODEL_*` selectors in `.env.example`. Direct keys remain fallback |
| Risk Management | Deterministic Rules Engine (Python, `RISK_MAX_*` from `.env`) | Position limits 15%, exposure 60%, daily drawdown 3% → `logs/.paused` auto-pause, SPXW/XSP close-before-expiry; ML surrogate deferred to v2 |
| Execution | Dual-mode `auto` (autonomous) vs `hitl` (Human-in-the-Loop via `langgraph.types.interrupt`) | `EXECUTION_MODE` + `HITL_ENABLED` from `.env`; `broker/client.submit_order` throttled `25/min` + `tenacity` + `30s` timeout, handles `market/limit/stop/options` with fill/partial/reject logging |
| Broker Integration | `alpaca-py==0.44.0` + `tenacity==9.1.4` + `scipy` (optional) | Paper trading only, throttled wrapper (`tenacity.wait_exponential_jitter`, `is_retryable`); Greeks via `scipy.stats.norm` else `math.erf` |
| Database | PostgreSQL plus TimescaleDB, optional | Deferred in v1; used only if flat-file logs prove insufficient for historical bars and audit history |
| Caching and Rate Limiting | Redis | Token bucket for execution limiter (see §9.1), optional pub/sub between agent stages; configured via REDIS_URL in .env (e.g., redis://localhost:6379/0) |
| Token Counter | `tiktoken==0.14.0` | Strategy prompt `<1000` tokens via `tiktoken.encoding_for_model`, `enforce_token_limit` truncates head/tail |
| Evaluation | DeepEval with pytest and Promptfoo (Node CLI, separate) | Prompt regression and agent output quality checks, not imported into the application runtime |
| DRY Utils | `backend/core/utils.py` | Single source for `get_model_id` (was 4×), `handle_tool_errors` (was 3×), `normalize_symbol`, `clamp_limit` — net -71 lines |
| System Prompts | `backend/core/system_prompt.py` + `backend/System_Prompt.py` | Central registry for `RESEARCH/STRATEGY/REPORTING/RISK/EXECUTION/CLI` prompts, fetched via `get_system_prompt(agent)` |
| Alpaca CLI | `backend/tools/alpaca_cli_tool.py` | `alpaca` CLI subprocess wrappers (`account/positions/orders/clock --json`) with broker fallback, 8s timeout; wired into research agent for Phase 12 bonus |
| MCP Server | `backend/mcp/client.py` + `backend/tools/mcp_tools.py` | MCP bridge (`langchain_mcp_adapters` when `MCP_SERVER_URL` set) + 4 LangChain tools with broker fallback; config in `backend/mcp/server_config.example.json` |
| No-Mock Policy | `backend/data/market.py` + `backend/data/news.py` | Every empty fetch returns `[]` or `pd.DataFrame()` with `No data available for this ...` + `log_event` (`market_data_no_data`, `news_no_data`), never fabricated bars/headlines — `strategy` returns `risk: no_trade` on empty |

**Explicitly excluded in v1:** FastAPI backend service and React plus Tailwind frontend. These were removed after confirmation that a UI is not required and incurs unnecessary scope. The ML surrogate risk model using XGBoost or LSTM over Monte Carlo paths is also excluded from v1 for timeline reasons.

---

## 5. Backend File Architecture — Target Structure

The project is structured around the agent graph, not generic feature folders.

- **cli:** Houses the REPL entrypoint, the prompt loop and command routing, and the handlers for slash commands such as status, positions, report, pause, and resume.
- **agents:** Contains one module per specialized agent: research, strategy, risk, execution, and reporting. Each module encapsulates its prompt (fetched from `core/system_prompt.py`), input contract, and output contract; `research`/`strategy`/`reporting` are built-in `create_agent` with `HumanInTheLoopMiddleware` for `submit_order`. `research` now imports 9 tools (3 news + 3 `alpaca_cli` + 3 `mcp`) for Phase 12 bonus; `research_agent` vs `get_research_agent()` factory covers sync+async MCP loading.
- **graph:** Holds the shared LangGraph state schema (`GraphState` with 7 fields: `messages`, `market_snapshot`, `research`, `strategy`, `risk`, `execution`, `reporting_context`) and the wiring (`research→strategy→risk→execution` with `approved_scaled` handling and `InMemorySaver` checkpointer for HITL).
- **broker:** Encapsulates the Alpaca client wrapper (`get_account`, `get_positions`, `get_orders`, `get_clock`, `submit_order` for `market/limit/stop/options`) and the Redis-backed rate limiting (`25/min` Lua + `tenacity` retry + `30s` timeout).
- **data:** Separates market data handling (`fetch_ohlcv` + `VWAP` + `RSI/MACD/EMA/BB/ATR` via `pandas_ta`, `fetch_option_chain` with Greeks via `scipy` else `math`, `align_timeframes`, `cache` TTL, **no-mock**: empty `AAPL` weekend or unknown symbol returns `pd.DataFrame()` + `log_event("market_data_no_data", "No data available for this symbol/timeframe")`) from news and sentiment fetching (`fetch_news`, `get_macro_calendar`, `extract_keywords`, also no-mock with `news_no_data`/`macro_calendar_no_data`).
- **tools:** LangChain `@tool` wrappers — 21 tools total: `broker_tools` (5), `market_tools` (5 + `detect_arbitrage`), `news_tools` (3), `alpaca_cli_tool` (4: `alpaca_cli_account/positions/orders/clock` via subprocess + broker fallback), `mcp_tools` (4: `mcp_get_account/positions/orders/clock` via MCP bridge + broker fallback). All respect `25/min` + `30s` timeout (CLI 8s), `submit_order` is HITL-protected. Grouped as `TOOLS`, `BROKER_TOOLS`, `ALPACA_CLI_TOOLS`, `MCP_TOOLS`, `MARKET_TOOLS`, `NEWS_TOOLS`.
- **mcp:** MCP bridge for Phase 12 bonus — `mcp/client.py` (`is_mcp_configured`, `get_mcp_tools`, `aget_mcp_tools`, `mcp_server_info`) + `mcp/server_config.example.json` + `tools/mcp_tools.py` wrapping Alpaca MCP Server when `MCP_SERVER_URL`/`MCP_SERVER_COMMAND` set, else broker fallback.
- **core:** Centralizes configuration management (`config.py` with `LLM_PROVIDER`, `LLM_MODEL_*` compulsory from `.env`, `RISK_MAX_*`, `EXECUTION_MODE`/`HITL_ENABLED`, `MCP_SERVER_URL/COMMAND`), structured logging (`logging.py` with `_redact`), shared domain models (`models.py`), system prompts (`system_prompt.py` + `System_Prompt.py`), and DRY utils (`utils.py`).
- **logs:** Gitignored directory for JSON-line output (`broker.jsonl`, `.paused` flag for circuit breaker) that serves as the authoritative audit trail.
- **Root configuration:** Environment template (`backend/.env.example` with `LLM_MODEL_*`, `RISK_MAX_*`, `EXECUTION_MODE`, `MCP_SERVER_URL/COMMAND`), ignore rules, Python project manifest (`pyproject.toml` with `meanrev` entry), and single-command wrappers (`meanrev`, `meanrev.bat`).

This layout ensures that a change to risk logic, execution throttling, or strategy prompting is isolated to a single directory with a clear ownership boundary.

---

## 6. Layer Responsibilities

### 6.1 CLI Layer

- Provides an interactive REPL that remains open for the operator while agents run. Single entry `meanrev` (via `pyproject.toml` `[project.scripts]` → `venv/Scripts/meanrev.exe`, plus `meanrev.bat`/`meanrev` wrappers) like `claude`/`codex` — `meanrev --mode auto --thread-id scoring-0831` or `python -m backend.cli`.
- Routes natural-language input to the agent graph as instructions and routes slash commands to direct handlers.
- Streams agent steps live to the terminal via `rich.live.Live` while the same events are persisted to the structured log.
- Exposes commands for operational control and inspection without requiring a browser or dashboard — see `How_To_use.md` for flag table (`--mode`, `--thread-id`, `--symbol`, `--dry-run`).

### 6.2 Orchestration Layer

- Defines the shared state that flows between agents, including market context, sentiment output, proposed trades, risk verdicts, execution confirmations, and reporting context.
- Sequences the five agents in order and determines conditional branching such as rejection at the risk stage or retry at execution.
- Ensures that state transitions are validated and that agent outputs conform to expected schemas before advancing.

### 6.3 Agent Layer

See Agent_Architecture.md for full agent specifications. From the backend perspective, each agent is a LangGraph node with a single responsibility and a well-defined input and output.

### 6.4 Broker Layer

- Offers a single integration point for all Alpaca Trading API interactions.
- Enforces a leaky-bucket rate limit targeting 25 requests per minute to stay below platform caps.
- Applies exponential backoff with jitter on timeouts and rate-limit responses.
- Handles authentication via paper credentials and normalizes API URL configuration.

### 6.5 Data Layer

- Aggregates OHLCV data across multiple timeframes and computes technical indicators including RSI, MACD, EMA variants, Bollinger Bands, and ATR.
- Fetches and processes news and macro catalyst information for sentiment analysis.
- Supplies both the research and strategy agents with normalized, time-aligned features.
- Tracks account and risk state such as unrealized and realized profit and loss, margin usage, cash balance, and drawdown relative to threshold.

### 6.6 Core Layer

- Manages configuration from environment variables with a committed template and a gitignored secrets file.
- Provides the structured logger that writes JSON-line events for every agent transition.
- Defines shared domain models that are reused across agents, graph state, and logging.

---

## 7. Data Points and Feature Matrix

| Category | Fields Consumed by the System |
| :--- | :--- |
| Price and Volume | Open, High, Low, Close, Volume, VWAP aggregated at 1 minute, 5 minute, 1 hour, and 1 day |
| Technical Indicators | RSI, MACD, EMA at 20, 50, and 200 periods, Bollinger Bands, Average True Range |
| Macro and Catalyst Data | Federal Reserve speeches, Non-Farm Payrolls, Consumer Price Index, earnings releases, benchmark revisions |
| Sentiment Metrics | News headline sentiment, social velocity, natural language keyword extraction |
| Account and Risk State | Unrealized and realized profit and loss, margin usage, cash balance, drawdown versus threshold |

The data layer is responsible for ensuring these inputs are fresh, aligned, and available to agents without each agent performing its own fetching or computation.

---

## 8. Configuration and Environment

- Configuration is loaded from environment variables with support for a local environment file. LLM provider and model selection are driven by `LLM_PROVIDER` and `LLM_MODEL_*` selectors defined in `.env.example` (single source of truth).
- A committed example file documents the required keys without containing secrets. The real file is gitignored.
- Required credentials are an Alpaca paper API key and secret dedicated to the submission account. Live credentials must not be used.
- The Alpaca API URL is configurable and normalized by the broker layer regardless of whether a version suffix is included.
- The system distinguishes a development paper account used for prototyping from the fresh submission paper account that starts with a 100,000 dollar balance. Reuse of a testing account for judging is not permitted.

---

## 9. Persistence and Caching Strategy

- **Primary audit trail in v1:** Structured JSON-line logs on the filesystem (`logs/broker.jsonl` + `.paused` flag). These logs are the source of truth for debugging, for the reporting agent, and for demonstrating agent reasoning to judges. Every `alpaca_cli_*` / `mcp_*` call logs `source: "alpaca_cli"` vs `"alpaca_cli_fallback"` / `"mcp"` vs `"mcp_fallback"` plus `cli_error`/`mcp_reason` for bonus verification; empty data logs `market_data_no_data` with `No data available...`.
- **Optional relational persistence:** PostgreSQL with TimescaleDB is reserved for v2 or for cases where flat-file history becomes insufficient for querying historical bars or long agent histories.
- **Caching and coordination:** Redis is used for the execution rate limiter token bucket and optionally for publish and subscribe messaging between agent stages. Its usage is limited to operational concerns, not as a primary data store in v1.
- **No dashboard database:** Since no web frontend exists, there is no backing store for UI state.

### 9.1 Redis Configuration

- **Purpose in v1:** Provide the token-bucket state for the broker rate limiter (25 requests per minute, capacity 25, refill 25/60 per second). The in-process fallback in `backend/broker/rate_limit.py` is the default for local development and single-worker runs; Redis is enabled when `REDIS_URL` is set and multiple workers or a durable bucket across restarts is required.
- **Connection:** Configured via `REDIS_URL` (for example, `redis://localhost:6379/0`) in the environment file. No Redis secret is committed. When `REDIS_URL` is absent, the system falls back to the in-memory bucket without error.
- **Rate limiter keys:** `rate_limit:bucket:tokens` and `rate_limit:bucket:ts` with atomic update via Lua or single-command transaction to preserve the 25 per minute guarantee across workers.
- **Pub/sub (optional):** When enabled, agent stages can publish events such as research completed or risk verdict to `agent:events` for live streaming fan-out. The feature is disabled by default and does not affect correctness of the autonomous loop.
- **Failure mode:** If Redis is unreachable, the broker surface surfaces a retryable error that is handled with the same exponential backoff and jitter as an upstream 429, and the operator can continue with the in-memory limiter for single-worker operation.

---

## 10. Execution Safeguards

- **Rate limiter:** Leaky-bucket limiter targeting 25 requests per minute, providing headroom below Alpaca platform limits. All order submissions are funneled through this limiter.
- **Prompt token discipline:** System prompts are capped below 1,000 tokens to control cost, latency, and behavior drift.
- **Retry with backoff:** Timeouts and rate-limit responses trigger exponential backoff with jitter rather than immediate retry.
- **Circuit breaker:** An automatic pause triggers when daily drawdown exceeds a configured threshold, for example negative 3 percent of total equity. The system remains paused until an operator resumes via the CLI after review.
- **Options settlement awareness:** See section 11 for the specific handling of cash-settled index options.

---

## 11. Known Platform Risk — Cash-Settled Index Options Settlement Lag

Cash-settled index options such as SPXW and XSP do not involve exercise or assignment. Settlement occurs as an overnight cash journal entry. Community testing on Alpaca paper accounts has observed that the settlement for an expiry-day position does not appear in end-of-day equity on expiry day and may not post until the following morning, sometimes as late as approximately 10:00 a.m. Eastern.

For a hold-to-expiry approach this can mean the entire final day profit and loss is invisible at the close. A paper equity reading on the morning after a zero-days-to-expiry expiration may therefore be understated.

**Backend mitigations:**

- Strategy guidance is to close cash-settled index option positions before expiry rather than holding to settlement, so profit and loss is realized immediately and visible in equity without lag.
- The risk layer must not treat the raw equity reading on the morning after an expiry as authoritative without accounting for possible pending settlement.
- Logging and reporting must distinguish a closed position with realized profit and loss from a held-to-expiry position awaiting settlement, so that judging artifacts are not misinterpreted.

---

## 12. Observability and Reporting

- Every agent transition emits a structured log event to the JSON-line log, including inputs, outputs, and timing.
- The CLI streams these events live for the operator and also persists them for later replay.
- A dedicated reporting agent reads the log and produces a human-readable summary covering positions, trades, profit and loss, and the reasoning trail. The summary is invoked on demand through the CLI report command and serves as the primary artifact for demo and judging, in the absence of a dashboard.
- No log file doubles as a secrets store. Credentials are never written to logs.

---

## 13. Security Considerations

- Secrets are loaded from environment and never committed, logged, or returned by any endpoint or report.
- Paper trading is enforced. The broker layer is configured to prevent accidental use of live endpoints.
- The rate limiter and circuit breaker act as safety controls against runaway autonomous behavior.
- Dependency scope is kept narrow in v1 by excluding a web server and frontend build chain, reducing attack surface.

---

## 14. Evaluation and Quality Assurance

- **DeepEval with pytest** is used for prompt regression testing and agent output quality checks within the Python workspace.
- **Promptfoo** is run as a separate Node CLI and is not imported into the application. It provides comparative prompt evaluation without coupling to the runtime.
- **Priority test coverage:** Broker logic and the risk module receive the highest test priority, since silent bugs in either area can cause incorrect orders or failure of the drawdown guardrail. Other modules are tested opportunistically given the compressed timeline.

### 14.1 Testing Strategy

- **Unit layer — broker:** `backend/broker/rate_limit.py` token bucket refill, consume, retry-after math, and jitter range; `backend/broker/client.py` 25 per minute enforcement, 429 and 5xx retry with backoff, mapping of missing position to empty list, and error translation to 401, 429, and 502. Tests mock `TradingClient` and use `TestClient` against `backend/app/main.py`.
- **Unit layer — risk:** `backend/agents/risk.py` per-position size limits, exposure caps, daily drawdown circuit breaker threshold including auto-pause and explicit resume, and cash-settled index options handling such as close-before-expiry preference and settlement lag flagging. Verified against account state fixtures with unrealized and realized profit and loss, margin usage, and cash.
- **Agent output layer:** Strategy and research prompt regression under DeepEval with pytest, asserting token count below one thousand, output schema conformance, and presence of options in every trade decision. Prompt changes are gated on these tests.
- **Integration layer:** Graph wiring in `backend/graph/build.py` including the conditional risk branch where approved goes to execution and rejected or scaled stops or retries; CLI to graph instruction routing such as status, positions, report, pause, and resume; broker to execution connectivity; data to research and strategy feature flow. Integration tests use a mocked broker and in-memory bucket.
- **Prompt evaluation layer:** Promptfoo as a separate Node CLI for side-by-side comparison of prompt variants. Results are recorded outside the application and do not affect runtime dependencies.
- **Configuration and quality:** `pytest.ini` and `pyproject.toml` configure `pytest-asyncio`, `pytest-xdist`, and coverage with `pytest --cov=backend --cov-branch`; minimum branch coverage is enforced for `broker` and `risk` while other modules are allowed to grow. Async tests and parallel workers are enabled. See `docs/TESTING.md` for the full matrix, fixtures, and commands.

---

## 15. Deferred and v2 Extensions

- **ML surrogate risk model:** An XGBoost or LightGBM model trained on simulated portfolio paths to approximate Value at Risk in real time, restoring the original sub-10-millisecond estimation goal. Deferred to v2 and replaced in v1 by the deterministic rules engine.
- **Relational persistence:** Full PostgreSQL with TimescaleDB for historical bars and agent logs if flat-file logs prove insufficient.
- **Static report renderer:** An after-the-fact HTML report generator that transforms the structured log into a shareable static document for demo purposes, without building a live dashboard.

---

## 16. Operational Requirements for Submission

- Create a new Alpaca paper account with a starting balance of exactly 100,000 dollars dedicated to the submission. Do not reuse the development account.
- Populate the environment file with that account key and secret using the provided template.
- Ensure the agent is live and trading from that account starting Monday, August 31, at 9:30 a.m. Eastern.
- Dry-run the full pipeline against the new account before Monday so that authentication or configuration issues surface outside the scoring window.
- Free-tier market data with an indicative options feed is permitted. OPRA or Algo Trader Plus is optional and not required.

---

## 17. References

- Alpaca Getting Started, Trading API, Python SDK, CLI, and MCP Server documentation
- LabLab.ai hackathon page and Discord
- Project living document DOC.md, which remains the authoritative source for evolving decisions

---

*This architecture deliberately trades breadth for reliability in v1. A small number of enforceable guarantees — throttled execution, capped prompts, a hard drawdown breaker, and full audit logging — are prioritized over additional features that would be harder to verify under hackathon constraints.*
