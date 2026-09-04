# Meanrev Backend – Autonomous AI Trading Agent Engine

> **Project:** Autonomous Multi-Agent Trading System — Alpaca AI Trading Agents Hackathon 2026 (LabLab.ai)  
> **Target:** 100% Unattended Autonomous Paper Trading ($100k Account)  
> **Key Technologies:** Python 3.12, LangChain, LangGraph, `alpaca-py==0.44.0`, APScheduler, OpenRouter / Groq / Modal  
> **Safety:** Deterministic Rule-Based Risk Engine, 25 req/min Leaky Bucket Rate Limiter, Daily Drawdown Circuit Breaker  

---

## Table of Contents

- [1. System Overview](#1-system-overview)
- [2. Multi-Agent Pipeline & Orchestration](#2-multi-agent-pipeline--orchestration)
- [3. Project Directory Structure](#3-project-directory-structure)
- [4. Environment Configuration](#4-environment-configuration)
- [5. Installation & Quickstart](#5-installation--quickstart)
- [6. Operating Modes](#6-operating-modes)
  - [6.1 Autonomous Scheduler Mode (`--scheduler`)](#61-autonomous-scheduler-mode---scheduler)
  - [6.2 Interactive CLI REPL Mode (`meanrev`)](#62-interactive-cli-repl-mode-meanrev)
  - [6.3 Broker REST API Surface (FastAPI)](#63-broker-rest-api-surface-fastapi)
- [7. Tool Surface (25 LangChain Tools)](#7-tool-surface-25-langchain-tools)
- [8. Risk Management & Execution Safeguards](#8-risk-management--execution-safeguards)
- [9. Testing & Quality Assurance](#9-testing--quality-assurance)
- [10. Related Documentation](#10-related-documentation)

---

## 1. System Overview

The **Meanrev Backend** is an autonomous multi-agent quantitative trading system built specifically for the Alpaca AI Trading Agents Hackathon. The backend orchestrates five specialized AI agents via **LangGraph**, executing deterministic risk checks, technical and macro analysis, and throttled order execution against Alpaca's paper trading brokerage.

### Key Architectural Pillars
- **Strict Autonomy:** Designed to run 100% unattended during market hours (`09:30 - 16:00 ET`) via `backend/scheduler/` with state recovery.
- **Deterministic Risk First:** AI models propose; deterministic Python rules decide. Position sizing, gross exposure, and drawdown circuit breakers cannot be overridden by LLM output.
- **Options Integration:** Guaranteed options consideration across trading strategies (`ensure_options_in_decision`).
- **No-Mock Policy:** Real market data and paper broker APIs only. Missing or closed market data returns explicit empty datasets (`No data available...`), never hallucinated bars.
- **Hackathon Compliance:** Dual support for both **Alpaca CLI** and **Alpaca MCP Server** tools with automatic broker fallback.

---

## 2. Multi-Agent Pipeline & Orchestration

The backend pipeline flows sequentially through five specialized agents sharing a unified state schema (`GraphState`):

```
┌────────────────────────────────────────────────────────┐
│               1. Market Research Agent                 │
│         (Claude 3.5 Sonnet / 9 LangChain Tools)        │
│       Macro Catalysts, Sentiment, Regime Detection     │
└───────────────────────────┬────────────────────────────┘
                            │ regime, sentiment_score, catalysts
                            ▼
┌────────────────────────────────────────────────────────┐
│                   2. Strategy Agent                    │
│            (GPT-4o / Prompt < 1,000 tokens)            │
│       ATR Sizing, Multi-Timeframe VWAP, Options Leg    │
└───────────────────────────┬────────────────────────────┘
                            │ proposed TradeDecision
                            ▼
┌────────────────────────────────────────────────────────┐
│             3. Risk Management Agent                   │
│        (Deterministic Rules Engine, Python)            │
│   15% Pos Limit, 60% Gross Exposure, 3% Breaker        │
└───────────────────────────┬────────────────────────────┘
                            │ RiskVerdict (approved / scaled / rejected)
              ┌─────────────┴─────────────┐
              │ Approved / Scaled         │ Rejected / Paused
              ▼                           ▼
┌───────────────────────────┐   ┌────────────────────────┐
│    4. Execution Agent     │   │   Order Terminated /   │
│ (Throttled Alpaca Client) │   │ Circuit Breaker Tripped│
│   Auto or HITL Approval   │   └────────────────────────┘
└─────────────┬─────────────┘
              │ execution confirmation & fill logs
              ▼
┌────────────────────────────────────────────────────────┐
│                  5. Reporting Agent                    │
│                 (GPT-4o-mini / CLI)                    │
│      Structured JSONL Log Ingestion → 5-Section Doc     │
└────────────────────────────────────────────────────────┘
```

### Agent Roles & Model Assignments

| Agent | Default Model Selector | Primary Tools & Inputs | Output Contract |
| :--- | :--- | :--- | :--- |
| **Market Research** | `anthropic/claude-3.5-sonnet` | 9 tools: 3 news/macro, 3 Alpaca CLI, 3 MCP | Sentiment vector, regime classification, catalyst notes |
| **Strategy** | `openai/gpt-4o` | Multi-timeframe OHLCV, VWAP, indicators, options chain | `TradeDecision` (action, symbol, qty, stop/target, options leg) |
| **Risk Management** | Deterministic (Python) | Account equity, open positions, drawdown tracking | `RiskVerdict` (approved, approved_scaled, rejected) |
| **Execution** | Deterministic / `alpaca-py` | `broker/client.submit_order` (25 req/min, tenacity) | Execution confirmation, order status, fill metrics |
| **Reporting** | `openai/gpt-4o-mini` | `logs/broker.jsonl` audit records | Human-readable 5-section markdown audit report |

---

## 3. Project Directory Structure

```
backend/
├── README.md                   # This document
├── .env.example                # Environment configuration template
├── agents/                     # Specialized LangChain agent definitions
│   ├── research.py             # Market Research agent (Claude 3.5 Sonnet + 9 tools)
│   ├── strategy.py             # Strategy generation agent (GPT-4o + ATR sizing)
│   ├── risk.py                 # Deterministic rule-based risk enforcement engine
│   ├── execution.py            # Throttled broker order execution (Auto vs HITL)
│   └── reporting.py            # Audit log summarizer and report exporter
├── graph/                      # LangGraph orchestration state machine
│   ├── state.py                # GraphState typed dictionary schema
│   └── build.py                # StateGraph wiring, nodes, edges, and checkpoints
├── broker/                     # Alpaca brokerage integration
│   ├── client.py               # Throttled Alpaca TradingClient wrapper
│   └── rate_limit.py           # 25 req/min leaky bucket limiter + exponential backoff
├── data/                       # Market data and news ingestion (no mocks)
│   ├── market.py               # OHLCV, VWAP (1m/5m/1h/1d), pandas-ta indicators, options
│   └── news.py                 # Alpaca news feed, macroeconomic catalyst calendar
├── tools/                      # 25 LangChain @tool wrappers
│   ├── broker_tools.py         # Account, positions, orders, clock, submit, cancel, modify
│   ├── market_tools.py         # OHLCV, market snapshots, options chain, arbitrage
│   ├── news_tools.py           # News feed, macro calendar, keyword extraction
│   ├── alpaca_cli_tool.py      # Subprocess wrappers for "alpaca --json" (Hackathon bonus)
│   ├── mcp_tools.py            # Model Context Protocol bridge tools (Hackathon bonus)
│   └── __init__.py             # Central TOOL exports (TOOLS, BROKER_TOOLS, etc.)
├── mcp/                        # MCP client integration
│   ├── client.py               # MCP bridge configuration and tool discovery
│   └── server_config.example.json # Configuration for npx @alpacahq/alpaca-mcp-server
├── scheduler/                  # Phase 12b autonomous execution loop
│   ├── runner.py               # Cron / interval ticker (APScheduler + asyncio fallback)
│   ├── market_hours.py         # Market clock verification with 60s TTL cache
│   └── state.py                # State persistence to logs/scheduler.json
├── cli/                        # Interactive operator REPL
│   ├── __main__.py             # Entrypoint for "meanrev" command
│   ├── repl.py                 # prompt_toolkit + rich streaming interface
│   └── commands.py             # Slash commands (/status, /positions, /report, etc.)
├── core/                       # Core configuration and domain primitives
│   ├── config.py               # pydantic-settings config reader (.env)
│   ├── logging.py              # Structured JSON-line logger with credential redaction
│   ├── models.py               # Domain models (TradeDecision, RiskVerdict, HTTP envelopes)
│   ├── system_prompt.py        # Central agent system prompt registry
│   └── utils.py                # Token counter, symbol normalizer, tool error handlers
├── app/                        # REST API service (FastAPI)
│   ├── main.py                 # FastAPI application instance & routing
│   └── routers/
│       ├── broker.py           # Active broker endpoints (/api/v1/*)
│       └── alpaca.py           # Deprecated legacy alias (/get_account)
└── logs/                       # Gitignored runtime logs & state files
    ├── broker.jsonl            # Authoritative audit log
    ├── scheduler.json          # Scheduler state checkpoint
    └── .paused                 # Circuit breaker active flag
```

---

## 4. Environment Configuration

Copy the template to initialize your local `.env`:

```bash
cp backend/.env.example backend/.env
```

### Essential Settings in `backend/.env`:

```ini
# 1. Alpaca Credentials (Paper Trading Only)
ALPACA_API_URL=https://paper-api.alpaca.markets
ALPACA_API_KEY=your_paper_key_here
ALPACA_API_SECRET=your_paper_secret_here

# 2. LLM Provider Gateway
LLM_PROVIDER=openrouter               # Options: openrouter | groq | modal
OPENROUTER_API_KEY=your_openrouter_key
GROQ_API_KEY=your_groq_key             # If LLM_PROVIDER=groq

# 3. Model Selectors (Compulsory)
LLM_MODEL_MARKET_RESEARCH=anthropic/claude-3.5-sonnet
LLM_MODEL_STRATEGY=openai/gpt-4o
LLM_MODEL_REPORTING=openai/gpt-4o-mini

# 4. Risk Thresholds (Deterministic Rules)
RISK_MAX_POSITION_PCT=0.15           # Max 15% equity per position
RISK_MAX_EXPOSURE_PCT=0.60           # Max 60% gross portfolio exposure
RISK_DAILY_DRAWDOWN_PCT=0.03         # 3% daily drawdown triggers circuit breaker

# 5. Execution Mode
EXECUTION_MODE=auto                  # "auto" (autonomous scoring) or "hitl" (human approval)
HITL_ENABLED=false

# 6. Autonomous Scheduler (Phase 12b)
SCHEDULER_ENABLED=true
SCHEDULER_INTERVAL_MIN=5             # Run every 5 minutes during open hours
SCHEDULER_THREAD_ID=scoring-0831
SCHEDULER_PROMPT="Conduct market research on SPY, evaluate options, and propose orders."

# 7. Hackathon Bonus: MCP Server (Optional)
MCP_SERVER_URL=                      # e.g., http://localhost:8001/sse
MCP_SERVER_COMMAND=npx @alpacahq/alpaca-mcp-server
```

---

## 5. Installation & Quickstart

```bash
# 1. Create and activate virtual environment
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Install CLI command in editable mode
pip install -e .
```

---

## 6. Operating Modes

### 6.1 Autonomous Scheduler Mode (`--scheduler`)
*Recommended for hackathon scoring window. Runs unattended, checks market hours, and cycles through the 5-agent pipeline every 5 minutes.*

```bash
# Start autonomous scheduler
meanrev --scheduler --thread-id scoring-0831

# Windows background launch:
start /B meanrev --scheduler --thread-id scoring-0831

# Test a single tick without waiting (dry-run, no live orders placed):
meanrev --scheduler --dry-run --once
```

### 6.2 Interactive CLI REPL Mode (`meanrev`)
*Provides full operator control, live streaming agent output, and manual trade inspection.*

```bash
# Start interactive REPL in auto mode:
meanrev --mode auto --thread-id scoring-0831 --symbol SPY

# Start interactive REPL in Human-in-the-Loop (HITL) mode:
meanrev --mode hitl --thread-id manual-test-01 --symbol AAPL
```

#### Supported Slash Commands:
| Command | Description |
| :--- | :--- |
| `/status` | Displays account balance, cash, buying power, and active risk breaker status |
| `/positions` | Shows all currently held stock and options positions with unrealized P&L |
| `/report [n] [file]` | Summarizes the last `n` events from `logs/broker.jsonl` and exports to Markdown |
| `/pause` | Manually engages the circuit breaker and blocks all outbound orders |
| `/resume` | Clears `logs/.paused` and restores normal order submission |
| `/help` | Displays available commands and usage instructions |

### 6.3 Broker REST API Surface (FastAPI)
*Exposes a read-only HTTP broker surface proxying Alpaca endpoints with rate limiting.*

```bash
# Run server from repo root:
uvicorn backend.app.main:app --reload --port 8000
```
- Interactive API Docs: `http://localhost:8000/docs`
- Account Endpoint: `http://localhost:8000/api/v1/account`
- Positions Endpoint: `http://localhost:8000/api/v1/positions`
- Orders Endpoint: `http://localhost:8000/api/v1/orders`
- Market Clock: `http://localhost:8000/api/v1/clock`

---

## 7. Tool Surface (25 LangChain Tools)

The backend provides 25 specialized LangChain `@tool` wrappers categorized into 7 functional sets:

| Category | Count | Tool Names | Source File | Description |
| :--- | :---: | :--- | :--- | :--- |
| **Broker (Read)** | 4 | `get_account`, `get_positions`, `get_orders`, `get_clock` | `tools/broker_tools.py` | Rate-limited (25/min) broker telemetry |
| **Broker (Write/HITL)** | 5 | `submit_order`, `set_stop_loss`, `modify_order`, `cancel_order`, `cancel_all_orders` | `tools/broker_tools.py` | Order management and stop placement (HITL protected) |
| **Market Data** | 5 | `get_ohlcv`, `get_market_snapshot`, `get_option_chain`, `align_timeframes_tool`, `detect_arbitrage` | `tools/market_tools.py` | Multi-timeframe VWAP, indicators, options chain |
| **News & Catalysts** | 3 | `fetch_news`, `get_macro_calendar`, `extract_keywords` | `tools/news_tools.py` | News sentiment and macroeconomic releases |
| **Alpaca CLI** | 4 | `alpaca_cli_account`, `alpaca_cli_positions`, `alpaca_cli_orders`, `alpaca_cli_clock` | `tools/alpaca_cli_tool.py` | Subprocess wrapper (`alpaca --json`) with fallback |
| **Alpaca MCP** | 4 | `mcp_get_account`, `mcp_get_positions`, `mcp_get_orders`, `mcp_get_clock` | `tools/mcp_tools.py` | Model Context Protocol bridge with fallback |

---

## 8. Risk Management & Execution Safeguards

1. **Deterministic Position Limit:** Any single asset position cannot exceed `RISK_MAX_POSITION_PCT` (default: 15%) of total portfolio equity. Larger orders are scaled down to limit or rejected.
2. **Gross Exposure Cap:** Total aggregate portfolio exposure cannot exceed `RISK_MAX_EXPOSURE_PCT` (default: 60%).
3. **Daily Drawdown Circuit Breaker:** If current equity falls below `RISK_DAILY_DRAWDOWN_PCT` (default: 3%) from peak equity, the system creates `logs/.paused`. All subsequent trade proposals are instantly rejected until `/resume` is called.
4. **Token Bucket Rate Limiter:** Enforces strict adherence to 25 requests per minute with exponential backoff and jitter (`tenacity`).
5. **SPXW / XSP Settlement Lag Guard:** Cash-settled index options are identified; contracts near expiry are prioritized for closing rather than holding to settlement to eliminate next-morning journal lag.

---

## 9. Testing & Quality Assurance

Run the comprehensive test suite:

```bash
# Run all unit and integration tests:
pytest

# Test risk management rules:
pytest tests/test_risk.py

# Test broker rate limiting and retry logic:
pytest tests/test_broker.py

# Test no-mock data policies:
pytest tests/test_data_no_mock.py

# Test scheduler loop and market hours guards:
pytest tests/test_scheduler.py

# Generate test coverage report:
pytest --cov=backend --cov-branch --cov-report=term-missing
```

---

## 10. Related Documentation

- [Documentation Index](../docs/README.md) – Central guide to all project documentation
- [Agent Architecture](../docs/Agent_Architecture.md) – In-depth LangGraph agent specifications
- [Backend Architecture](../docs/Backend_Architecture.md) – Detailed backend system design
- [API Reference](../docs/API_REFERENCE.md) – REST endpoints and tool schema catalog
- [How to Use](../docs/How_To_use.md) – Comprehensive operational CLI and execution handbook
- [Testing Strategy](../docs/TESTING.md) – Full test matrix and validation procedures
- [Product Requirements Document (PRD)](../docs/PRD.md) – Complete hackathon specifications and goals
