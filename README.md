# Meanrev – Autonomous AI Trading Agent

[![Alpaca Hackathon](https://img.shields.io/badge/Alpaca_Hackathon-2026-blue)](https://lablab.ai/event/alpaca-ai-trading-agents-hackathon)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-brightgreen.svg)](https://python.org)
[![LangGraph](https://img.shields.io/badge/Orchestration-LangGraph-purple.svg)](https://github.com/langchain-ai/langgraph)
[![Alpaca-py](https://img.shields.io/badge/Broker-alpaca--py%200.44.0-yellow.svg)](https://github.com/alpacahq/alpaca-py)
[![Status](https://img.shields.io/badge/Phase-12b%20Scheduler%20Autonomous-success.svg)](./PHASES.md)

**Meanrev** is an autonomous, multi-agent AI quantitative trading system developed for the **Alpaca AI Trading Agents Hackathon 2026** (LabLab.ai). Built on Python 3.12, LangGraph, and Alpaca-py, Meanrev operates unattended against a dedicated $100,000 Alpaca paper trading account during the official scoring window.

The system combines qualitative market sentiment, multi-timeframe technical indicators, guaranteed options trading, deterministic risk controls, and automated rate-limited execution.

---

## 🚀 Quickstart (30 Seconds)

### 1. Installation
```bash
# Clone and enter directory
git clone <repo-url>
cd atena

# Create and activate virtual environment
python -m venv venv
venv\Scripts\activate   # On Windows
# source venv/bin/activate  # On Linux/macOS

# Install dependencies and meanrev command
pip install -r requirements.txt
pip install -e .
```

### 2. Configure Environment
```bash
cp backend/.env.example backend/.env
# Edit backend/.env with your Alpaca paper keys and LLM API keys
```

### 3. Launch the Autonomous Agent
```bash
# Autonomous Scheduler (ticks every 5m during market hours, unattended for scoring):
meanrev --scheduler --thread-id scoring-0831

# OR Launch Interactive REPL with live streaming agent reasoning:
meanrev --mode auto --thread-id scoring-0831 --symbol SPY
```

---

## 🏛️ System Architecture

Meanrev sequences five specialized agents in a stateful **LangGraph** pipeline, enforcing deterministic safety rules before any order reaches Alpaca:

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

---

## ✨ Key Features & Capabilities

- **100% Autonomous Operation:** The Phase 12b scheduler (`meanrev --scheduler`) polls market hours (`09:30 - 16:00 ET`), evaluates trading conditions every 5 minutes, logs to `logs/broker.jsonl`, and checkpoints to `logs/scheduler.json`.
- **Deterministic Risk Engine:** Machine learning models propose trades, but hard-coded rules govern approvals:
  - **15% Max Position Limit:** No single holding can exceed 15% of total portfolio equity.
  - **60% Gross Exposure Cap:** Portfolio exposure is capped at 60% of equity.
  - **3% Daily Drawdown Circuit Breaker:** If portfolio drawdown exceeds 3% from peak equity, the system auto-pauses (`logs/.paused`) until explicitly resumed.
- **Mandatory Options Consideration:** Every strategy run evaluates equity options via `ensure_options_in_decision`, satisfying hackathon judging criteria.
- **Hackathon Bonus Integration:** Implements **both** the [Alpaca CLI](https://github.com/alpacahq/alpaca-cli) and [Alpaca MCP Server](https://github.com/alpacahq/alpaca-mcp-server) within the research agent (`tools/alpaca_cli_tool.py` and `tools/mcp_tools.py`) with automatic fallback to broker endpoints.
- **25 LangChain Tools:** Full coverage for account inspection, order modification, stop loss placement, multi-timeframe VWAP, indicators, macro calendar, and arbitrage detection.
- **Strict No-Mock Policy:** Operates exclusively against live/paper market feeds. Missing data triggers explicit `No data available...` empty datasets, preventing hallucinated decisions.
- **Auditability:** Every decision, catalyst, risk check, and order execution is appended to a structured JSONL audit log, enabling instant post-trade reporting via `/report`.

---

## 🕹️ CLI & Operating Modes

| Mode | Command | Description |
| :--- | :--- | :--- |
| **Autonomous Scheduler** | `meanrev --scheduler --thread-id scoring-0831` | Cycles every 5 min during market hours, 100% unattended. |
| **Interactive Auto REPL** | `meanrev --mode auto --symbol SPY` | Live REPL with streaming agent steps and auto order execution. |
| **Human-in-the-Loop REPL** | `meanrev --mode hitl --symbol AAPL` | Pauses at execution stage for interactive operator approval. |
| **Single-Tick Dry Run** | `meanrev --scheduler --dry-run --once` | Executes a single cycle without placing live paper orders. |
| **Broker REST API** | `uvicorn backend.app.main:app --port 8000` | Exposes `/api/v1/*` read endpoints and Swagger documentation. |

### Slash Commands in REPL:
- `/status` – View live cash, portfolio value, buying power, and circuit breaker status.
- `/positions` – Display all open equity and option positions with unrealized P&L.
- `/report 50 reports/report.md` – Generate an audit report from the last 50 log events.
- `/pause` / `/resume` – Manually toggle the circuit breaker.
- `/help` – Show help and command options.

---

## 📖 Complete Documentation Suite

All system documentation is centrally indexed in the [**docs/ Documentation Hub**](./docs/README.md):

- 📋 [**Product Requirements Document (docs/PRD.md)**](./docs/PRD.md) – Hackathon scope, evaluation rubric, and account constraints.
- 🏗️ [**Backend Architecture (docs/Backend_Architecture.md)**](./docs/Backend_Architecture.md) – 7-layer architecture, tech stack, and data pipelines.
- 🤖 [**Agent Architecture (docs/Agent_Architecture.md)**](./docs/Agent_Architecture.md) – Detailed agent specifications, prompt controls, and tool wiring.
- 🔌 [**API Reference (docs/API_REFERENCE.md)**](./docs/API_REFERENCE.md) – REST endpoints (`/api/v1/*`), 25 LangChain tools, and error catalog.
- 📖 [**How To Use (docs/How_To_use.md)**](./docs/How_To_use.md) – Comprehensive operational manual, CLI flags, and scheduler guide.
- 🧪 [**Testing Strategy (docs/TESTING.md)**](./docs/TESTING.md) – Test harnesses, coverage guidelines, and no-mock verification.
- ⚙️ [**Backend Engine Guide (backend/README.md)**](./backend/README.md) – Dedicated backend setup and module breakdown.
- 📊 [**Test Suite Breakdown (Tests.md)**](./Tests.md) – Overview of all ~40 automated unit and integration tests.
- 🗺️ [**Implementation Roadmap (PHASES.md)**](./PHASES.md) – Phase 1 through Phase 12b implementation status.

---

## 🧪 Testing

The system includes ~40 unit and integration tests covering risk invariants, broker rate limits, scheduler persistence, and no-mock data policies:

```bash
# Run all tests
pytest

# Test risk management rules
pytest tests/test_risk.py

# Test broker rate limiting
pytest tests/test_broker.py

# Test scheduler loop and market hours guards
pytest tests/test_scheduler.py

# Generate test coverage report
pytest --cov=backend --cov-branch --cov-report=term-missing
```

---

## ⚖️ Hackathon Compliance & Disclaimer

- **Dedicated Paper Account:** Meanrev runs against a fresh paper trading account starting with exactly $100,000.
- **Scoring Window:** Active trading Mon Aug 31, 09:30 a.m. ET → Fri Sep 4, 09:30 a.m. ET.
- **Paper Trading Only:** Meanrev is designed solely for paper trading evaluation. It does not trade real capital.
