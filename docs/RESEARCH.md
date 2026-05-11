# Masumi / Kodosumi Platform Analysis

> Research date: 2026-02-28
> Purpose: Evaluate whether Masumi should stick with current stack or pivot for scale

---

## Executive Summary

Kodosumi is a **Ray-based distributed runtime for interactive AI agents** — the only component in the Masumi stack that's open-source and handles agent execution. The Masumi ecosystem (Kodosumi runtime + Sokosumi marketplace + on-chain payment/identity) has real enterprise traction (BMW, Allianz, Lufthansa) and solid metrics (5,830 users, 66% free-to-paid conversion, 4,461 jobs executed).

**The verdict: The current stack is architecturally sound for its purpose but has concrete scaling bottlenecks that can be addressed without a full rewrite.** The Ray dependency is both Kodosumi's greatest strength (distributed compute) and its biggest operational burden. The critical missing piece is **durable execution guarantees** for paid agent workflows.

---

## 1. What Kodosumi Actually Is

Kodosumi is infrastructure for **interactive** agentic services — not just batch task execution. The killer feature is the pause/resume cycle:

```
Agent starts → makes decisions → PAUSES for user input (forms) →
user submits → agent RESUMES with context → repeat until done
```

### Core Architecture

```
┌─────────────────────────────────────────┐
│        ADMIN PANEL (Jinja2 + D3.js)     │
└──────────┬──────────────────────────────┘
           │
┌──────────▼──────────────────────────────┐
│   KODOSUMI SERVICE (Litestar + FastAPI) │
│   - FlowControl (register/list flows)   │
│   - ProxyControl (route to services)    │
│   - LockController (pause/resume)       │
│   - Auth (JWT + roles)                  │
│   - File upload/download                │
└──────────┬──────────────────────────────┘
           │
┌──────────▼──────────────────────────────┐
│        RAY DISTRIBUTED SYSTEM           │
│   - Actors: Execute user functions      │
│   - Serve: Multi-replica deployment     │
│   - Queues: Event streaming to spooler  │
│   - Detached actors: Registry, health   │
└──────────┬──────────────────────────────┘
           │
┌──────────▼──────────────────────────────┐
│        SPOOLER (Polling Loop)           │
│   - Discovers Ray actors                │
│   - Polls message queues (NOT push)     │
│   - Batches events → SQLite             │
└──────────┬──────────────────────────────┘
           │
┌──────────▼──────────────────────────────┐
│     STORAGE (SQLite + WAL mode)         │
│   - Admin DB (users, roles)             │
│   - Per-execution DBs (events)          │
└─────────────────────────────────────────┘
```

### Tech Stack

| Layer          | Technology              | Notes                                  |
|----------------|-------------------------|----------------------------------------|
| Language       | Python 3.10+            |                                        |
| Web            | FastAPI + Litestar      | Dual framework (unusual)               |
| Server         | Uvicorn (ASGI)          |                                        |
| Distributed    | Ray 2.48.0              | **Hard dependency — cannot run without**|
| Database       | SQLite + SQLAlchemy 2.0 | WAL mode for concurrency               |
| Validation     | Pydantic 2.11.7         |                                        |
| Observability  | OpenTelemetry, Prometheus|                                       |
| Auth           | python-jose, bcrypt     | JWT tokens                             |
| Frontend       | Beer CSS, D3.js         | Admin dashboard                        |
| CLI            | Click 8.x               | `koco` command                         |
| Dependencies   | **107 total**           | Reasonable for scope                   |

---

## 2. Identified Bottlenecks & Weaknesses

### Critical

| Issue | Impact | Difficulty to Fix |
|-------|--------|-------------------|
| **No durable execution** — if agent crashes mid-paid-job, work is lost | Money escrowed but job incomplete, no recovery | High (needs Temporal or equivalent) |
| **SQLite as primary DB** — single-file, no horizontal write scaling | Bottleneck at ~1000 concurrent users | Medium (migrate to PostgreSQL) |
| **Spooler uses polling, not streaming** — adds latency with many actors | Delayed event delivery under load | Medium (switch to Redis streams or Ray channels) |
| **Ray cluster required for ANY operation** — no lightweight dev mode | Developer friction, ops overhead | High (architectural) |

### Significant

| Issue | Impact | Difficulty to Fix |
|-------|--------|-------------------|
| **Dual web framework** (FastAPI + Litestar) — confusing, split ecosystem | Maintenance burden, learning curve | Medium (pick one) |
| **Per-user SQLite DBs** for executions — doesn't scale in cluster | File-system bound, no shared query | Medium (centralized DB) |
| **No configurable per-lock TTL** — fixed 3s timeouts | Can't handle varying agent response times | Low |
| **8 test files only** — modest coverage | Regression risk | Medium |
| **107 dependencies** — attack surface | Supply chain risk | Low-Medium |

### Minor

| Issue | Impact |
|-------|--------|
| Forms system has no conditional visibility or nested forms | UX limitations |
| No local-dev mode without Ray cluster | Developer onboarding friction |
| Endpoint discovery requires OpenAPI specs | Can't auto-detect simpler agents |
| No load testing or chaos engineering visible | Unknown breaking points |

---

## 3. The Masumi Ecosystem Context

### Current Traction

| Metric | Value |
|--------|-------|
| Total users | 5,830+ |
| Paying users | 2,158 (66% conversion) |
| Jobs executed | 4,461 |
| Live agents | 250+ |
| Enterprise clients | BMW, Allianz, Lufthansa, Telekom, Samsung, Generali |
| Team size | 35+ |
| Funding | 900K ADA Catalyst (Milestone 1 at 20%) |

### Three-Layer Architecture

1. **Masumi Protocol** (Cardano L1) — Payment escrow, agent identity (DIDs/NFTs), registry smart contracts
2. **Kodosumi** (Runtime) — Agent execution, interactive workflows, scaling via Ray
3. **Sokosumi** (Marketplace) — Agent discovery, task assignment, credit-based billing

### Known Platform Challenges

- **L1 tx fees too high for micropayments** — L2 solution (Hydra/rollups) planned but only 20% into Milestone 1
- **USDM stablecoin has limited liquidity** vs USDC/USDT
- **Small developer community** relative to Ethereum/Solana ecosystems
- **GitHub stars are modest** (39 max for Kodosumi) — limited open-source traction
- **Enterprise clients are primarily Serviceplan's own clients** — organic demand unclear

---

## 4. Alternative Stack Analysis

### Comparison Matrix

| Platform | Deploy | Auto-Scale | Cost | DX | Production | Marketplace Fit |
|----------|:------:|:----------:|:----:|:--:|:----------:|:---------------:|
| **Ray Serve (current)** | Medium | High | Good | Medium | High | Medium-High |
| **Temporal.io** | Medium | Good | Good | Good | Excellent | **High** |
| **Modal** | High | High | Medium | High | Good | Medium |
| **FastAPI + Celery** | Good | Medium | Excellent | Good | High | Medium |
| **BentoML** | High | Good | Good | High | Good | Medium |
| **LangGraph Cloud** | Good | Medium | Variable | Good | Good | Medium |
| **KServe/K8s** | Low | Excellent | Good | Low | Excellent | Medium |

### Key Findings

**Ray Serve strengths worth keeping:**
- Distributed GPU compute (fractional sharing, multi-node)
- Framework-agnostic (any Python framework works)
- Custom autoscaling policies
- Production-proven at OpenAI, Uber, Spotify scale

**Ray Serve weaknesses that matter:**
- Cold-start issue: after 24h+ idle, first request batch fails, ~10min recovery (Ray issue #59327)
- Operational complexity — teams report spending more time on infra than shipping
- Debugging remote actors is painful (v2.54 added Grafana dashboards to help)
- No durable execution — crashes lose state

**The gap Temporal fills:**
- Durable workflow execution — survives crashes, outages, long pauses
- Perfect for payment-linked workflows (escrowed job must complete or refund)
- Used at Netflix, Stripe, Snap
- OpenAI Agents SDK integration announced 2025
- Gold member of Agentic AI Foundation (co-founded by Anthropic, Block, OpenAI)

**The "just use FastAPI + Celery" argument:**
- Maximum control, zero lock-in, proven at any scale
- No Ray cluster overhead — Redis broker is simpler to operate
- But: you lose Ray's distributed GPU scheduling and actor model
- Makes sense for CPU-bound agent workloads (which most marketplace agents are)

---

## 5. Recommendations

### Option A: Evolve Current Stack (Recommended)

**Philosophy:** Keep Ray as the compute backbone, fix the concrete bottlenecks, add Temporal for durability.

| Change | Why | Effort |
|--------|-----|--------|
| **Add Temporal as orchestration layer** | Durable execution for paid jobs — the single biggest gap | High |
| **Replace SQLite with PostgreSQL** | Horizontal scaling, shared queries, proper migrations | Medium |
| **Replace spooler polling with Redis Streams** | Real-time event delivery, pub/sub | Medium |
| **Pick one web framework** (drop Litestar OR FastAPI) | Reduce confusion, simplify maintenance | Low-Medium |
| **Add lightweight dev mode** (local Ray or mock) | Developer onboarding | Medium |
| **Increase test coverage** (target 80%) | Regression safety for the above changes | Medium |

**Architecture after changes:**

```
                     ┌─────────────┐
                     │  Sokosumi   │ (Marketplace UI)
                     └──────┬──────┘
                            │
                     ┌──────▼──────┐
                     │  Temporal   │ (Workflow durability)
                     │  Workflows  │ (Ensures paid jobs complete)
                     └──────┬──────┘
                            │
                     ┌──────▼──────┐
                     │  Kodosumi   │ (Agent runtime)
                     │  (Ray)      │ (Execution, forms, scaling)
                     └──────┬──────┘
                            │
              ┌─────────────┼─────────────┐
              │             │             │
        ┌─────▼─────┐ ┌────▼────┐ ┌──────▼──────┐
        │ PostgreSQL │ │  Redis  │ │   Masumi    │
        │ (state)    │ │(events) │ │  (payments) │
        └───────────┘ └─────────┘ └─────────────┘
```

**Pros:** Minimal disruption, preserves existing integrations, addresses real bottlenecks
**Cons:** Still carries Ray operational complexity, incremental improvement not a leap

### Option B: Rebuild Runtime on FastAPI + Celery + Temporal

**Philosophy:** Drop Ray entirely. Most marketplace agents are CPU-bound LLM calls (the LLM provider handles GPU). Ray's distributed GPU scheduling is overkill.

| Component | Technology | Why |
|-----------|-----------|-----|
| API layer | FastAPI | Already partially used, massive ecosystem |
| Task queue | Celery + Redis | Proven, simple, scalable |
| Durability | Temporal | Workflow guarantees for paid jobs |
| Database | PostgreSQL | Production-grade from day one |
| Events | Redis Streams | Real-time, pub/sub |
| Scaling | K8s HPA or Docker Compose | Standard ops, no Ray cluster |

**Pros:** Dramatically simpler ops, lower barrier to entry, standard tooling
**Cons:** Lose Ray's GPU scheduling (matters if agents need GPU), significant rewrite effort, breaks existing Kodosumi integrations

### Option C: Modular Platform (Long-term Vision)

**Philosophy:** Make Kodosumi runtime-agnostic. Let agents run on ANY infrastructure (Ray, Modal, bare Docker) and standardize only the interface (MIP-003).

```
┌─────────────────────────────────────────────┐
│              MASUMI PROTOCOL                │
│     (Payment, Identity, Discovery)          │
└──────────────────┬──────────────────────────┘
                   │ MIP-003 Standard API
┌──────────────────▼──────────────────────────┐
│           AGENT GATEWAY                     │
│  - Routes requests to registered agents     │
│  - Temporal workflows for job durability    │
│  - Health checking, load balancing          │
│  - Metering & billing                       │
└───┬──────────┬──────────┬───────────────────┘
    │          │          │
┌───▼───┐ ┌───▼───┐ ┌───▼───┐
│ Ray   │ │ Docker│ │ Modal │  (Agent runs wherever)
│ Agent │ │ Agent │ │ Agent │
└───────┘ └───────┘ └───────┘
```

**Pros:** Maximum flexibility, agents bring their own compute, marketplace becomes infrastructure-agnostic
**Cons:** Highest effort, requires rethinking the entire developer experience

---

## 6. Strategic Assessment

### Should They Stick or Switch?

**Stick with Ray (evolve) if:**
- GPU-heavy agents are a key use case (model inference, fine-tuning)
- The team has Ray expertise and doesn't want to retrain
- Existing Kodosumi integrations (kodo-masu-connector) are critical path
- Time-to-market matters more than architectural purity

**Switch away from Ray if:**
- Most agents are CPU-bound LLM API calls (which they are today)
- Operational simplicity is a priority for the 35-person team
- They want a lower barrier for third-party agent developers
- They're willing to invest 3-6 months in a rebuild

### The Real Question

The question isn't "Ray vs not-Ray." It's: **What layer should Masumi own?**

| Layer | Current | Should Own? | Why |
|-------|---------|-------------|-----|
| Compute runtime | Kodosumi (Ray) | **No** — commoditize | Let agents run anywhere. Docker, Modal, bare metal. |
| Workflow durability | Missing | **Yes** — add Temporal | Critical for payment-linked execution |
| Agent gateway | Partial (proxy) | **Yes** — build out | Routing, health, metering, rate limiting |
| Discovery/registry | On-chain (Masumi) | **Yes** — core value | This is the moat |
| Payment/settlement | On-chain (Masumi) | **Yes** — core value | This is the moat |
| Marketplace UI | Sokosumi | **Yes** — core value | Enterprise entry point |

**The moat is not the runtime — it's the marketplace + payment + identity protocol.** Making the runtime pluggable (Option C) would let any agent developer participate regardless of their infrastructure choice, which accelerates ecosystem growth.

---

## 7. Competitive Landscape

### Direct Competitors

| Platform | Threat Level | Differentiation |
|----------|:------------:|-----------------|
| SingularityNET (AGIX) | Medium | Older, cross-chain, but less enterprise traction |
| Fetch.ai (FET/ASI) | Medium | Real-world deployments, Cosmos SDK, but different market |
| Autonolas (OLAS) | Low-Medium | DeFi-focused, co-owned agents |
| Nevermined | Low | Identity focus, no marketplace traction |

### Existential Threats

| Threat | Risk | Mitigation |
|--------|:----:|------------|
| **Stripe's Agentic Commerce Protocol** | High | If Stripe makes agent payments trivial in fiat, the crypto payment moat weakens |
| **Visa/Mastercard Agent Pay** | High | Same — traditional payment rails adding agent support |
| **OpenAI/Anthropic native agent marketplaces** | High | Platform risk if LLM providers build their own marketplaces |
| **Coinbase x402 on Base/Ethereum** | Medium | x402 on a larger ecosystem |
| **ASI Alliance mega-merger** | Medium | Fetch.ai + SingularityNET + Ocean Protocol combined |

### Masumi's Defensible Position

1. **Enterprise relationships** (via Serviceplan) — BMW, Allianz, Lufthansa already using it
2. **Cardano-native** — only real option in the Cardano ecosystem
3. **Full-stack** — only platform combining runtime + marketplace + payment + identity
4. **EU compliance** — GDPR + EU AI Act ready (matters for European enterprise)
5. **First-mover on x402 + Cardano** — smart-contract-aware payment protocol

---

## 8. Quick Wins (Immediate Value, Low Effort)

If advising the Masumi team today:

1. **Add PostgreSQL support** alongside SQLite (keep SQLite for dev, Postgres for prod)
2. **Replace spooler polling** with Redis pub/sub or Ray's built-in channels
3. **Consolidate to one web framework** (FastAPI — larger ecosystem, better known)
4. **Add a "lite mode"** that runs without Ray for local development
5. **Publish benchmark numbers** — "X agents, Y concurrent jobs, Z latency" builds credibility
6. **Increase test coverage** to 80%+ before any architectural changes
7. **Document the cold-start workaround** for Ray's 24h+ idle issue

---

## 9. Research Questions for Follow-Up

- [ ] What % of Sokosumi agents actually need GPU compute vs pure LLM API calls?
- [ ] What's the p99 latency for the spooler polling loop under load?
- [ ] Has anyone benchmarked Kodosumi with 100+ concurrent agent executions?
- [ ] What's the actual cost of running a Ray cluster vs equivalent Docker Compose setup?
- [ ] How many of the 250+ agents are using Kodosumi vs self-hosted?
- [ ] Is the L2 solution (Hydra/rollups) on track, and what's the realistic timeline?
- [ ] What's the agent developer onboarding time (hours from zero to deployed agent)?

---

## Sources

### Kodosumi
- [GitHub: masumi-network/kodosumi](https://github.com/masumi-network/kodosumi) (39 stars, Apache 2.0)
- [Kodosumi Docs](https://docs.kodosumi.io/)
- [Kodosumi Website](https://www.kodosumi.io/)

### Masumi Ecosystem
- [Masumi Network](https://www.masumi.network/)
- [Masumi Docs](https://docs.masumi.network/documentation)
- [Sokosumi Marketplace](https://www.sokosumi.com/)
- [Cardano Foundation Case Study](https://cardanofoundation.org/case-studies/masumi)
- [Catalyst Fund 14 Proposal](https://projectcatalyst.io/funds/14/cardano-use-cases-partners-and-products/masumi-ai-agent-network-20-by-serviceplan-group)

### Alternative Platforms
- [Temporal: Building Dynamic AI Agents](https://temporal.io/blog/of-course-you-can-build-dynamic-ai-agents-with-temporal)
- [Temporal OpenAI SDK Integration](https://temporal.io/blog/announcing-openai-agents-sdk-integration)
- [Ray Serve v2.54 Production Debugging](https://blockchain.news/news/ray-serve-grafana-dashboard-production-debugging)
- [Modal Labs $80M Raise](https://siliconangle.com/2025/09/29/modal-labs-raises-80m-simplify-cloud-ai-infrastructure-programmable-building-blocks/)
- [KServe Joins CNCF](https://www.cncf.io/blog/2025/11/11/kserve-becomes-a-cncf-incubating-project/)
- [LangGraph 1.0](https://blog.langchain.com/langchain-langgraph-1dot0/)
- [AI Agent Frameworks Comparison 2026](https://openagents.org/blog/posts/2026-02-23-open-source-ai-agent-frameworks-compared)

### Competitive Landscape
- [AI Agent Payments Landscape 2026](https://www.useproxy.ai/blog/ai-agent-payments-landscape-2026)
- [Agentic AI Infrastructure Landscape](https://medium.com/@vinniesmandava/the-agentic-ai-infrastructure-landscape-in-2025-2026-a-strategic-analysis-for-tool-builders-b0da8368aee2)
- [CoinGecko: AI Agent Payment Infrastructure](https://www.coingecko.com/learn/ai-agent-payment-infrastructure-crypto-and-big-tech)
