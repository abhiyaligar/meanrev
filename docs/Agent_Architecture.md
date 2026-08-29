# Agent Architecture

**Project:** Autonomous AI Trading Agent — Alpaca AI Trading Agents Hackathon (LabLab.ai)  
**Scoring Window:** Mon Aug 31, 9:30 a.m. ET → Fri Sep 4, 9:30 a.m. ET  
**Orchestration:** LangChain plus LangGraph graph-based state machine  
**Interface:** CLI only — no web frontend. Alpaca guidance is explicit that a user interface is not required and evaluation focuses on the autonomous agent workflow and trading performance.

---

## 1. Purpose and Scope

This document describes the agentic architecture: the five specialized agents, how they are sequenced through a LangGraph state machine, what each agent consumes and produces, how prompts and models are assigned, how the CLI exposes control, and what safeguards and evaluation surround the system.

The original draft envisioned an XGBoost and LSTM surrogate risk model and a React dashboard. Both were deliberately removed for v1. Risk in v1 is deterministic and rule-based, and observability is provided through a rich CLI and a reporting agent that reads structured logs.

---

## 2. Overall Agentic Workflow

The system is a single linear pipeline with a conditional branch at the risk stage. Five agents are wired in sequence through LangGraph, sharing a common state object that accumulates context as it flows.

Flow in order:

1.  Market Research Agent produces a qualitative sentiment vector and macro regime classification from news, catalysts, and macro data.
2.  Strategy Agent consumes that sentiment together with technical indicators and price history to propose a trade decision with direction, sizing, and risk parameters.
3.  Risk Management Agent validates or adjusts the proposed trade against deterministic thresholds and either approves, scales down, or rejects it.
4.  Execution Agent submits the validated order through a throttled Alpaca client and returns an execution confirmation.
5.  Reporting Agent reads the persisted JSON-line log and produces a human-readable summary of positions, trades, profit and loss, and reasoning trail on demand.

Between each stage the graph persists the updated shared state so that downstream agents have full context and so that every transition is auditable through logging.

Market Research (Claude 3.5 Sonnet) → sentiment and macro regime → Strategy (GPT-4o) → proposed trade parameters → Risk Management (deterministic rules, v1) → validated or adjusted order or rejection → Execution (throttled Alpaca client) → execution confirmation → Reporting (log reader and summarizer)

Natural-language instructions from the CLI can influence the graph at the top, for example making the system more conservative, without changing agent code. Direct slash commands bypass the graph for operational inspection.

---

## 3. Orchestration — LangChain and LangGraph

### 3.1 Graph Model

- LangGraph is used as a stateful, graph-based orchestrator. Each agent is a node in the graph and the shared state is the edge payload.
- Transitions are sequential with one conditional branch: if the risk agent rejects or scales a trade, the execution agent receives the adjusted output or no order at all.
- State is validated at each transition so that malformed or incomplete agent output cannot propagate silently.

### 3.2 Shared State

The shared state is defined as a single schema that travels through the entire graph. Conceptually it carries:

- The current market snapshot and computed indicators.
- The research agent output including sentiment, catalyst notes, and regime label.
- The strategy agent output including trade direction, notional or contract sizing, stop and target levels, and rationale.
- The risk agent verdict including approval status, any sizing adjustment, and the rule that triggered the decision.
- The execution outcome including order identifier, fill information if available, and any throttling or retry note.
- A reporting context that accumulates the events needed for the later summary.

The schema is shared across agents so that no agent needs to re-derive context already produced upstream.

### 3.3 System Prompt Discipline

All strategy and research prompts are kept below 1,000 tokens. This constraint limits cost and latency and reduces prompt-induced behavior drift. Prompt changes are evaluated through DeepEval and Promptfoo rather than ad hoc editing.

---

## 4. Agent Specifications

### 4.1 Market Research Agent — Claude 3.5 Sonnet

**Role:** Macro and catalyst awareness, qualitative judgment.

**Consumes:**
- Macro catalyst feed including Federal Reserve speeches, Non-Farm Payrolls, Consumer Price Index prints, earnings announcements, and benchmark revisions.
- News headlines and related social velocity and keyword signals.
- Prior regime classification for continuity.

**Produces:**
- A qualitative sentiment vector describing directional bias and conviction.
- A macro regime classification that characterizes the current environment.
- A compact summary of the catalysts driving the view, suitable for the downstream strategy prompt.

**Behavioral scope:**
- Focuses on narrative and catalyst risk, not on generating orders.
- Explicitly avoids repeating technical indicator computation. Technicals are owned by the data and strategy layers.
- Output is designed to be concise so that the downstream strategy prompt remains within the token cap.

### 4.2 Strategy Agent — GPT-4o

**Role:** Signal synthesis and trade parameter generation.

**Consumes:**
- The research agent sentiment and regime output.
- Price and volume history with precomputed technical indicators: RSI, MACD, EMA at 20, 50, and 200 periods, Bollinger Bands, and Average True Range.
- Multi-timeframe VWAP aggregates at 1 minute, 5 minute, 1 hour, and 1 day.
- Account and risk context such as current exposure and proximity to the drawdown threshold.

**Produces:**
- A trade decision of buy, sell, or hold.
- Sizing expressed as notional or contract count depending on instrument.
- Stop and target levels tied to volatility and structure.
- A short rationale linking the decision to both the research narrative and the technical evidence.

**Behavioral scope:**
- Prompt is capped below 1,000 tokens and is the primary tuning surface for performance.
- Must incorporate options when trading, per hackathon requirements. A strategy that never considers options is non-compliant.
- Does not enforce risk limits itself. It proposes, the risk agent disposes.

### 4.3 Risk Management Agent — Deterministic Rules Engine, v1

**Role:** Enforce hard safety invariants. The only agent that can block or resize a trade.

**Consumes:**
- The strategy agent proposed trade.
- Current portfolio state including positions, margin usage, cash balance, and drawdown.
- Instrument characterization including whether an instrument is a cash-settled index option.

**Produces:**
- A risk verdict of approved, approved with adjusted size, or rejected.
- The specific rule or threshold that determined the verdict.
- The final order parameters that the execution agent is permitted to submit.

**Rules enforced in v1:**
- Per-position size limits.
- Portfolio exposure caps.
- The daily drawdown circuit breaker, for example an automatic pause if total equity drawdown exceeds negative 3 percent on the day. When triggered, the system transitions to a paused state that requires explicit CLI resume.

**Explicitly deferred:**
- The ML surrogate risk model that would approximate Value at Risk over simulated portfolio paths using XGBoost, LightGBM, or LSTM and deliver sub-10-millisecond estimates. This was in the original draft and is now a documented v2 extension. It is not a v1 dependency and no agent in v1 performs learned risk scoring.

### 4.4 Execution Agent — Throttled Alpaca Client

**Role:** Reliable, rate-aware order submission.

**Consumes:**
- The validated order from the risk agent.
- Throttling state and recent error history.

**Produces:**
- An execution confirmation or a structured failure describing why submission did not succeed.
- Log entries that capture throttling, retry, and fill outcome.

**Safeguards applied:**
- A leaky-bucket rate limiter targeting 25 requests per minute, providing headroom below the platform cap. All Alpaca calls are funneled through this limiter.
- Exponential backoff with jitter on timeouts and rate-limit responses.
- No direct Alpaca SDK usage outside this agent and its underlying broker wrapper.

**Options awareness:**
- When the strategy considers cash-settled index options near expiry, the execution layer prefers a close-before-expiry outcome. Holding such positions to settlement is discouraged because the resulting cash journal entry may not be visible until the next morning, obscuring true profit and loss.

### 4.5 Reporting Agent — Log Reader and Summarizer

**Role:** Turn the audit trail into a judging and debugging artifact.

**Consumes:**
- The structured JSON-line log that every upstream agent appends to.

**Produces:**
- On demand, a human-readable summary covering current positions, executed trades, profit and loss, and the reasoning trail that connects research to execution.
- A narrative that can be presented during demo and judging without building a live dashboard.

**Scope:**
- Reading is the primary operation. The agent does not mutate portfolio state.
- Its output is derived entirely from persisted events, so it is reproducible and does not depend on transient terminal scrollback.
- This agent is both the main debugging tool during the build and the main evidence of agent autonomy for evaluation.

---

## 5. Data Points and Feature Matrix

Each agent consumes a subset of the following. Ownership of computation stays in the data layer; agents consume normalized features.

| Category | Fields |
| :--- | :--- |
| Price and Volume | Open, High, Low, Close, Volume, VWAP at 1 minute, 5 minute, 1 hour, and 1 day aggregates |
| Technical Indicators | RSI, MACD, EMA at 20, 50, and 200, Bollinger Bands, Average True Range |
| Macro and Catalyst Data | Federal Reserve speeches, Non-Farm Payrolls, Consumer Price Index, earnings, benchmark revisions |
| Sentiment Metrics | News headline sentiment, social velocity, natural language keyword extraction |
| Account and Risk State | Unrealized and realized profit and loss, margin usage, cash balance, drawdown versus threshold |

The research agent is the primary consumer of the macro and sentiment rows. The strategy agent is the primary consumer of the price, volume, and technical rows combined with the research output. The risk agent is the primary consumer of the account and risk state rows.

---

## 6. CLI Design

The CLI is an interactive REPL modeled after the Claude Code interaction pattern. It is not a one-shot script and it is not a web application.

**Interaction modes:**
- Natural-language input is forwarded to the LangGraph agent as an instruction. Examples include asking the system to adopt a more conservative posture or to explain the rationale for the last trade.
- Slash commands are handled directly without invoking the full agent chain. Supported commands include status, positions, report, pause, and resume.

**Live streaming:**
- Agent steps stream to the terminal using rich live rendering in the order research, strategy, risk, execution. The same events are simultaneously written to the structured log underneath, so what is shown live is also what is persisted.

**Reporting surface:**
- The report command triggers the reporting agent, which reads the log and renders the after-the-fact summary. In the absence of a dashboard, this summary is the principal artifact for demo and judging.

**Design rationale:**
- A terminal-native interface is faster to build, easier to operate unattended, and fully aligned with the evaluation guidance that a user interface is not required.

---

## 7. Broker and Data Support

- The broker support consists of a thin Alpaca wrapper that normalizes configuration and authentication and a rate limiting module that implements the token bucket and backoff policy.
- The data support is split between market data handling that prepares OHLCV and indicator features and news handling that prepares sentiment and catalyst features.
- Agents never fetch raw market or news data themselves. They rely on the prepared features supplied through the shared graph state.

---

## 8. Core Support — Configuration, Logging, Domain Models

- Configuration is loaded from environment with a committed example and a gitignored secrets file. The system distinguishes a development paper account from the dedicated submission account that must start at 100,000 dollars.
- Logging is structured and JSON-line oriented. Every agent transition is recorded with enough context to reconstruct why a decision was made.
- Shared domain models define the shapes of trade decisions, risk verdicts, and report sections so that agents, graph, and logging agree on contracts.

---

## 9. Execution Safeguards and Platform Risk

### 9.1 Safeguards

- Rate limiter set to 25 requests per minute.
- System prompts capped below 1,000 tokens.
- Exponential backoff with jitter on timeouts and rate-limit responses.
- Circuit breaker that auto-pauses the system on excessive daily drawdown, for example negative 3 percent of total equity, requiring explicit resume.

### 9.2 Cash-Settled Index Options Settlement Lag

Cash-settled index options such as SPXW and XSP settle through an overnight cash journal entry with no exercise or assignment event. Community testing on Alpaca paper accounts has shown that expiry-day settlement may not appear in end-of-day equity on expiry day and may not post until the following morning, sometimes as late as around 10:00 a.m. Eastern. For a hold-to-expiry approach the entire final day profit and loss can therefore be invisible at the close.

**System mitigations:**
- The strategy agent should close cash-settled index option positions before expiry rather than holding to settlement, so that profit and loss is realized and reflected in equity immediately.
- The risk agent must not treat the raw equity reading on the morning after a zero-days-to-expiry expiration as authoritative without accounting for a possibly pending journal entry.
- Logs and reports distinguish a closed position with realized outcome from a held position awaiting settlement.

---

## 10. Evaluation

- **DeepEval with pytest** covers prompt regression and agent output quality. It runs inside the Python workspace and is the primary harness for iterating on prompts.
- **Promptfoo** runs as a separate Node CLI and is not imported into the application. It provides comparative prompt evaluation without coupling to the runtime.
- **Test priority:** Broker handling and the risk module receive the highest coverage because silent bugs in either area can cause incorrect orders or a failing drawdown guardrail. Other modules are tested opportunistically within the compressed timeline.

---

## 11. Deferred and v2 Extensions

- **ML surrogate risk model:** An XGBoost or LightGBM model trained on many simulated portfolio paths to estimate Value at Risk in real time, restoring the original sub-10-millisecond goal. This replaces the v1 deterministic rules engine when sufficient data and training time are available.
- **Relational persistence:** PostgreSQL with TimescaleDB for long-horizon historical bars and agent audit history if flat-file logs prove insufficient.
- **Static report renderer:** A generator that turns the structured log into a shareable static HTML report for demo purposes without building a live dashboard.

---

## 12. Operational Requirements for Submission

- Create a new Alpaca paper account with a starting balance of exactly 100,000 dollars for this submission. Do not reuse a testing account or the score will be ineligible.
- Load that account key and secret into the environment file using the provided template.
- Keep the agent live and trading from that account starting Monday, August 31, at 9:30 a.m. Eastern.
- Dry-run the full pipeline against the new account before that Monday so that authentication or configuration problems are found outside the scoring window.
- Free-tier market data with an indicative options feed is permitted for the hackathon. OPRA or Algo Trader Plus is optional.

---

## 13. Decision Log

- **Dropped the dashboard.** The React and Tailwind frontend and the FastAPI backend service were removed after Alpaca clarified that a user interface is not required and that evaluation centers on the autonomous workflow and profit and loss.
- **Deferred the ML risk surrogate.** The 100,000-path Monte Carlo plus XGBoost or LSTM estimator was replaced by enforceable deterministic thresholds for v1 to preserve reliability under the one-week build window.
- **Chose CLI plus log plus reporting agent as the observability stack.** Live streaming to the terminal satisfies operational needs, the JSON-line log provides the audit trail, and the reporting agent produces the judging artifact. Together they replace a dashboard without losing evidence of reasoning.

---

*The agent architecture optimizes for verifiable autonomy: a small number of clearly scoped agents, a single shared state contract, enforceable safety invariants, and a complete log of why every decision was made.*
