# Autonomous AI Trading Agent — System Architecture & Specifications

**Event:** Alpaca AI Trading Agents Hackathon (LabLab.ai)
**Hackathon window:** Fri Aug 28, 9:30 a.m. ET → Fri Sep 4, 9:30 a.m. ET
**Official scoring window:** Mon Aug 31, 9:30 a.m. ET → Fri Sep 4, 9:30 a.m. ET
**Judged on:** Total account equity change during the scoring window, plus the creativity, autonomy, and robustness of the agent workflow. P&L is important but not the sole factor.
**Project name:** *TBD* — candidates under consideration: `drawdown`, `neurotrade` (see §9)

---

## 1. Executive Overview & System Goals

Build an autonomous, multi-agent AI trading system that handles market research, strategy generation, risk validation, execution, and reporting — running largely unattended against a dedicated Alpaca paper account.

**Two decisions reshaped this project from the original draft:**

1. **No UI is required or wanted.** Alpaca's official guidance is explicit: *"A user interface is not required. We are primarily evaluating the autonomous agent workflow and its trading performance."* The React/Tailwind dashboard originally planned has been dropped in favor of a CLI.
2. **No ML surrogate risk model for now.** The original design used an XGBoost/LSTM model trained on 100k+ Monte Carlo paths to approximate VaR in real time. Given hackathon time constraints, risk management for v1 uses deterministic, rule-based checks (position limits, daily drawdown circuit breaker). The ML surrogate is a documented future extension, not a v1 dependency.

---

## 2. Tech Stack

| Layer | Component | Notes |
| :--- | :--- | :--- |
| **Interface** | CLI (Python, `prompt_toolkit` + `rich`) | Interactive REPL, Claude-Code-style: natural language or slash-commands, live-streamed agent activity. No web frontend. |
| **Agent Orchestration** | LangChain + LangGraph | Graph-based state machine; each node is one specialized agent. |
| **Market Research Model** | `LLM_MODEL_MARKET_RESEARCH` (default `anthropic/claude-3.5-sonnet`) via OpenRouter / Groq / Modal | Macro news, sentiment, catalyst tracking. Model ID read from `.env.example` → `.env` selector. |
| **Strategy & Decision Model** | `LLM_MODEL_STRATEGY` (default `openai/gpt-4o`) via OpenRouter / Groq / Modal | Signal synthesis, trade parameter generation. Model ID read from `.env.example` → `.env` selector. |
| **Reporting Model** | `LLM_MODEL_REPORTING` (default `openai/gpt-4o-mini`) via same gateway | Structured log → human-readable summary. Model ID read from `.env.example` selector. |
| **LLM Provider Gateway** | OpenRouter, Groq, or Modal | Provider abstraction — OpenRouter (unified gateway), Groq (fast inference), or Modal (serverless GPU). Selected via `LLM_PROVIDER` env; models selected via `LLM_MODEL_*` selectors in `.env.example`. Direct Anthropic/OpenAI keys remain fallback. |
| **Risk Management** | Deterministic rules engine (Python) | VaR/ML surrogate deferred — see §1. |
| **Broker Integration** | `alpaca-py` (official SDK) | Paper trading, throttled REST client. |
| **Database** | PostgreSQL (+ TimescaleDB, optional) | Only if persisting historical bars/logs beyond flat files — can be deferred; JSON-line logs are the primary audit trail for v1. |
| **Caching / Rate Limiting** | Redis | Token bucket for the execution rate limiter; optional pub/sub between agent stages. |
| **Evaluation** | DeepEval (pytest-based) + Promptfoo (separate Node CLI, not imported into the app) | Prompt regression testing, agent output quality checks. |

**Explicitly removed from the original draft:** FastAPI backend and React+Tailwind frontend (no UI requirement — see §1), ML surrogate risk model (deferred — see §1).

---

## 3. Agentic Architecture & Workflow

```
        ┌─────────────────────────┐
        │  Market Research Agent  │  (Claude 3.5 Sonnet)
        └────────────┬────────────┘
                      │ sentiment, macro regime
                      ▼
        ┌─────────────────────────┐
        │     Strategy Agent      │  (GPT-4o)
        └────────────┬────────────┘
                      │ proposed trade parameters
                      ▼
        ┌─────────────────────────┐
        │  Risk Management Agent  │  (rule-based, v1)
        └────────────┬────────────┘
                      │ validated / adjusted order
                      ▼
        ┌─────────────────────────┐
        │     Execution Agent     │  (throttled Alpaca client)
        └────────────┬────────────┘
                      │ execution confirmation
                      ▼
        ┌─────────────────────────┐
        │    Reporting Agent      │  (structured logs → summary)
        └─────────────────────────┘
```

1. **Market Research Agent (`LLM_MODEL_MARKET_RESEARCH` via OpenRouter / Groq / Modal)** — monitors macro catalysts (Fed speeches, NFP, CPI), news/sentiment; outputs a qualitative sentiment vector and regime classification. Model ID defined in `.env.example` (default `anthropic/claude-3.5-sonnet`) and read at startup via `core/config.py`.
2. **Strategy Agent (`LLM_MODEL_STRATEGY` via OpenRouter / Groq / Modal)** — combines sentiment with technical signals (OHLCV, RSI, MACD, EMA, Bollinger, ATR) to produce Buy/Sell/Hold + size + stop/target. System prompt kept under 1,000 tokens. Model ID defined in `.env.example` (default `openai/gpt-4o`) and read at startup.
3. **Risk Management Agent (rule-based, v1)** — enforces position size limits, portfolio exposure caps, and the daily drawdown circuit breaker. Rejects or scales orders that violate thresholds. *(ML surrogate VaR model is a v2 extension — see §8.)*
4. **Execution Agent** — submits orders via `alpaca-py`; enforces a leaky-bucket rate limiter (25 req/min) with exponential backoff + jitter on timeouts/429s.
5. **Reporting Agent (`LLM_MODEL_REPORTING` via same gateway)** — reads the structured JSON-line log and produces a human-readable summary (positions, trades, P&L, reasoning trail) on demand via the CLI's `report` command using model ID defined in `.env.example` (default `openai/gpt-4o-mini`). This is both the debugging tool and the primary evidence of agent reasoning for judging. Model ID is selector-driven, not hardcoded.

---

## 4. CLI Design

Interactive REPL, not a one-shot script — modeled after the Claude Code interaction pattern.

- Natural-language input is routed to the LangGraph agent as an instruction (e.g. "go more conservative today," "explain your last trade").
- Slash-commands handle direct operations: `/status`, `/positions`, `/report`, `/pause`, `/resume`.
- Agent steps stream live to the terminal (`rich.live`) — research → strategy → risk → execution — while the same events are written to the structured log underneath.
- `report` renders the after-the-fact summary — the main artifact for demoing/judging, since no dashboard exists.

---

## 5. Backend File Architecture

Structured around the agent graph itself rather than generic feature folders, since the system is one pipeline with distinct stages:

```
project/
├── cli/
│   ├── __main__.py        # REPL entrypoint
│   ├── repl.py             # prompt_toolkit loop, command routing
│   └── commands.py         # /status, /report, /pause handlers
├── agents/
│   ├── research.py
│   ├── strategy.py
│   ├── risk.py
│   ├── execution.py
│   └── reporting.py
├── graph/
│   ├── state.py             # shared LangGraph state schema (pydantic)
│   └── build.py              # wires agents/ into the graph
├── broker/
│   ├── client.py             # thin alpaca-py wrapper
│   └── rate_limit.py        # token bucket / backoff
├── data/
│   ├── market.py              # OHLCV + indicators (pandas-ta)
│   └── news.py                 # sentiment/news fetching
├── core/
│   ├── config.py               # pydantic-settings, reads .env
│   ├── logging.py               # structured JSON-line logger
│   └── models.py                 # shared pydantic models (TradeDecision, RiskVerdict, etc.)
├── logs/                         # gitignored, JSON-line output
├── .env.example
├── .gitignore
└── pyproject.toml
```

Priority test coverage (not full coverage everywhere, given the timeline): `broker/` and `risk.py` — the two places a silent bug costs real (paper) money or lets the drawdown guardrail fail silently.

---

## 6. Data Points & Feature Matrix

| Category | Fields |
| :--- | :--- |
| **Price & Volume (OHLCV)** | Open, High, Low, Close, Volume, VWAP (1m/5m/1h/1d aggregates) |
| **Technical Indicators** | RSI, MACD, EMA(20/50/200), Bollinger Bands, ATR |
| **Macro & Catalyst Data** | Fed speeches, NFP, CPI, earnings, benchmark revisions |
| **Sentiment Metrics** | News headline sentiment, social velocity, NLP keyword extraction |
| **Account & Risk State** | Unrealized/realized P&L, margin usage, cash balance, drawdown vs. threshold |

---

## 7. Execution Safeguards & Known Platform Risks

**Safeguards:**
- Leaky-bucket rate limiter targeting 25 req/min (headroom below Alpaca's cap)
- System prompts capped under 1,000 tokens
- Exponential backoff + jitter on timeouts/429s
- Circuit breaker: auto-pause if daily drawdown exceeds threshold (e.g. -3% total equity)

**Known platform risk — cash-settled index options (SPXW/XSP) overnight settlement lag:**
Community testing on Alpaca paper accounts observed that expiry-day cash settlement for cash-settled index options (no exercise/assignment event — just an overnight cash journal entry) does **not** appear in end-of-day equity on expiry day. It posts the following morning, sometimes not until ~10:00 a.m. ET. For a hold-to-expiry strategy, this can be the entire final day's P&L. **Mitigation for this system:** if the Strategy Agent ever considers cash-settled index options into expiry, close the position before expiry rather than holding to settlement — a closed position's P&L is realized and reflected in equity immediately, with no lag. Also don't let the Risk Agent's drawdown check trust equity readings on the morning following any 0DTE expiry without accounting for this possible lag.

---

## 8. Deferred / v2 Ideas

- **ML surrogate risk model** (XGBoost/LightGBM trained on simulated portfolio paths) to replace the v1 rule-based risk engine, restoring the original sub-10ms VaR estimation goal.
- **PostgreSQL/TimescaleDB persistence** for historical bars and full agent logs, if flat-file JSON logs prove insufficient.
- **After-the-fact HTML report renderer** — turns the structured log into a shareable static report for demo purposes, without building a live dashboard.

---

## 9. Required Submission Setup

- Create a **new** Alpaca paper account (starting balance $100,000) dedicated to this submission — do not reuse a testing account.
- Populate `.env` with that account's `ALPACA_API_KEY` / `ALPACA_API_SECRET` (see `.env.example`).
- Agent must be live and trading from that account starting Monday Aug 31, 9:30 a.m. ET.
- Dry-run the full pipeline against the new account before Monday so auth/config issues surface early, not during the scoring window.
- Free-tier market data (indicative options feed) is permitted; OPRA/Algo Trader Plus is optional, not required.

---

*Living document — update as the build progresses.*