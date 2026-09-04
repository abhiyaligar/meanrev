# Meanrev Documentation Hub

Welcome to the comprehensive documentation suite for **Meanrev**, an autonomous multi-agent quantitative trading system developed for the **Alpaca AI Trading Agents Hackathon 2026** hosted on LabLab.ai.

Meanrev operates against Alpaca's paper brokerage API on a dedicated $100,000 account, executing autonomous research, strategy generation, risk checks, order execution, and reporting without human intervention.

---

## 📚 Documentation Catalog

| Document | Primary Audience | Description |
| :--- | :--- | :--- |
| [**Product Requirements Document (PRD.md)**](./PRD.md) | Judges, Product Managers | Complete hackathon requirements, scoring criteria, system scope, non-goals, and operational mandates. |
| [**Backend Architecture (Backend_Architecture.md)**](./Backend_Architecture.md) | Developers, Architects | 7-layer system design, technology stack, state machine flow, Redis rate limiting, and persistence model. |
| [**Agent Architecture (Agent_Architecture.md)**](./Agent_Architecture.md) | AI Engineers, Developers | Deep dive into the 5 specialized agents, model assignments, LangGraph orchestration, and prompt limits. |
| [**API Reference (API_REFERENCE.md)**](./API_REFERENCE.md) | Developers, Integrators | Complete specification of active `/api/v1` REST endpoints, all 25 LangChain tools, and error catalogs. |
| [**How To Use (How_To_use.md)**](./How_To_use.md) | Operators, Judges | Comprehensive operational runbook, CLI flag reference, slash commands, autonomous scheduler, and copy-paste commands. |
| [**Testing Strategy (TESTING.md)**](./TESTING.md) | QA, Developers | Full test matrix covering unit broker/risk tests, prompt regression (DeepEval), no-mock validation, and integration tests. |
| [**Backend Engine Guide (backend/README.md)**](../backend/README.md) | Developers, DevOps | Backend engine overview, installation steps, directory tree breakdown, and operating mode guide. |
| [**Test Suite Summary (Tests.md)**](../Tests.md) | Developers | Quick-reference breakdown of all ~40 test cases across risk, broker, data, scheduler, and graph. |
| [**Implementation Phases (PHASES.md)**](../PHASES.md) | All | Historical roadmap and completed phases from Phase 1 through Phase 12b (Autonomous Scheduler). |

---

## 🧭 Reading Pathways

Select the pathway tailored to your role or current task:

### 1. 🏆 For Hackathon Judges & Evaluators
*Evaluate autonomy, risk enforcement, options mandate, and Alpaca CLI / MCP usage.*
1. **Start with [PRD.md](./PRD.md):** Review the hackathon context, $100k account starting balance, scoring window, and evaluation rules.
2. **Review [Agent_Architecture.md](./Agent_Architecture.md) §11:** Verify compliance with the Alpaca CLI (`alpaca_cli_*`) and Alpaca MCP Server (`mcp_*`) bonus integrations.
3. **Inspect [How_To_use.md](./How_To_use.md):** See how to verify autonomy via `meanrev --scheduler` or audit trades via `/report`.
4. **Examine [Backend_Architecture.md](./Backend_Architecture.md) §10-11:** Inspect the deterministic risk gates and the cash-settled index options (SPXW/XSP) settlement-lag protections.

### 2. 🚀 For Operators (Scoring Window & Dry-Runs)
*Run the agent unattended or monitor paper trading live.*
1. **Quickstart:** Read [How_To_use.md](./How_To_use.md) for single-command `meanrev` execution.
2. **Autonomous Run:** Run `meanrev --scheduler --thread-id scoring-0831` during market hours.
3. **Live Commands:** Use `/status` to monitor equity, `/positions` for open legs, and `/report` for structured P&L analysis.
4. **Safety Control:** Use `/pause` and `/resume` to operate the circuit breaker.

### 3. 🛠️ For Developers & Contributors
*Understand the internals, extend tools, or run test suites.*
1. **System Overview:** Read [Backend_Architecture.md](./Backend_Architecture.md) for the 7-layer architecture and data flow.
2. **Agent Graph:** Read [Agent_Architecture.md](./Agent_Architecture.md) to inspect `GraphState`, the conditional risk branch, and token management.
3. **Tools & Endpoints:** Review [API_REFERENCE.md](./API_REFERENCE.md) for the 25 LangChain tools and `/api/v1/*` REST routes.
4. **Verification:** Follow [TESTING.md](./TESTING.md) and run `pytest --cov=backend` to validate changes against safety gates.

---

## ⚡ Core System Invariants

Every component and document in Meanrev adheres to these strict operating invariants:

```
                  ┌─────────────────────────────────────┐
                  │          CORE INVARIANTS            │
                  └──────────────────┬──────────────────┘
                                     │
     ┌───────────────────────────────┼───────────────────────────────┐
     ▼                               ▼                               ▼
┌──────────────┐             ┌──────────────┐                ┌──────────────┐
│  AUTONOMY    │             │ DETERMINISTIC│                │   NO-MOCK    │
│  EVERY 5 MIN │             │     RISK     │                │    POLICY    │
│ Unattended   │             │ Max 15% Pos  │                │ Real data or │
│ Market Hours │             │ Max 60% Gross│                │ clean empty  │
│   APScheduler│             │  3% Breaker  │                │ Never fakes  │
└──────────────┘             └──────────────┘                └──────────────┘
```

1. **Deterministic Risk Invariance:** Machine learning models propose trade decisions; deterministic Python code strictly disposes. LLMs cannot bypass position limits (15%), exposure caps (60%), or daily drawdown circuit breakers (3%).
2. **Options Inclusion Mandate:** Every strategy proposal includes an options leg (`ensure_options_in_decision`), meeting the core hackathon requirement.
3. **Strict No-Mock Policy:** Market and news APIs return real data or explicit empty indicators (`No data available...`). No synthetic bars or mock orders are ever produced.
4. **Rate Limit Headroom:** A central leaky-bucket rate limiter enforces a strict 25 requests/minute ceiling with exponential backoff and jitter, preventing 429 penalties.
5. **Settlement Lag Mitigation:** SPXW and XSP index options are flagged near expiry to prefer closing before the bell, preventing overnight paper cash-journal lag.

---

## 📁 Repository Directory Map

```
.
├── docs/                        # Complete project documentation suite
│   ├── README.md                # Documentation Hub (this file)
│   ├── PRD.md                   # Product Requirements Document
│   ├── Backend_Architecture.md  # Backend architecture & tech stack
│   ├── Agent_Architecture.md    # Multi-agent LangGraph specifications
│   ├── API_REFERENCE.md         # REST API & 25 LangChain tools reference
│   ├── How_To_use.md            # Operator CLI handbook & execution guide
│   └── TESTING.md               # Testing strategy & coverage guidelines
├── backend/                     # Backend Python package
│   ├── README.md                # Backend engine quickstart & architecture
│   ├── agents/                  # 5 LangGraph agent implementations
│   ├── broker/                  # Alpaca TradingClient & rate limiting
│   ├── data/                    # Market & news data loaders (no mocks)
│   ├── tools/                   # 25 LangChain tools (Broker, Market, CLI, MCP)
│   ├── scheduler/               # Autonomous ticker & market hours guard
│   ├── cli/                     # prompt_toolkit & rich interactive REPL
│   ├── core/                    # Config, models, logging, utils
│   └── app/                     # FastAPI service (/api/v1 endpoints)
├── tests/                       # Test suite (~40 automated tests)
├── reports/                     # Generated markdown audit reports
├── pyproject.toml               # Package build & CLI script configuration
└── requirements.txt             # Python dependencies
```
