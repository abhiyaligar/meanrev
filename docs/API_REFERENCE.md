# API Reference

**Project:** Autonomous AI Trading Agent — Alpaca AI Trading Agents Hackathon (LabLab.ai)  
**Version:** v1 — Hackathon Scoring Window Mon Aug 31, 9:30 a.m. ET → Fri Sep 4, 9:30 a.m. ET  
**Base URL:** None in v1 — No HTTP service is exposed

---

## 1. Status — No HTTP APIs in v1

There are no active HTTP APIs in v1.

Per DOC.md §1 and §2, the FastAPI backend and React frontend from the original draft were explicitly removed after Alpaca confirmed that a user interface is not required and that evaluation focuses on the autonomous agent workflow and trading performance. The system is CLI-only in v1 and all Alpaca interactions are performed through the throttled broker surface using alpaca-py, not through a self-hosted HTTP layer.

This document is intentionally empty of active endpoints. It exists as the placeholder where /api/v1 endpoints will be documented when a HTTP surface is specified.

If you are looking for platform APIs, see §3 for the external Alpaca surfaces that this system consumes. If you are looking for operator interaction, see the CLI surface in DOC.md §4 and Agent_Architecture.md §6.

---

## 2. Active Endpoints — None

### Base Path Convention (Reserved for Future)

When a HTTP surface is introduced, all endpoints will live under:

```
/api/v1
```

No endpoints are active under this prefix in v1.

| Method | Path | Status | Description | In Schema | Out Schema |
| :--- | :--- | :--- | :--- | :--- | :--- |
| — | — | — | — | — | — |

No rows. This table will be populated only when endpoints are specified and implemented. Do not infer endpoints from the agent or broker internals.

---

## 3. External APIs Consumed (Not Exposed By This System)

This system does not expose these. It calls them through the broker surface.

- **Alpaca Trading API** — Orders, account, positions, and market data. Accessed exclusively via the throttled alpaca-py wrapper at 25 req/min with exponential backoff and jitter. Paper trading only.
- **Alpaca MCP Server** — Claude/Cursor/VS Code bridge for paper environment. At least one of MCP Server or CLI must be used per hackathon requirements.
- **Alpaca CLI** — Terminal JSON output for account and order operations.

These are documented by Alpaca and are not part of this system's /api/v1 contract.

---

## 4. Future Endpoints — Reserved and Not Implemented

The following are reserved path shapes for a future HTTP surface. They are not implemented, not routed, and not tested in v1. They are listed only to reserve naming and to define the shape that will be used when specified.

All future endpoints will include request and response schemas with field names, types, required flags, and error shapes. Until specified, this section remains a reservation list.

| Method | Path | Purpose (Reserved) | In Schema (Draft) | Out Schema (Draft) | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GET | /api/v1/account | Return paper account state and buying power | None — reads server-side env | To be specified | Reserved — Not Implemented |
| GET | /api/v1/positions | Return open positions and unrealized P&L | None | To be specified | Reserved — Not Implemented |
| POST | /api/v1/orders | Submit a risk-validated order | To be specified | To be specified | Reserved — Not Implemented |
| GET | /api/v1/orders | List recent orders and fills | Query: to be specified | To be specified | Reserved — Not Implemented |
| POST | /api/v1/agent/pause | Pause the autonomous loop | To be specified | To be specified | Reserved — Not Implemented |
| POST | /api/v1/agent/resume | Resume the autonomous loop | To be specified | To be specified | Reserved — Not Implemented |
| GET | /api/v1/agent/status | Return loop state and last agent verdicts | None | To be specified | Reserved — Not Implemented |
| GET | /api/v1/report | Generate report from JSON-line log | Query: to be specified | To be specified | Reserved — Not Implemented |

No In Schema or Out Schema is finalized until an endpoint is specified for implementation. When an endpoint is activated, its row will move to §2 and will be documented with full schemas, examples, and error codes.

---

## 5. Schemas — None Active

No request or response schemas are active in v1 because no endpoints are active.

When an endpoint is specified, its schemas will be documented here with:

- Field name, type, required versus optional, and description
- Validation rules and limits
- Success response shape with status code
- Error response shape with status code and error catalog reference

---

## 6. Errors — None Active

No HTTP error codes are emitted by this system in v1 because no HTTP surface is exposed.

When a HTTP surface is introduced, errors will be documented per endpoint using a shared error shape and the existing internal catalog covering validation errors, risk rejections, risk scaling, transient broker errors, deterministic broker rejections, circuit-breaker pauses, and stale settlement reads.

---

## 7. How to Populate This Document

This document will be populated only when a HTTP endpoint is explicitly specified for build. To add an endpoint:

1. Move its row from §4 to §2
2. Fill Method, Path under /api/v1, Description, full In Schema, and full Out Schema
3. Add request and response examples
4. Add its error codes to §6

Until then, this reference remains empty by design.

---

*Source of truth for this decision: DOC.md §1 — No UI is required or wanted, DOC.md §2 — Explicitly removed FastAPI backend, §5 — Backend file architecture with no HTTP layer, and §9 — Submission setup requiring a dedicated paper account. This document will change only when a HTTP API is specified.*
