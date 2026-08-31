# Complete Product Requirements Document — Autonomous AI Trading Agent

**Project name:** TBD — candidates under consideration include drawdown and neurotrade  
**Event:** Alpaca AI Trading Agents Hackathon, hosted on LabLab.ai  
**Hackathon window:** Fri Aug 28, 9:30 a.m. ET → Fri Sep 4, 9:30 a.m. ET  
**Official scoring window:** Mon Aug 31, 9:30 a.m. ET → Fri Sep 4, 9:30 a.m. ET  
**Paper account starting balance for judging:** 100,000 dollars in a new dedicated paper account  
**Evaluation focus:** Total equity change during the scoring window plus creativity, autonomy, and robustness of the agent workflow. Profit and loss matters but is not the sole factor. A user interface is not required per Alpaca.  
**Last Updated:** Phase 12 — MCP Server + Alpaca CLI wired into research agent (TOOLS 12→21: `alpaca_cli_*` + `mcp_*` with broker fallback, `backend/mcp/` + `server_config.example.json`), no-mock `No data available...`; prior: Phase 11 `meanrev` single command, Phase 9 Strategy `count_tokens<1000` + options + ATR

---

## 1. Executive Summary

Build an autonomous, multi-agent AI trading system that performs market research, strategy generation, risk validation, execution, and reporting with minimal human intervention, running against Alpaca programmable brokerage for stocks, options, ETFs, and crypto on a dedicated paper account.

Two scope decisions shape this product:

- **No UI is built.** The React and Tailwind dashboard originally contemplated was dropped after Alpaca confirmed evaluation is on the autonomous agent workflow and trading performance, not on a frontend. Observability is delivered through a rich CLI and a reporting agent that reads structured logs.
- **No ML surrogate risk model in v1.** The original plan to approximate Value at Risk in real time with an XGBoost or LSTM model trained on more than 100,000 Monte Carlo paths is deferred. Risk in v1 is enforced by a deterministic, rule-based engine with hard thresholds and a circuit breaker.

The product therefore optimizes for verifiable autonomy: a small set of scoped agents, a shared state contract, enforceable safety invariants, and a full audit trail that can be shown to judges.

---

## 2. Goals and Non-Goals

### 2.1 Goals

- Operate autonomously for the full scoring window with live streaming of agent activity and a reproducible log.
- Identify opportunities by combining macro catalyst and sentiment analysis with technical indicators and account state.
- Generate and validate trade parameters including direction, sizing, and stop and target levels.
- Incorporate options trading in every strategy, satisfying the hackathon requirement.
- Use at least one of the Alpaca MCP Server or Alpaca CLI during operation.
- Manage positions with deterministic risk gates and a daily drawdown circuit breaker.
- Produce a clear one-page write-up plus a reporting artifact that together explain AI logic, risk gates, and Alpaca integration.
- Demonstrate profit and loss on a fresh paper account in a way that is attributable and auditable.

### 2.2 Non-Goals for v1

- No web dashboard or hosted frontend.
- No learned risk scoring. The ML surrogate Value at Risk model is a v2 extension.
- No persistent relational store beyond flat-file logs unless those logs prove insufficient.
- No attempt to guarantee positive profit and loss. The product guarantees autonomy, discipline, and auditability; market outcomes are not promised.

---

## 3. Stakeholders and Personas

| Stakeholder | Interest |
| :--- | :--- |
| Hackathon judges | Evaluate profit and loss during the scoring window, technology implementation, creativity, and presentation |
| LabLab.ai organizers | Operate the submission and evaluation process |
| Alpaca platform | Provides brokerage, data, MCP server, and CLI; evaluates integration quality |
| Build team (1 to 6 people) | Designs, implements, operates, and presents the agent |
| Operator during scoring week | Monitors the live system via CLI, pauses or resumes when needed, triggers reports |

**Primary persona:** The operator. This person starts the agent against the dedicated paper account, watches live streamed steps, inspects positions and profit and loss on demand, pauses the system when risk thresholds trigger, and generates the report that will be submitted and demoed. The operator does not place trades manually; the agent does.

---

## 4. Hackathon Context and Constraints

- **Dates and schedule:** Kick-off August 28 at 11:00 a.m. Eastern. Submissions close September 4 at 11:00 a.m. Eastern. Winners are paid within 90 days subject to tax documentation.
- **Prize pool:** 6,300 dollars total. First place 2,500 plus Featherless credits, second 1,500, third 1,000, plus two social engagement awards of 500 each with Algo Trader Plus.
- **Team rules:** One to six people, 18 and older, no purchase required to register, paper trading is free.
- **Core challenge requirements that must be satisfied for judging:** Autonomous agents that trade via the Trading API, use of MCP Server or CLI, and use of options trading in all strategies.
- **Account rules:** Development may use any paper account. Judging must use a brand new paper account with exactly 100,000 dollars. The submission must include the Alpaca paper account identifier so judges can pull profit and loss. Reused accounts are ineligible.
- **Platform stack:** Trading API for orders and account data, MCP Server for Claude, Cursor, or VS Code integration in the paper environment, CLI for terminal-based JSON output suitable for automation, paper environment with simulated 100,000 dollars and real market data.
- **Market data:** Free-tier market data with an indicative options feed is sufficient. OPRA or Algo Trader Plus subscriptions are optional and not required for eligibility.

---

## 5. Product Overview

The product is a single autonomous pipeline organized as a graph of five specialized agents plus a CLI. It runs as a long-lived process that loops through sensing, deciding, checking, and acting, while persisting every step to a structured log.

Market Research (Claude 3.5 Sonnet via OpenRouter / Groq / Modal) → Strategy (GPT-4o via OpenRouter / Groq / Modal) → Risk Management (deterministic) → Execution (throttled) → Reporting (log summarizer)

Natural-language instructions from the CLI can modulate behavior at the top of the graph, while slash commands provide direct operational control without invoking the full chain.

---

## 6. Functional Requirements

### 6.1 Autonomy and Orchestration

- The system must run as a continuous loop that autonomously cycles through research, strategy, risk, execution, and logging without requiring a per-trade manual trigger.
- Orchestration must be handled by LangChain and LangGraph as a graph-based state machine where each node is one agent and state is shared across transitions.
- The shared state must carry market context, research output, proposed trade parameters, risk verdict, execution confirmation, and reporting context in a single validated schema.
- System prompts must remain below 1,000 tokens.

### 6.2 Market Research

- The system must monitor macro catalysts such as Federal Reserve speeches, Non-Farm Payrolls, Consumer Price Index, earnings, and benchmark revisions.
- The system must ingest news headline sentiment, social velocity, and keyword signals.
- The research agent, using Claude 3.5 Sonnet via OpenRouter / Groq / Modal (selected via LLM_PROVIDER), must produce a qualitative sentiment vector and a macro regime classification with a compact catalyst summary.

### 6.3 Strategy Generation

- The strategy agent, using GPT-4o via OpenRouter / Groq / Modal (selected via `LLM_PROVIDER` + `LLM_MODEL_STRATEGY` compulsory from `.env` via `get_model_id`), must combine the research output with technical indicators and price history to produce a trade decision.
- Indicators to be considered include RSI, MACD, EMA at 20, 50, and 200, Bollinger Bands, Average True Range, and VWAP aggregated at 1 minute, 5 minute, 1 hour, and 1 day (via `data/market.fetch_ohlcv` + `pandas_ta`).
- The strategy output must include direction of buy, sell, or hold, sizing as notional or contract count, stop and target levels (tied to `ATR` via `compute_sizing(atr, equity, price, conservatism)` — `qty = min(equity*0.01/ATR, equity*0.15/price) * conservatism`, `stop = close-1.5*ATR`, `target = close+2.5*ATR`), and a concise rationale.
- Prompt must be `<1000` tokens via `tiktoken` (`count_tokens` + `enforce_token_limit` truncating `catalyst_summary`); every decision must include an option leg (`ensure_options_in_decision` auto-injects `get_option_chain` if missing — hackathon requirement); natural-language instruction hook (`apply_instruction` for `conservative 0.5` / `aggressive 1.5` / `explain`) must be supported.

### 6.4 Risk Management

- The risk agent must enforce position size limits, portfolio exposure caps, and a daily drawdown circuit breaker.
- The circuit breaker must automatically pause the system when daily drawdown exceeds a configured threshold, for example negative 3 percent of total equity, and must require explicit operator resume via the CLI.
- The risk agent must be the only component that can block or resize a proposed trade. Rejection or adjustment must cite the rule that triggered it.
- Cash-settled index options near expiry must be handled per the settlement-lag mitigation described in section 11.

### 6.5 Execution

- The execution agent must submit only risk-validated orders via the Alpaca Trading API through a single throttled client.
- Rate limiting must target 25 requests per minute with a leaky-bucket model.
- Timeouts and rate-limit responses must be retried with exponential backoff and jitter.
- The paper environment must be used. Live trading must not be reachable from the submission configuration.

### 6.6 Reporting and Audit

- Every agent transition must append a structured JSON-line event to a persisted log.
- The reporting agent must be able to read that log and produce on demand a human-readable summary covering positions, trades, profit and loss, and the reasoning trail.
- The report command in the CLI must trigger this summary. Terminal scrollback alone is not sufficient as an audit trail.

### 6.7 CLI

- The interface must be an interactive REPL modeled after the Claude Code interaction pattern, not a one-shot script and not a web application.
- Natural-language input must be routed to the agent graph as an instruction.
- Slash commands must be supported for direct operations: status, positions, report, pause, and resume.
- Agent steps must stream live to the terminal while also being persisted to the log.

### 6.8 Alpaca Integration Compliance

- The agent must trade autonomously via the Trading API, not through manual order entry.
- The system must use at least one of the Alpaca MCP Server or Alpaca CLI as part of its workflow — **Phase 12 implements both**: `backend/tools/alpaca_cli_tool.py` (4 tools via `subprocess alpaca --json` + broker fallback) and `backend/tools/mcp_tools.py` + `backend/mcp/client.py` (4 tools via MCP bridge when `MCP_SERVER_URL`/`MCP_SERVER_COMMAND` set + broker fallback), wired into `backend/agents/research.py:_RESEARCH_TOOLS` (9 tools) and verified via `source: "alpaca_cli"` vs `"alpaca_cli_fallback"` / `"mcp"` vs `"mcp_fallback"` logs. Config template `backend/mcp/server_config.example.json` + `.env.example` `MCP_SERVER_URL`.
- Every strategy must incorporate options trading (`ensure_options_in_decision` + `fetch_option_chain`).

### 6.9 Evaluation Support

- Prompt iteration must be supported by DeepEval with pytest inside the Python workspace and by Promptfoo as a separate Node CLI that is not imported into the application.

---

## 7. Non-Functional Requirements

| Category | Requirement | Rationale |
| :--- | :--- | :--- |
| Rate discipline | No more than 25 Alpaca requests per minute sustained, with burst absorbed by the bucket | Preserve headroom below platform caps and avoid 429 storms |
| Prompt discipline | System prompts below 1,000 tokens | Control cost, latency, and behavioral drift |
| Resilience | Exponential backoff with jitter on timeouts and rate limits, and auto-pause on excessive drawdown | Keep autonomy safe under transient failures and adverse moves |
| Auditability | Every agent step persisted as a JSON-line event, reproducible report from the log | Provide judging evidence without a dashboard |
| Operability | Single-command start, explicit pause and resume, live stream plus on-demand report | Allow unattended running with minimal supervision |
| Secrets handling | Credentials in gitignored environment file, never logged, never committed | Prevent credential leakage |
| Test focus | Highest coverage on broker handling and risk enforcement | Those are the places a silent bug costs paper money or fails the safety guardrail |

---

## 8. System Architecture Summary

The backend is layered to mirror the agent graph:

- **CLI layer** for interaction and live observation (`meanrev` via `pyproject.toml`).
- **Orchestration layer** for LangGraph sequencing and state validation (`StateGraph` + `InMemorySaver`).
- **Agent layer** for the five specialized nodes (research now 9 tools incl. `alpaca_cli_*` + `mcp_*` for Phase 12).
- **Broker layer** for throttled Alpaca connectivity (25/min Lua + `tenacity` + 30s timeout, `submit_order` market/limit/stop/options+crypto).
- **Data layer** for market data and news preparation plus indicator computation (OHLCV no-mock: `No data available...` + `BTC/ETH` derived).
- **Tool layer** 21 tools: 5 broker + 5 market (incl. `detect_arbitrage`) + 3 news + 4 Alpaca CLI + 4 MCP, all with broker fallback, wired via `backend/tools/__init__.py:TOOLS`.
- **MCP bridge** (`backend/mcp/`) for Phase 12 bonus — SSE or stdio MCP Server → LangChain tools.
- **Core layer** for configuration (`LLM_PROVIDER`, `LLM_MODEL_*`, `MCP_SERVER_URL`), domain models, and structured logging (`source` tags).

Persistence in v1 is flat-file JSON-line logs as the primary audit trail (`logs/broker.jsonl` + `.paused`). PostgreSQL with TimescaleDB is optional and deferred. Redis provides the token bucket and optional inter-stage publish and subscribe. No web server or frontend build chain exists in v1.

Data consumed by the system spans price and volume with multi-timeframe VWAP, technical indicators including RSI, MACD, EMA variants, Bollinger Bands, and ATR, macro catalyst data, sentiment metrics, and account and risk state including unrealized and realized profit and loss, margin usage, cash balance, and drawdown versus threshold.

For a detailed breakdown see Backend_Architecture.md and Agent_Architecture.md.

---

## 9. User Stories and Use Cases

**Story 1 — Start the agent for the scoring window.**
As an operator I want to start the agent against the dedicated submission paper account so that it begins trading autonomously from the market open on August 31.
Acceptance: The system loads credentials from the submission environment, authenticates to the paper API, validates connectivity, begins the research to execution loop, and begins streaming activity to the terminal while writing to the log.

**Story 2 — Observe live behavior.**
As an operator I want to see each agent step as it happens so that I can confirm the system is reasoning and acting.
Acceptance: Research, strategy, risk, and execution steps appear in order in the terminal and are simultaneously persisted as structured events.

**Story 3 — Check portfolio state.**
As an operator I want to ask for current positions and profit and loss through the CLI.
Acceptance: A slash command returns positions, cash, buying power, and unrealized and realized profit and loss without invoking the full agent chain.

**Story 4 — Intervene on risk.**
As an operator I want the system to pause itself when daily drawdown exceeds the threshold and to stay paused until I resume it.
Acceptance: When the threshold is breached the system stops submitting orders, marks itself paused, logs the rule that triggered, and only resumes after an explicit resume command.

**Story 5 — Understand a trade.**
As an operator or judge I want a readable summary of why a trade was taken.
Acceptance: The report command produces a narrative that links the catalyst and sentiment, the technical evidence, the risk verdict, and the execution outcome, all derived from the persisted log.

**Story 6 — Adjust posture with natural language.**
As an operator I want to tell the agent to become more conservative today without editing code.
Acceptance: A natural-language instruction is forwarded to the graph and reflected in subsequent strategy and risk behavior, with the instruction and its effect visible in the log.

---

## 10. CLI Specification

- **Mode:** Interactive REPL that remains open for the lifetime of the process.
- **Natural-language input:** Forwarded to the LangGraph agent as a behavioral instruction. Examples include requesting a more conservative stance or asking for an explanation of the last trade.
- **Slash commands:** Handled directly. Required commands are status, positions, report, pause, and resume. Additional commands may be added without changing the agent graph.
- **Output:** Rich live rendering of streaming agent steps and a formatted on-demand report view.
- **Constraint:** The CLI is the only user-facing surface. No web frontend or mobile surface exists.

---

## 11. Risk, Safeguards, and Platform Awareness

- Deterministic position limits and exposure caps are enforced on every proposed trade.
- The daily drawdown circuit breaker auto-pauses the system on excessive loss and requires manual resume.
- All Alpaca calls are throttled through a single limiter and retried with backoff and jitter.
- System prompts are capped to avoid behavioral drift under long contexts.

**Cash-settled index options settlement lag:** Cash-settled index options such as SPXW and XSP settle through an overnight cash journal entry. Community testing on Alpaca paper accounts has shown that expiry-day settlement may not appear in end-of-day equity on expiry day and may not post until the following morning, sometimes as late as around 10:00 a.m. Eastern. The system mitigates this by preferring to close such positions before expiry so that profit and loss is realized immediately, by not treating the raw equity reading on the morning after an expiration as authoritative without adjustment, and by distinguishing in logs and reports between a closed position and a position held to settlement.

---

## 12. Data and Model Specification

- **Research model:** Claude 3.5 Sonnet via OpenRouter / Groq / Modal for macro narrative, catalyst tracking, sentiment, and regime classification.
- **Strategy model:** GPT-4o via OpenRouter / Groq / Modal for signal synthesis and trade parameter generation.
- **LLM Provider Gateway:** OpenRouter (unified gateway for Claude + GPT-4o), Groq (fast inference), or Modal (serverless GPU for hosted models) — selected via LLM_PROVIDER env. Direct Anthropic/OpenAI keys remain fallback.
- **Risk in v1:** Rule-based engine with explicit thresholds. No learned model.
- **Risk in v2:** XGBoost or LightGBM trained on simulated portfolio paths to estimate Value at Risk in real time with a sub-10-millisecond target, replacing the rule engine when data and time allow.
- All prompts are subject to regression testing via DeepEval and comparative evaluation via Promptfoo.

---

## 13. Submission and Compliance Checklist

- Fresh paper account created with exactly 100,000 dollars, never used for prior testing.
- Environment configured from that account key and secret using the committed template; secrets remain gitignored.
- Agent live and trading from that account starting Monday, August 31, 9:30 a.m. Eastern.
- Full pipeline dry-run completed against the new account before the scoring window to surface auth or configuration issues.
- Autonomous trading via the Trading API demonstrated, with MCP Server or CLI usage documented.
- Options trading present in the strategy and visible in the execution log.
- Account identifier included in the submission so judges can pull profit and loss.
- One-page write-up prepared covering AI logic including models, features, and decision flow, risk gates including position limits, stop loss, Greeks handling, and drawdown control, and Alpaca infrastructure including endpoints, MCP or CLI tools, and paper account handling.
- Social posts if pursuing the bonus: up to five posts on X or LinkedIn tagging the organizers and Alpaca.

---

## 14. Milestones and Timeline

| Date | Milestone |
| :--- | :--- |
| Fri Aug 28, 11:00 a.m. ET | Hackathon kick-off. Confirm Alpaca credentials, MCP or CLI access, and market data availability. |
| Fri Aug 28 – Sun Aug 30 | Build and stabilize the five-agent graph, CLI, broker throttling, risk thresholds, and structured logging. Run against the development paper account. |
| Sun Aug 30 | Create the new dedicated submission paper account with 100,000 dollars. Configure the submission environment. |
| Sun Aug 30 – Mon Aug 31 morning | Dry-run the full pipeline end to end against the submission account. Fix auth, rate limit, or prompt issues outside the scoring window. |
| Mon Aug 31, 9:30 a.m. ET | Scoring window opens. Agent runs live from the submission account. |
| Mon Aug 31 – Fri Sep 4 | Operate the system, monitor via CLI, respond to circuit breaker pauses, generate reports, collect evidence for the write-up and demo. |
| Fri Sep 4, 11:00 a.m. ET | Scoring window closes. Freeze the account, generate the final report from the log, and submit the write-up, account identifier, and demo materials. |

---

## 15. Success Metrics and Judging Alignment

| Judging Criterion | How This Product Addresses It |
| :--- | :--- |
| Profit and loss performance | Real paper profit and loss during the scoring window on the dedicated account, with discipline from risk gates and options-aware handling of settlement timing |
| Technology implementation | Graph-based autonomous agents via Trading API plus MCP or CLI usage plus options incorporation |
| Creativity and originality | Distinct research and strategy model pairing, deterministic safety design, and log-derived reporting as a dashboard replacement |
| Presentation and execution | Clear one-page write-up plus a reproducible report artifact that narrates the full reasoning chain |
| Social engagement bonus | Up to five tagged posts demonstrating the build and live results |

Profit and loss is necessary but not sufficient. Autonomy, robustness, and clarity of the reasoning trail are equally weighted.

---

## 16. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
| :--- | :--- | :--- | :--- |
| Auth or config failure at the start of the scoring window | Medium | High — no profit and loss recorded | Dry-run against the submission account before August 31 and validate with a minimal order outside market hours |
| Rate limiting or timeouts under burst activity | Medium | Medium — missed or delayed orders | Single throttled client at 25 requests per minute with exponential backoff and jitter |
| Drawdown from correlated positions | Medium | High — scoring window loss | Hard position limits, exposure caps, and an auto-pause circuit breaker on daily drawdown |
| Settlement lag making end-of-day equity misleading for cash-settled index options | Medium | High for hold-to-expiry strategies | Close such positions before expiry; treat the morning-after equity reading as provisional; log the distinction |
| Prompt drift degrading strategy quality | Medium | Medium — poor trade quality | Keep prompts below 1,000 tokens and iterate under DeepEval and Promptfoo regression |
| Overbuilding a UI under time pressure | Low | Medium — lost build time | UI is explicitly out of scope; CLI plus log plus reporting agent is the complete observability stack |
| Flat-file log becoming hard to query over the full week | Low | Low — slower analysis | Reserve PostgreSQL with TimescaleDB as a v2 migration path without blocking v1 |

---

## 17. Deferred and v2 Roadmap

- **ML surrogate risk model:** Train an XGBoost or LightGBM model on simulated portfolio paths to estimate Value at Risk in real time, targeting sub-10-millisecond inference to replace the rule engine.
- **Relational persistence:** Adopt PostgreSQL with TimescaleDB for long-horizon historical bars and agent audit history when flat files are no longer convenient.
- **Static report renderer:** Generate a shareable static HTML report from the structured log for demo distribution without building a live dashboard.
- **Additional evaluation:** Expand DeepEval and Promptfoo coverage to include regime-specific prompt performance and multi-day consistency checks.

---

## 18. Open Decisions

| Decision | Current Stance | Options Still Open |
| :--- | :--- | :--- |
| Project name | TBD | drawdown and neurotrade are candidates |
| Exact drawdown threshold | Negative 3 percent daily as a starting point | Tune based on dry-run volatility observed before August 31 |
| Options coverage breadth | At least index and equity options; cash-settled handling is explicit | Scope of single-name options universe to be set by liquidity filter |
| Storage promotion | Flat-file logs in v1 | Promote to PostgreSQL with TimescaleDB if query patterns demand it |

---

## 19. References

- DOC.md — the living architecture and specifications source for this project
- LabLab.ai hackathon page and Discord
- Alpaca Getting Started, Trading API, Python SDK, CLI, and MCP Server documentation

---

## 20. Document History

- This PRD consolidates and expands the material in DOC.md, which previously served as the combined architecture and specifications source.
- Future edits to scope, thresholds, or agent responsibilities should update this PRD, DOC.md, and the two architecture documents together so that they remain consistent.

---

*This product trades a larger feature surface for a smaller set of guarantees that can be verified under hackathon constraints: autonomous execution, deterministic safety, throttled brokerage, and a complete account of why every trade was taken.*
