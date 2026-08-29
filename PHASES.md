# Implementation Phases

## Phase 1: Fix Documentation
- Update DOC.md: Fix ALPACA_SECRET_KEY → ALPACA_API_SECRET
- Delete duplicate: Remove docs/COMPLETE PRD.md (keep PRD.md)
- Update Backend_Architecture.md: Add questionary to CLI tech stack row
- Update API_REFERENCE.md: Remove app/ from File Map or mark deprecated
- Update .env.example: Add ANTHROPIC_API_KEY= and OPENAI_API_KEY=
- Update Agent_Architecture.md: Add MCP Server/CLI integration section
- Update Backend_Architecture.md: Add Redis config or remove from stack table
- Add Testing Strategy section to Backend_Architecture.md or new TESTING.md

## Phase 2: Core Config & Models
- Implement backend/core/config.py (pydantic-settings, load .env, support dev/submission profiles)
- Verify backend/core/models.py complete (TradeDecision, RiskVerdict, response envelopes, account/risk state fields)
- Verify backend/core/logging.py complete (JSON-line logger, redaction, broker_call helper)

## Phase 3: Broker Layer (Already Done - Verify)
- Verify backend/broker/client.py complete (throttled wrapper, all 4 methods: get_account, get_positions, get_orders, get_clock)
- Verify backend/broker/rate_limit.py complete (TokenBucket, backoff, retry logic, 25 req/min)

## Phase 4: Data Layer - Market & News
- Implement backend/data/market.py (OHLCV fetch, indicators: RSI, MACD, EMA 20/50/200, Bollinger, ATR, VWAP)
- Implement backend/data/news.py (news fetch, sentiment, macro catalysts: Fed speeches, NFP, CPI, earnings, benchmark revisions)

## Phase 4b: Data Layer - Options & Alignment
- Add options chain fetch + indicative Greeks to backend/data/market.py (required: every strategy uses options)
- Implement VWAP at 1m, 5m, 1h, 1d aggregates (multi-timeframe)
- Add time-aligned feature normalization (single timestamp index across price, volume, indicators, options)
- Cache/rate-limit market data calls (Alpaca free-tier limits)

## Phase 5: Graph State & Orchestration
- Implement backend/graph/state.py (LangGraph shared state schema with all agent fields: market snapshot, research output, strategy decision, risk verdict, execution result, reporting context)
- Implement backend/graph/build.py (wire 5 agents into LangGraph, conditional risk branch: approved → execution, rejected/scaled → stop or retry)

## Phase 6: Agents - Risk (Priority 1)
- Implement backend/agents/risk.py (deterministic rules: per-position size limits, portfolio exposure caps, daily drawdown circuit breaker)
- Track unrealized/realized P&L, margin usage, cash balance from account state
- Circuit breaker: auto-pause on drawdown > threshold (e.g., -3% equity), require explicit CLI resume
- Cash-settled index options (SPXW/XSP) handling: prefer close-before-expiry, flag settlement lag in verdict

## Phase 7: Agents - Execution (Priority 2)
- Implement backend/agents/execution.py (submit validated orders via broker/client.py, log confirmation)
- Support order types: market, limit, stop, options (single-leg, spreads)
- Handle partial fills, rejections, timeouts with retry via broker layer
- Log execution outcome with order_id, fill_price, filled_qty, latency

## Phase 8: Agents - Reporting
- Implement backend/agents/reporting.py (read logs/broker.jsonl, generate human-readable summary)

## Phase 8b: Reporting Narrative
- Structure report: catalyst summary → technical evidence → risk rule → execution result → P&L
- Output sections: positions, trades, realized/unrealized P&L, reasoning trail, risk events (circuit breaker triggers)
- Support CLI /report command and export to file for submission

## Phase 9: Agents - Strategy
- Implement backend/agents/strategy.py (GPT-4o, combine research + technicals + account state, output TradeDecision)
- Prompt < 1,000 tokens (enforce with token counter)
- Incorporate options in every decision (hackathon requirement)
- Sizing: notional or contracts, stop/target tied to ATR/volatility
- Natural-language instruction hook (modulate conservatism/aggression)

## Phase 10: Agents - Research
- Implement backend/agents/research.py (Claude 3.5 Sonnet, macro catalysts, sentiment, regime classification)
- Prompt < 1,000 tokens
- Output: sentiment vector, regime label, catalyst summary for strategy consumption
- Consume: news headlines, social velocity, macro calendar, prior regime for continuity

## Phase 11: CLI Layer
- Implement backend/cli/commands.py (slash commands: /status, /positions, /report, /pause, /resume)
- Implement backend/cli/repl.py (prompt_toolkit loop, natural language routing, rich live streaming of agent steps)
- Implement backend/cli/__main__.py (entrypoint, arg parsing, start REPL)

## Phase 11b: CLI → Graph Instruction Routing
- Implement natural language → graph instruction parser (e.g., "be more conservative" → adjust risk params, strategy temp)
- Add prompt token counter + enforcement (<1000 tokens) in agents/strategy.py, agents/research.py
- Live streaming: research → strategy → risk → execution steps rendered in terminal via rich.live

## Phase 12: Integration & Wiring
- Wire agents into graph/build.py with real imports (not stubs)
- Connect CLI to graph (natural language → graph, slash commands → direct handlers)
- Connect broker/client.py to execution agent
- Connect data/market.py + news.py to research/strategy agents
- Add MCP Server or Alpaca CLI usage in one agent (hackathon requirement)

## Phase 12b: Autonomous Scheduler
- Add continuous loop runner (APScheduler or asyncio) that triggers graph at market open, runs on interval (e.g., every 5 min during market hours)
- Handle market hours: pre-market, regular (9:30-16:00 ET), post-market, closed
- Ensure submission account starts trading Mon Aug 31 9:30 ET automatically
- Persist scheduler state (last run, next run) for resilience

## Phase 13: Testing
- Add tests for backend/broker/ (rate limiting, retry, error mapping, 429/5xx handling)
- Add tests for backend/agents/risk.py (circuit breaker, position limits, scaling, SPXW handling)
- Add DeepEval test file for prompt regression (strategy, research prompts)
- Add Promptfoo config for comparative prompt evaluation (Node CLI, separate from app)
- Add pytest.ini / pyproject.toml test config (async support, coverage)

## Phase 14: Cleanup
- Delete or archive backend/app/ (legacy FastAPI)
- Remove __pycache__ directories
- Verify .gitignore covers .env, logs/, __pycache__/, venv/

## Phase 15: Dry Run & Submission Prep
- Create fresh Alpaca paper account ($100k)
- Populate backend/.env with submission account keys + LLM keys
- Run full pipeline end-to-end against submission account
- Verify autonomous loop runs: research → strategy → risk → execution → logging
- Verify CLI commands work live
- Verify report command produces readable summary
- Verify drawdown circuit breaker triggers and pauses
- Verify rate limiter holds at 25 req/min
- Document account ID for submission
- Prepare one-page write-up (AI logic, risk gates, Alpaca integration)

## Phase 15b: Account Profiles & Bonus
- Add config profile for dev vs submission paper account (switch via env var)
- Document submission account ID in submission package
- (Optional) Draft 5 social posts tagging @Alpaca @LabLabAI for engagement bonus