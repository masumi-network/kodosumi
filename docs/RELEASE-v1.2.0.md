# Kodosumi v1.2.0 — Reliability & Observability

## Release Summary

Version 1.2.0 closes the reliability and observability gaps that surfaced during the loki/odin cluster incidents of June 2026. The release is organized into two clusters: **A — Reliability** addresses scale-to-zero service discovery, HITL replica keep-alive, payment deadline enforcement, and spooler resilience; **B — Observability** introduces structured machine-parseable logs, correlation IDs, lifecycle heartbeat events, dependency and node health monitoring, boot/startup logging, and severity-level hygiene across the codebase. All work lives in `masumi-network/kodosumi`; infrastructure changes (systemd unit files, promtail/alloy pipeline stages, Ray Serve YAML templates) are included here as ready-made templates but are deployed through the separate `kodosumi_infra` repository via the standard pyinfra pipeline.

Incident root-cause analysis: `kodosumi_infra/docs/loki-stability-analysis-and-testplan.md`

---

## Scope & Boundaries

**In scope — kodosumi-core code changes:**
- New Python modules and helper functions in `kodosumi/`
- Configuration fields (all via `KODO_` env vars, `config.py`)
- Unit tests in `tests/` written by the implementing engineer
- Infra template snippets (systemd units, promtail YAML, .env additions) embedded in this doc as reference

**In scope — documented but deployed separately:**
- `environments/*/systemd/spooler.service` — Type=notify upgrade (D4)
- `environments/*/systemd/panel.service` — env var additions (D6, D11)
- `deployment/promtail/kodosumi.yaml` — Loki JSON pipeline stage (D12)
- Ray Serve YAML template changes — downscale_delay_s / min_replicas (D2 companion)

**Explicitly out of scope for v1.2.0:**
- L3 durable disk buffer for spooler (deferred to v1.3; see D5)
- Active hung-job enforcement / timeout kill (D9 follow-up ticket D9b)
- Redis memory monitoring via Ray Dashboard API (fragile schema; infra Prometheus instead)
- Secret redaction in structured logs (#80 depends on D12 landing first; placeholder only)
- Lock-resume/recovery for crashed replicas (D2 companion; separate ticket)
- `koco deploy` CLI (already removed; not referenced here)

---

## Dependency Graph & Implementation Order

The specs' declared dependencies and logical sequencing yield the following phased implementation order.

### Phase 0 — Enabler (must land first)
| Order | Spec | Ticket | Rationale |
|-------|------|--------|-----------|
| 1 | D12-format | #81 | Cross-cutting enabler. D8 (correlation), D9 (lifecycle), D11 (boot log) all produce structured events whose format D12 defines. Adopt early so subsequent PRs emit correct JSON from day one. |

### Phase 1 — Low-risk, high-leverage observability foundations
| Order | Spec | Ticket | Rationale |
|-------|------|--------|-----------|
| 2 | D7-severity | #79 | Pure log-level reclassification; zero logic change. Deploy alongside or immediately before D6 so new WARNINGs are not immediately drowned by access-log noise. |
| 3 | D6-accesslog | #71 | Noise reduction. Required before new WARNINGs from D7, D10, etc. become visible in production logs. No Ray coupling. |

### Phase 2 — Reliability cluster (Cluster A)
| Order | Spec | Ticket | Rationale |
|-------|------|--------|-----------|
| 4 | D1-scaletozero | #73 + #60 | Boot and availability correctness. No dependency on D12 or other v1.2.0 slices; safe to ship early. |
| 5 | D4-spoolerwatchdog | #69 | Systemd sd_notify. Self-contained; must ship **before** D5 (spooler drain) because D5's fail-loud guard relies on a healthy watchdog loop to actually kill a hung spooler. |
| 6 | D5-spoolerloss | #74 | Drain-before-kill + fail-loud guard. Depends on D4 being deployed (watchdog catches hangs introduced by the final-drain loop). |
| 7 | D3-paymentguard | #56 | HITL payment deadline guard (runtime guard only; **no** global `LOCK_EXPIRES` change). Self-contained; independent of D2. |
| — | ~~D2-hitlkeepalive~~ | ~~#62~~ | **REMOVED from v1.2.0** (2026-06-25). Not kodosumi-core: fix is `min_replicas:1` in the agent's expose `bootstrap` (`expose.db`, agent-dev). See OD-3. |

### Phase 3 — Observability cluster (Cluster B, D12-dependent)
| Order | Spec | Ticket | Rationale |
|-------|------|--------|-----------|
| 9 | D8-correlation | #70 + #80 | Correlation IDs (sumi_debug.log removal + structured start_job log). Depends on D12 being in the file handler so the correlation lines are machine-parseable. |
| 10 | D11-bootlog | #78 | Boot/startup logging. D12 dependency: BOOT SUMMARY line format deferred to D12 serializer. |
| 11 | D9-lifecycle | #76 | Lifecycle events + heartbeat. D12 dependency for envelope format; otherwise self-contained. |
| 12 | D10-dephealth | #77 + #75 | Node health aggregation. Self-contained from a code perspective; benefits from D6 noise reduction already being in place before new WARNING-on-dead-node lines appear. |
| 13 | D13-paymentobs | #54 | Payment timeout observability. No external dependencies; emits EVENT_PAYMENT events that land in the same structured log stream D12 enables. Can be developed in parallel with Phase 3. |

---

## Spec Sections

---

### D12-format — Structured Log Format

**Tickets:** #81
**Suggested branch:** `feat/81-structured-log-format`

#### Design Summary

Add `StructuredFormatter` (a `logging.Formatter` subclass emitting newline-delimited JSON) and a `slog()` helper to `kodosumi/log.py`. The formatter is attached only to the rotating file handler; the StreamHandler keeps the human-readable format for terminal use. Adoption is opt-in per handler and gated behind `KODO_APP_STRUCTURED_LOG` / `KODO_SPOOLER_STRUCTURED_LOG` (both default `True`). High-value call sites in `spooler.py`, `runner/reconcile.py`, and `sumi/control.py` are migrated in this ticket; the rest of the codebase adopts incrementally. The hardcoded `/srv/kodosumi/data/sumi_debug.log` writes in `sumi/control.py` are replaced with `logger.debug()` as part of this change.

#### Files Touched

| File | Change |
|------|--------|
| `kodosumi/log.py` | Add `StructuredFormatter`, `slog()`, `STRUCTURED_LOG_FORMAT`, `enable_structured_logging()` |
| `kodosumi/config.py` | Add `APP_STRUCTURED_LOG: bool = True`, `SPOOLER_STRUCTURED_LOG: bool = True` |
| `kodosumi/spooler.py` | Replace free-text logger calls in `retrieve()`, `save()`, `reconcile_payments()` with `slog()` |
| `kodosumi/runner/reconcile.py` | Replace logger calls in `_relaunch_resume()`, `reconcile_payment_job()` with `slog()` |
| `kodosumi/service/sumi/control.py` | Replace 8 `open('/srv/kodosumi/data/sumi_debug.log')` blocks with `logger.debug()` / `slog()` |
| `tests/test_structured_log.py` | New: unit tests for formatter output, field merging, secret-redaction placeholder, backward compat |

#### Key Code Anchors

- `kodosumi/log.py:116` — insert `StructuredFormatter` class after existing code
- `kodosumi/log.py:~130` — insert `slog()` helper function
- `kodosumi/log.py:54` — `_log_setup()` — add `fh.setFormatter(StructuredFormatter())` guard
- `kodosumi/spooler.py:99` — `save()` debug log → `slog()`
- `kodosumi/spooler.py:203` — `finished {fid}` log → `slog()`
- `kodosumi/spooler.py:150` — reconcile sweep log → `slog()`
- `kodosumi/runner/reconcile.py:289` — `reconcile: resumed` log → `slog()`
- `kodosumi/service/sumi/control.py:768` — first `sumi_debug.log` write → `logger.debug()` / `slog()`

#### Unit Tests

- `test_structured_formatter_emits_valid_json` — attach formatter to `MemoryHandler`, emit via `logger.info()`, parse with `json.loads()`, assert `ts/level/logger/event` keys present
- `test_structured_formatter_includes_slog_fields` — call `slog()` with `fid`, `agent`, `status`, `duration_ms`; assert all appear in JSON
- `test_structured_formatter_omits_none_fields` — call `slog()` without `fid`/`agent`; assert those keys absent (no null bloat)
- `test_structured_formatter_includes_exc_info` — raise exception, call `slog(..., exc_info=True)`; assert `exc` key in JSON
- `test_plain_formatter_not_broken` — StreamHandler still emits human-readable format (does not start with `{`)
- `test_slog_no_ray_dependency` — import `slog` in pure-stdlib test (no ray import); call with all fields; confirm zero Ray coupling
- `test_slog_with_existing_freetext_logger` — plain `Formatter` (not `StructuredFormatter`); `slog()` emits event string verbatim
- `test_settings_structured_log_flag` — `APP_STRUCTURED_LOG=True` by default; `KODO_APP_STRUCTURED_LOG=false` overrides
- `test_sumi_debug_log_removed` — import `sumi.control`, assert `open('/srv/kodosumi/data/sumi_debug.log')` is never called
- `test_spooler_save_emits_structured` — capture log records via `MemoryHandler`, parse JSON, assert `event='spooler.saved'` and correct `fid`

#### Staging Tests (odin)

1. Tail `app.log` after deploying; start a job via `/sumi/{expose}/start_job`; confirm JSON line with `"event":"sumi.start_job","fid":"...","agent":"...","status":"submitted"` appears within 5 seconds.
2. Confirm `/srv/kodosumi/data/sumi_debug.log` does not exist (or receives no new writes) post-deploy.
3. Run `logcli query '{job="kodosumi-app"} | json' --limit=5`; confirm `fid/event/agent` labels are indexed.
4. Run a full job end-to-end; tail `spooler.log`; confirm valid JSON lines with `event=spooler.finished` and correct `fid`.
5. Verify `koco serve` stdout shows only human-readable INFO lines (no JSON on stdout at default `INFO` level).
6. Check existing Grafana/Loki dashboards for regex-based panels that parse old plain-text format — document any that need updating.
7. Emit a log record containing a test API key; confirm no `ANTHROPIC_API_KEY` or `MASUMI_TOKEN` appears in the file handler output.

#### Infra Snippet (promtail/alloy pipeline — kodosumi_infra)

```yaml
# kodosumi_infra: deployment/environments/{odin,loki}/promtail/kodosumi.yaml
# Assumes log file scrape job already exists for /srv/kodosumi/data/app.log.
# Add a json pipeline stage AFTER the existing regex stage (or replace it).

scrape_configs:
  - job_name: kodosumi-app
    static_configs:
      - targets: [localhost]
        labels:
          job: kodosumi-app
          __path__: /srv/kodosumi/data/app.log

    pipeline_stages:
      # Structured JSON path (v1.2.0+)
      - match:
          selector: '{job="kodosumi-app"} |~ "^\\{"'
          stages:
            - json:
                expressions:
                  ts: ts
                  level: level
                  event: event
                  fid: fid
                  agent: agent
                  status: status
                  duration_ms: duration_ms
            - labels:
                level:
                event:
                fid:
                agent:
                status:
            - timestamp:
                source: ts
                format: RFC3339

      # Legacy plain-text path (pre-v1.2.0 — keep until all nodes updated)
      - match:
          selector: '{job="kodosumi-app"} !~ "^\\{"'
          stages:
            - regex:
                expression: '^(?P<level>\w+)\s+(?P<event>.+)$'
            - labels:
                level:

  # Identical block for spooler.log:
  - job_name: kodosumi-spooler
    static_configs:
      - targets: [localhost]
        labels:
          job: kodosumi-spooler
          __path__: /srv/kodosumi/data/spooler.log
    pipeline_stages:
      - match:
          selector: '{job="kodosumi-spooler"} |~ "^\\{"'
          stages:
            - json:
                expressions:
                  ts: ts
                  level: level
                  event: event
                  fid: fid
                  agent: agent
                  status: status
                  duration_ms: duration_ms
            - labels:
                level:
                event:
                fid:
                agent:
                status:
            - timestamp:
                source: ts
                format: RFC3339
```

#### Risk

**Medium-low.** The `StructuredFormatter` replaces only the rotating-file handler formatter; `StreamHandler` is untouched. The format change in `app.log` / `spooler.log` is a breaking change for any grep-based monitoring that relies on plain-text patterns — audit promtail config before deploying. The `sumi_debug.log` removal is safe (no monitoring tool consumed it). A bad formatter producing malformed JSON is mitigated by the unit test that asserts `json.loads()` succeeds on every emitted record.

#### Resolved Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| StructuredFormatter scope | File handler only; StreamHandler stays human-readable | Preserves operator UX in terminal; Loki reads the file |
| Dependency | stdlib `json` + `logging.Formatter`; no `structlog` | Zero new deps; no call-site churn (existing `logger.info(...)` unchanged) |
| `sumi_debug.log` | Replace with `logger.debug()` + `slog()` | Hardcoded prod path is a known smell; data moves to Loki at DEBUG level |
| `duration_ms` | Caller-provided | `slog()` is a leaf; callers already have start times |

---

### D1-scaletozero — Scale-to-Zero Probing

**Tickets:** #73 (boot step D writes "timeout" into `meta.state`) + #60 (availability endpoint wakes replicas)
**Suggested branch:** `fix/73-60-scale-to-zero-probing`

#### Design Summary

Boot step D currently calls a bare `HEAD` per endpoint; on scale-to-zero deployments this times out, and `merge_flow_with_meta` persists `"timeout"` as `ExposeMeta.state`, permanently removing the service from `/sumi/` discovery. Separately, the `/availability` endpoint fires a `GET` to Ray Serve which cold-starts every scaled-to-zero replica. Both probes are replaced with the existing `check_app_running()` (boot.py:453) which queries the Ray Dashboard control-plane API — replica-free. A `HEAD` fallback (no `GET`) is retained when the dashboard is unreachable.

#### Files Touched

| File | Change |
|------|--------|
| `kodosumi/service/expose/boot.py` | Replace `check_one` closure (boot.py:1912–1918) with dashboard-first probe; thread `ray_dashboard` through `run_boot_process` → `_step_retrieve_flows` → `check_all_flows` |
| `kodosumi/service/sumi/control.py` | Rewrite `_check_availability` (sumi/control.py:472–525): dashboard call instead of `GET`; fallback `HEAD` (not `GET`) when dashboard unavailable; optional 10s TTL in-process cache |

#### Key Code Anchors

- `boot.py:1912–1918` — `check_one` closure inside `check_all_flows`
- `boot.py:453` — `check_app_running()` (existing; reused)
- `boot.py:1850` — `check_flow_health()` (existing; demoted to fallback)
- `boot.py:2198/2203` — `merge_flow_with_meta` writes `state` — "timeout" eliminated here
- `sumi/control.py:355` — `_extract_alive_metas` gates on `meta.state == "alive"`
- `sumi/control.py:472–525` — `_check_availability` (rewritten)
- `sumi/control.py:509` — `client.get(endpoint_url)` — changed to `HEAD` in fallback
- `sumi/control.py:1072/1090` — handler call sites; add `ray_dashboard=state["settings"].RAY_DASHBOARD`

#### Unit Tests

- `test_check_all_flows_dashboard_returns_alive` — mock `check_app_running(valid=True)`; assert `FlowStatus.state == "alive"` and `check_flow_health` is NOT called
- `test_check_all_flows_dashboard_returns_dead` — mock `check_app_running(valid=False, message="UNHEALTHY")`; assert `state == "dead"`
- `test_check_all_flows_dashboard_not_found` — mock message contains "not found"; assert `state == "not-found"`
- `test_check_all_flows_fallback_no_dashboard` — `ray_dashboard=""`; mock `check_flow_health` returns `("alive", 200)`; assert `check_app_running` not called
- `test_check_all_flows_dashboard_connect_error` — mock `check_app_running` raises `httpx.ConnectError`; assert fallback to `check_flow_health` invoked
- `test_step_update_meta_state` — `FlowStatus(state="alive")` → `merge_flow_with_meta` → `ExposeMeta.state == "alive"`; confirm `"timeout"` never written
- `test_check_availability_dashboard_available` — temp sqlite DB with enabled expose; mock `check_app_running(valid=True)`; assert `status == "available"`; confirm no call to Ray Serve HTTP
- `test_check_availability_dashboard_unavailable` — mock `check_app_running(valid=False, message="DEPLOY_FAILED")`; assert `status == "unavailable"`
- `test_check_availability_fallback_uses_head_not_get` — `ray_dashboard=""`; mock `HTTPXClient`; assert `HEAD` method used (not `GET`)
- `test_check_availability_debounce_cache` — two calls within 10s; assert `check_app_running` called only once
- `test_check_availability_not_found` — expose absent in DB; assert `status == "unavailable"` and `check_app_running` not called

#### Staging Tests (odin)

1. Deploy expose with `min_replicas=0`. Boot. Confirm expose appears in `GET /sumi/` with `state="alive"`. (Previously absent or `state="timeout"`.)
2. With replicas at zero, call `GET /sumi/{expose}/{meta}/availability`. Confirm `{status: "available"}` and `ray serve status` shows `num_replicas` still 0.
3. Fire 10 concurrent `/availability` requests while replicas=0. All must return in under 1s; replica count stays 0.
4. With `min_replicas=1`, confirm normal warm path still works.
5. Point `KODO_RAY_DASHBOARD` to wrong port; restart panel; run boot and `/availability`. Verify fallback to `HEAD` probe (check audit.log). Restore and reboot.
6. Deploy a genuinely `UNHEALTHY` expose. Confirm step D writes `meta.state="dead"` and it does not appear in `GET /sumi/`.

#### Infra Snippet

None required for this spec.

#### Risk

**Medium-low.** The dashboard endpoint `/api/serve/applications/` is already exercised continuously by step B health polling. Primary risk: dashboard temporarily unreachable during step D — mitigated by `ConnectError` fallback to `HEAD`. Secondary risk: dashboard reports `RUNNING` while all replicas have crashed — same behaviour as current `HEAD` probe (would return 503). The `GET`→`HEAD` downgrade in the fallback changes semantics only if an endpoint returns 2xx to `HEAD` but error to `GET` (not the case for FastAPI/ServeAPI auto-mirrored routes). Module-level availability cache introduces shared mutable state; in multi-worker scenarios each worker populates independently within 10s.

#### Resolved Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| HEAD fallback in step D | Keep for `ray_dashboard=""` (empty = operator explicitly unconfigured) | Step C already parsed OpenAPI; dashboard RUNNING is sufficient; HEAD only as safety net |
| Availability cache | 10s TTL module-level dict (`_availability_cache: dict[str, tuple[float, AvailabilityResponse]]`) | Debounces Sokosumi burst; staleness cost < concurrent dashboard call cost |
| Dashboard unreachable state | `HEAD` as tiebreaker (fallback only on ConnectError from dashboard) | Limits blast radius of temporary dashboard outage without changing scale-to-zero behavior |

---

### D2-hitlkeepalive — HITL Replica Keep-Alive

> **❌ OUT OF SCOPE for v1.2.0 (decided 2026-06-25).** This is **not** a kodosumi-core fix. The autoscaling params live in the expose `bootstrap` (`expose.db`, authored by agent-dev). Resolution: set `min_replicas: 1` (+ `downscale_delay_s ≥ LOCK_EXPIRES`) for HITL-capable agents in their bootstrap — the replica never scales to zero, so the actor survives the lock wait. The short-ping code variant below is ineffective (see correction) and was rejected. #62 removed from the milestone and routed to agent-dev. Section retained for the handoff rationale only.

**Tickets:** #62
**Suggested branch:** _(n/a — agent-dev / expose.db config, not a kodosumi PR)_

#### Design Summary

During a HITL lock wait, `Runner.lock()` runs inside a Ray actor with zero ongoing Serve requests. Ray's autoscaler may evict the hosting replica after `downscale_delay_s` seconds, killing the runner and making `provide_input` permanently unroutable.

> **⚠ Owner correction (verified against Ray Serve autoscaling semantics, 2026-06-25):** Ray Serve scales on the *average* ongoing-requests-per-replica over a look-back window (~30s) vs `target_ongoing_requests`. A sub-millisecond 204 ping every 30s contributes ≈0 to that average → it does **not** prevent autoscale-down. **The short-ping mechanism described below is ineffective.** The real options are consolidated in **OD-3**. Primary recommendation: ensure HITL-capable agents deploy with `min_replicas:1` (replica never scales to zero → actor survives → no kill) — an infra/bootstrap companion change, no core code. A code keep-alive is only justified if scale-to-zero must be preserved for cost, and then it must be a *held-open* request (server sleeps ≈interval so one request stays continuously in-flight). Note: no keep-alive protects against an involuntary replica **crash** — only a lock-resume/recovery mechanism (deferred, separate ticket) does. The short-ping design below is retained only for reference.

#### Files Touched

| File | Change |
|------|--------|
| `kodosumi/runner/main.py` | Add `_hitl_keepalive()` coroutine; run concurrently inside `Runner.lock()` via `asyncio.ensure_future`; cancel in `try/finally` |
| `kodosumi/config.py` | Add `HITL_KEEPALIVE_INTERVAL: float = 30.0` |
| `kodosumi/serve.py` | Register `/_hitl_ping_/{fid}` GET route (204 No Content; checks actor existence via `ray.get_actor()`, returns 410 if not found) |
| `tests/test_hitl_keepalive.py` | New: unit tests for keepalive logic using mocked httpx and asyncio |

#### Key Code Anchors

- `runner/main.py:465–484` — `Runner.lock()` (keepalive integrated here)
- `runner/main.py:57` — `self.app_url` (loopback Serve URL stored on Runner)
- `serve.py:226` — `ServeAPI.add_features()` (add `/_hitl_ping_` route after line 342)
- `serve.py:342–347` — existing `/_lock_/` routes (new route registered after these)
- `config.py:158` — `LOCK_EXPIRES` (reference point for interval sizing)
- `runner/payment.py:39` — `httpx` already in use (no new dep)

#### Unit Tests

- `test_keepalive_pings_at_interval` — mock `httpx.AsyncClient.get`; run keepalive 0.2s with interval=0.05; assert 3–6 calls
- `test_keepalive_stops_on_stop_event` — set `stop_event` after first ping; assert total calls ≤ 2
- `test_keepalive_swallows_errors` — mock `get` to raise `httpx.RequestError` every call; assert no exception propagates
- `test_keepalive_stops_on_410_response` — mock returns `status_code=410`; assert `stop_event` set after first 410; no further calls
- `test_lock_cancels_keepalive_on_lease` — minimal Runner-like object; resolve lock after 0.05s; assert keepalive task is done
- `test_lock_cancels_keepalive_on_timeout` — `expires=time.monotonic()` (already expired); assert `TimeoutError` raised AND keepalive task done
- `test_lock_keepalive_disabled_when_interval_zero` — `HITL_KEEPALIVE_INTERVAL=0`; assert `httpx.AsyncClient.get` never called
- `test_hitl_ping_route_returns_204_when_actor_exists` — mock `ray.get_actor` succeeds; call handler; assert 204
- `test_hitl_ping_route_returns_410_when_actor_missing` — mock `ray.get_actor` raises; assert 410

#### Staging Tests (odin)

1. Deploy test agent that calls `tracer.lock()` and waits 15 min. Set `KODO_HITL_KEEPALIVE_INTERVAL=30`. Verify Ray dashboard shows `ongoing_requests > 0` and replica count stays ≥ 1 throughout.
2. While agent is in lock wait, POST `provide_input` via `/sumi/{expose}/{meta}/provide_input`. Verify job completes successfully, no "connection refused" in panel logs.
3. After lock resolves, verify via `ray list actors` that keepalive task is not still running (Runner actor gone after completion).
4. Set `KODO_HITL_KEEPALIVE_INTERVAL=0`; verify short lock waits (<5 min) still work; no ping calls in Serve access logs.
5. Kill Serve replica manually via `ray.kill()` on specific replica actor. Verify keepalive stops sending pings within 2 intervals after 410 is returned.

#### Infra Snippet

```ini
### kodosumi_infra companion change (separate PR, different repo)
### For all serve YAML templates — add to every HITL-capable deployment block:
###
###   autoscaling_config:
###     min_replicas: 1          # Never scale to zero — actor must survive between jobs
###     max_replicas: 4
###     target_ongoing_requests: 1
###     upscale_delay_s: 3
###     downscale_delay_s: 10800 # Must be >= KODO_LOCK_EXPIRES (default 10800s = 3h)
###
### systemd env for runner to pick up the interval
### File: environments/odin/systemd/ray-head.service
### [Service]
### Environment=KODO_HITL_KEEPALIVE_INTERVAL=30
###
### Or per-expose via expose bootstrap runtime_env.env_vars:
###   runtime_env:
###     env_vars:
###       KODO_HITL_KEEPALIVE_INTERVAL: "30"
```

#### Risk

**HIGH.** Behavioural change on the hot path of every HITL job. Three specific risks: (1) ping-storm: 100 concurrent locks = ~3 pings/s — acceptable but monitor; (2) keepalive masks crashed replica — old `app_url` is dead, pings get 404/ConnectError, are swallowed, lock expires after `LOCK_EXPIRES` — pre-existing failure mode, not worsened; (3) `asyncio.ensure_future` inside Ray actor — same event loop, safe, but `httpx` must have 5s timeout to avoid starving the lock spin loop. Mitigation: explicit `httpx timeout=5.0`; test 410 path on staging before shipping.

#### Resolved Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Ping target | `/_hitl_ping_/{fid}` (new no-op 204 route) | Clean, no noise in access logs; cheaper than re-using `/_lock_/` |
| Ping failure behaviour | Swallow silently | Lock() timeout/expiry handles cleanup independently; keepalive is best-effort |
| Default interval | 30s | 20× margin under Ray's 600s default downscale_delay_s; negligible overhead |
| 410 handling | `stop_event.set()` after first 410 | Actor gone → pings wasteful; lock expiry path cleans up |
| Cleanup | `try/finally` in `Runner.lock()` — always sets `stop_event` and cancels task | Covers normal resolution, timeout, and exception paths |
| infra YAML update | Deferred to companion infra ticket | Defense-in-depth; not blocking this slice |

---

### D3-paymentguard — HITL Payment Deadline Guard

**Tickets:** #56
**Suggested branch:** `fix/56-payment-deadline-guard`

#### Design Summary

`LOCK_EXPIRES` defaults to 3h, but Masumi's `submitResultTime` window defaults to 60 min. When a Sumi-paid HITL agent enters a lock, the on-chain deadline can expire before the human responds, triggering a refund.

> **⚠ Owner decision (final, see OD-4):** No pre-emptive guard, no `LOCK_EXPIRES` change. Business rule stays: HITL unresolved → refund. The only fix — bound a **paid** job's HITL wait by `submitResultTime`: when it passes, end the lock with a clear `payment window expired → refunded` status instead of waiting to 3h and hitting a 404. Mirrors `reconcile.py:72-73`. The `PaymentGuardError` design described below is **superseded** by this; section retained for context only.

#### Files Touched

| File | Change |
|------|--------|
| `kodosumi/config.py` | Add `LOCK_EXPIRES_SAFETY_MARGIN: float = 300.0`. **Do NOT change `LOCK_EXPIRES` (stays 10800).** |
| `kodosumi/runner/tracer.py` | Guard in `Tracer.lock()` reading `self._payment_deadline`; add `_payment_deadline: Optional[float]` attribute; no-op setter on `TracerMock` |
| `kodosumi/runner/main.py` | After `prepare()` resolves, call `tracer._set_payment_deadline(payment)` (converts `submitResultTime` ms → float epoch) |
| `kodosumi/runner/payment.py` | Add `PaymentGuardError(PaymentError)` exception class; confirm `_calculate_deadlines()` contract unchanged |
| `tests/test_paymentguard.py` | New: unit tests using TracerMock stubs, no Ray cluster |

#### Key Code Anchors

- `config.py:27` — `submit_result_by_time` default (60 min)
- `config.py:158` — `LOCK_EXPIRES` (change from 10800 to 3300)
- `tracer.py:150–163` — `Tracer.lock()` (guard inserted before `expires` computation)
- `tracer.py:154–155` — `max_seconds` derivation
- `tracer.py:173–193` — `TracerMock` (add `_payment_deadline` and no-op setter)
- `runner/main.py:355–361` — payment init and status transition (set `_payment_deadline` here)
- `runner/payment.py:61–80` — `_calculate_deadlines()` (unchanged; guard relies on its contract)
- `runner/payment.py:106` — deadlines stamped at `init_payment` call time

#### Unit Tests

- `test_guard_refuses_when_headroom_below_margin` — `_payment_deadline = now()+400`, `MARGIN=300`, `timeout=200` (headroom=200<300) → `PaymentGuardError`
- `test_guard_allows_when_headroom_sufficient` — `deadline=now()+1000`, `timeout=600` (headroom=400>300) → lock proceeds
- `test_guard_refuses_when_deadline_already_passed` — `_payment_deadline = now()-1` → `PaymentGuardError("submitResultTime already passed")`
- `test_no_guard_without_payment_deadline` — `_payment_deadline=None` (non-Sumi job) → lock proceeds regardless of timeout
- `test_calculate_deadlines_contract` — assert `submit_by_iso > pay_by_iso`, both parseable as UTC ISO
- `test_lock_expires_default_below_submit_result_default` — assert `Settings().LOCK_EXPIRES < MasumiConfig('N','u','t').submit_result_by_time`
- `test_payment_deadline_set_from_pay_data` — fake `pay_data={'submitResultTime': str(int((time.time()+3600)*1000))}`; assert `tracer._payment_deadline ≈ time.time()+3600`

#### Staging Tests (odin)

1. Deploy to odin; Sumi job with `submit_result_by_time=120`; do NOT provide input; after 120s confirm status=`error` with `PaymentGuardError` in error field.
2. Same job but `provide_input` within 60s; confirm job completes normally.
3. Panel-launched job with `tracer.lock()`; confirm no `PaymentGuardError` raised; `_payment_deadline` stays `None` for non-Sumi jobs.
4. Set `KODO_LOCK_EXPIRES=7200`, `KODO_LOCK_EXPIRES_SAFETY_MARGIN=600`; confirm guard fires when headroom < 600s.

#### Infra Snippet

None required.

#### Risk

**Low–medium.** The runtime guard fires only in the payment path (`_payment_deadline=None` for panel jobs) and raises a deterministic exception handled by the existing `STATUS_ERROR` path. **No global default change**, so non-payment panel jobs are unaffected. Residual: a paid HITL agent whose human genuinely needs longer than the configured `submit_result_by_time` now fails-loud at lock time — correct behaviour (better than a silent refund), but operators must set `submit_result_by_time ≥` expected lock duration for such agents.

#### Resolved Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Guard approach | Refuse (raise `PaymentGuardError`) | Only approach that guarantees on-chain contract is not violated; deterministic and testable |
| Deadline placement | On `Tracer` as instance attribute set by Runner after `prepare()` | Lock() call signature stays stable for existing agent code |
| `LOCK_EXPIRES` default | **Unchanged (10800s)** | The human HITL window must not be shortened to fit the payment deadline; alignment happens on the payment side via `submit_result_by_time` |

---

### D4-spoolerwatchdog — Spooler sd_notify / Watchdog

**Tickets:** #69
**Suggested branch:** `feat/69-spooler-sdnotify-watchdog`

#### Design Summary

Add a minimal inline `_sd_notify()` helper to `kodosumi/spooler.py` (12 lines, stdlib socket, no new dep). Send `READY=1` after the `SpoolerLock` actor is confirmed alive (spooler.py:226), and `WATCHDOG=1` at the top of every main-loop iteration (spooler.py:230). Update `spooler.service` in the infra repo from `Type=simple` to `Type=notify` with `WatchdogSec=120` and `MemoryMax=512M`.

#### Files Touched

| File | Change |
|------|--------|
| `kodosumi/spooler.py` | Add module-level `_sd_notify(msg: str) -> None`; call `_sd_notify("READY=1")` at line 226; call `_sd_notify("WATCHDOG=1")` at top of while-loop at line 230 |
| `pyproject.toml` | No change (inline implementation; `sdnotify` package not added) |

#### Key Code Anchors

- `spooler.py:221` — `SpoolerLock.options(...).remote(pid=...)` + `await lock.get_pid.remote()` — readiness precondition
- `spooler.py:226` — `logger.info("spooler started, pid={pid}")` — `READY=1` insertion point
- `spooler.py:230` — `while not self.shutdown_event.is_set():` — `WATCHDOG=1` insertion point
- `spooler.py:213–219` — duplicate-spooler early-exit (correct: never sends `READY=1` → unit goes to failed state)
- `config.py:129` — `KODO_SPOOLER_INTERVAL` default 0.25s (480× headroom vs 120s watchdog)

#### Unit Tests

- `test_sdnotify_noop_when_no_socket` — `monkeypatch.delenv("NOTIFY_SOCKET")`; call `_sd_notify("READY=1")` → no raise
- `test_sdnotify_sends_correct_datagram` — bind real `AF_UNIX SOCK_DGRAM` listener at tmp path; set `NOTIFY_SOCKET`; call `_sd_notify("READY=1")`; assert received bytes == `b"READY=1"`
- `test_sdnotify_noop_on_nonexistent_path` — `NOTIFY_SOCKET=/tmp/does_not_exist_xyzzy`; call → no raise
- `test_sdnotify_abstract_namespace` — bind `\0kodo_test_sdnotify`; `NOTIFY_SOCKET=@kodo_test_sdnotify`; call → assert datagram received (Linux only; skip on macOS)
- `test_ready1_sent_at_correct_point` — patch `_sd_notify`; run `Spooler.start()` with Ray fully mocked (mock `SpoolerLock`, `list_actors`, shutdown after first iteration); assert `_sd_notify("READY=1")` called exactly once before loop and `_sd_notify("WATCHDOG=1")` called at least once during loop

#### Staging Tests (odin)

1. Deploy updated `spooler.service` (Type=notify, WatchdogSec=120) + updated kodosumi. `systemctl restart spooler`. Verify `systemctl status spooler` shows `Active: active (running)` (not `activating`) within 30s.
2. `systemctl show spooler -p WatchdogTimestampMonotonic` must update every ~1s while running.
3. Start spooler with Ray stopped; `systemctl start spooler` must timeout and report `failed` (READY=1 never sent). Correct new behavior vs old `active` immediately.
4. `sudo kill -STOP $(systemctl show spooler -p MainPID --value)`; after 120s systemd must kill and restart; confirm `journalctl -u spooler` shows watchdog timeout kill reason.
5. After normal restart, submit a job via panel; confirm execution events appear in `data/execution/<user>/<fid>/sqlite3.db`.
6. `systemctl show spooler -p MemoryMax` → confirm shows configured value.

#### Infra Snippet

```ini
# environments/{loki,odin,...}/systemd/spooler.service
# Deploy via: pyinfra -y --sudo environments/<env>/inventories/inventory.py deployment/deploy_services.py
# Or file-only (no auto-restart): new deploy_spooler_unit.py analogous to deploy_ray_head_unit.py

[Unit]
Description=Kodosumi Spooler Service
After=network.target ray-head.service
Wants=ray-head.service

[Service]
User=kodosumi
WorkingDirectory=/srv/kodosumi
Type=notify
NotifyAccess=main

ExecStart=/srv/kodosumi/.venv/bin/koco spool --block
ExecStop=/srv/kodosumi/.venv/bin/koco spool --stop

# 480x headroom over the 0.25s loop interval
WatchdogSec=120

Restart=always
RestartSec=10

# Spooler holds one asyncio task + one sqlite3.Connection per active Runner
MemoryMax=512M

TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

#### Risk

**Low.** `_sd_notify()` is best-effort; `OSError` is silently caught. In any non-systemd environment (`NOTIFY_SOCKET` absent) the function is a pure no-op. The only irreversible coupling is the infra-side unit type change from `Type=simple` to `Type=notify`: if the new kodosumi package is not deployed before the updated unit is activated, the spooler starts correctly but `READY=1` never arrives → systemd holds in `activating` until `TimeoutStartSec` (90s) expires → kill. Mitigation: deploy kodosumi package before or atomically with the unit file (standard pyinfra deploy sequence).

#### Resolved Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Implementation | Inline 12-line stdlib socket helper | No new dep; `sdnotify` package does the same thing internally |
| WatchdogSec | 120s | 480× headroom; catches permanently deadlocked event loop within 2 min without false positives |
| STOPPING=1 | Omit | ExecStop via `koco spool --stop` already works; STOPPING=1 is a v1.2.0+ enhancement |
| MemoryMax | 512M | Generous ceiling for ~20 concurrent runners; prevents memory-leak node exhaustion |
| RuntimeMaxSec | Omit | WatchdogSec handles liveness; periodic restart causes uncontrolled mid-session drop |

---

### D5-spoolerloss — Spooler Drain-Before-Kill + Fail-Loud

**Tickets:** #74
**Suggested branch:** `fix/74-spooler-reap-drain-fail-loud`

#### Design Summary

Three layered defences: **L1** — guaranteed drain-before-kill using a sentinel token (`__drain_complete__`) emitted by `Runner.shutdown()` as its final queue put; spooler loops until sentinel seen (10s backstop timeout). **L2** — fail-loud guard in `Launch()` and `_submit_job()` that calls `check_spooler_health()` before starting any job; returns HTTP 503 when the spooler is absent. **L3** — durable disk buffer deferred to v1.3.

#### Files Touched

| File | Change |
|------|--------|
| `kodosumi/spooler.py` | `retrieve()`: add final drain loop after `is_active()==False` break; add `SpoolerLock.is_healthy()` method |
| `kodosumi/runner/main.py` | `shutdown()`: emit `__drain_complete__` sentinel as final queue put |
| `kodosumi/const.py` | Add `SPOOLER_DRAIN_SENTINEL = '__drain_complete__'` |
| `kodosumi/serve.py` | `Launch()`: add `check_spooler_health()` guard before `runner.run.remote()` |
| `kodosumi/service/sumi/control.py` | `_submit_job()`: add same `check_spooler_health()` guard before `proxy_forward()` |
| `kodosumi/config.py` | Add `SPOOLER_FINAL_DRAIN_TIMEOUT: float = 10.0`, `SPOOLER_HEALTH_CHECK: bool = True` |
| `kodosumi/helper.py` | Add `check_spooler_health() -> bool` |
| `tests/test_spooler_drain.py` | New: unit tests for drain-before-kill |
| `tests/test_spooler_health_guard.py` | New: unit tests for health check helper and guard |

#### Key Code Anchors

- `spooler.py:182–202` — `retrieve()` main loop and `ray.kill`
- `spooler.py:193–196` — `ActorDiedError` branch (no drain; patched by L1)
- `runner/main.py:443–459` — `Runner.shutdown()` (emit sentinel as final put before `self.active = False`)
- `serve.py:573–583` — `Launch()` function body (guard added here)
- `sumi/control.py:826–848` — `_submit_job` before `proxy_forward` (guard added here)
- `helper.py:203–218` — `get_health_status()` (existing `SpoolerLock` query pattern; `check_spooler_health` modelled after this)
- `config.py:129–131` — spooler settings block

#### Unit Tests

- `test_final_drain_after_inactive` — fake queue returns items then raises `Empty`; assert all items saved before `ray.kill`
- `test_sentinel_terminates_drain` — N events then sentinel; assert drain exits after sentinel and N events saved
- `test_actor_died_error_triggers_final_drain` — `ActorDiedError` in `get_nowait_batch`; assert final drain runs before `ray.kill`
- `test_final_drain_respects_timeout` — queue never returns `Empty`; assert loop exits before deadline
- `test_check_spooler_health_returns_true` — mock `ray.get_actor` + `ray.get`; assert `True`
- `test_check_spooler_health_returns_false_when_missing` — mock `ray.get_actor` raises; assert `False`
- `test_check_spooler_health_returns_false_on_timeout` — mock `ray.get` raises `RayTaskError`; assert `False`
- `test_launch_raises_503_when_no_spooler` — patch `check_spooler_health=False`; call `Launch()`; assert `HTTPException(503)`
- `test_launch_proceeds_when_spooler_healthy` — patch `check_spooler_health=True`; mock `create_runner`; assert `runner.run.remote()` called
- `test_submit_job_returns_error_when_no_spooler` — patch `check_spooler_health=False`; call `_submit_job()`; assert error response with spooler-down message
- `test_spooler_health_check_disabled_via_config` — `KODO_SPOOLER_HEALTH_CHECK=false`; assert `Launch()` does not raise 503

#### Staging Tests (odin)

1. Submit panel job; confirm completes with all events in execution DB. Baseline.
2. Kill spooler (`kill -9`) mid-flight; restart; verify no events missing and job eventually shows `finished`.
3. With spooler stopped, submit panel job; verify HTTP 503 and no Runner actor created.
4. With spooler stopped, submit Sumi `start_job`; verify error response with spooler-down message.
5. Restart spooler; verify both entry points succeed without panel restart.
6. Submit paid Sumi job; kill spooler during `STATUS_PAYMENT`; restart; verify reconcile sweep picks up orphan.

#### Infra Snippet

```ini
# /etc/kodosumi/kodosumi.env additions for D5

# L1: final drain timeout (seconds)
KODO_SPOOLER_FINAL_DRAIN_TIMEOUT=10.0

# L2: set false only in local dev (no spooler running)
KODO_SPOOLER_HEALTH_CHECK=true

# L3 (deferred to v1.3):
# KODO_DISK_BUFFER_DIR=/srv/kodosumi/data/spooler-buffer
```

#### Risk

**Medium.** L1 touches the `retrieve()` loop hot path — a bug could cause `retrieve()` to hang indefinitely if sentinel is never emitted or timeout backstop fails. Mitigation: 10s backstop is mandatory alongside the sentinel; `try/finally` ensures SQLite connection always closed and `ray.kill` always called. L2 adds a synchronous `ray.get()` (2s timeout) to every job start — worst case 2s delay; gate behind `KODO_SPOOLER_HEALTH_CHECK` for dev mode. L3 disk buffer deferred entirely.

#### Resolved Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| L1 drain mechanism | Sentinel token + 10s backstop timeout | Sentinel is emitted at a well-defined point in `shutdown()`; eliminates timeout guesswork |
| L2 scope | Both `Launch()` and `_submit_job()` | Silent result loss on panel jobs is also unacceptable; single helper call |
| L3 disk buffer | Defer to v1.3 | L1+L2 close most actionable gaps; disk buffer adds Tracer hot-path I/O |

---

### D6-accesslog — Access-Log Verbosity

**Tickets:** #71
**Suggested branch:** `fix/71-access-log-verbosity`

#### Design Summary

Three new env vars reduce log volume by ~75% without suppressing error signal: `KODO_PANEL_ACCESS_LOG_LEVEL` controls `uvicorn.access` logger level and `access_log` bool independently of `KODO_UVICORN_LEVEL`; `KODO_ACCESS_LOG_QUIET_PATHS` (comma-separated) demotes matching path prefixes to DEBUG in the `LoggingMiddleware` (errors always remain WARNING); `KODO_SERVE_ACCESS_LOG` (bool) controls Ray Serve `enable_access_log` in the serve config. All defaults preserve current behaviour.

#### Files Touched

| File | Change |
|------|--------|
| `kodosumi/config.py` | Add `PANEL_ACCESS_LOG_LEVEL: str = "INFO"`, `SERVE_ACCESS_LOG: bool = True`, `ACCESS_LOG_QUIET_PATHS: List[str] = []` with comma-split validator |
| `kodosumi/service/server.py` | Decouple `uvicorn.access` level from `UVICORN_LEVEL`; pass `access_log=panel_access_log` to `uvicorn.run()` |
| `kodosumi/log.py` | After `uvicorn_logger.setLevel(...)` (line 69): add `uvicorn.access` level gate for the propagation path |
| `kodosumi/service/app.py` | `LoggingMiddleware`: lazy-inject `quiet_paths` from `scope["app"].state`; demote matching paths to DEBUG; always log ≥400 at WARNING |
| `kodosumi/service/expose/boot.py` | `load_serve_config()`: override `logging_config.enable_access_log` from `SERVE_ACCESS_LOG` in-memory (env authoritative; file not rewritten) |

#### Key Code Anchors

- `service/server.py:20–41` — `log_config` dict construction and `uvicorn.run()` call
- `log.py:66–69` — `uvicorn` parent logger setup (propagation path)
- `service/app.py:187–212` — `LoggingMiddleware.__call__`
- `boot.py:83–99` — `DEFAULT_SERVE_CONFIG` dict (change `enable_access_log`)
- `boot.py:44–65` — YAML seed text (add comment; value is runtime-overridden)
- `boot.py:831` — `load_serve_config()` (apply in-memory override here)

#### Unit Tests

- `test_panel_access_log_level_off_sets_critical` — `Settings(PANEL_ACCESS_LOG_LEVEL='off')`; `app_logger(settings)`; assert `logging.getLogger("uvicorn.access").level > logging.CRITICAL`
- `test_panel_access_log_level_warning` — `Settings(PANEL_ACCESS_LOG_LEVEL='warning')`; assert level == `WARNING`
- `test_server_run_passes_access_log_false_when_off` — mock `uvicorn.run`; `PANEL_ACCESS_LOG_LEVEL='off'`; assert `access_log=False` passed
- `test_server_run_passes_access_log_true_when_warning` — assert `access_log=True` and log_config entry == `WARNING`
- `test_middleware_quiet_path_logs_debug` — minimal ASGI test with `quiet_paths=["/timeline"]`; `GET /timeline`; assert `record.levelno == DEBUG`
- `test_middleware_non_quiet_path_logs_info` — `GET /expose/`; assert `INFO`
- `test_middleware_error_always_logs_warning` — `quiet_paths=["/health"]`; `GET /health` returning 500; assert `WARNING`
- `test_settings_quiet_paths_parsed_from_comma_string` — `ACCESS_LOG_QUIET_PATHS='/timeline,/sumi,/health'`; assert list of 3
- `test_load_serve_config_respects_serve_access_log_false` — write `serve_config.yaml` with `enable_access_log: true`; patch `SERVE_ACCESS_LOG=False`; call `load_serve_config()`; assert result `['logging_config']['enable_access_log'] is False`
- `test_panel_access_log_level_default_is_info` — `Settings().PANEL_ACCESS_LOG_LEVEL == "INFO"`
- `test_serve_access_log_default_is_true` — `Settings().SERVE_ACCESS_LOG is True`

#### Staging Tests (odin)

1. Baseline: count `journalctl -u koco --since '1 hour ago' | wc -l` and grep access lines in `app.log`.
2. Deploy with `KODO_PANEL_ACCESS_LOG_LEVEL=WARNING`. Confirm journald koco lines/h drops ~50%; `app.log` still shows kodo middleware lines for non-poll paths.
3. With `KODO_ACCESS_LOG_QUIET_PATHS=/timeline,/sumi,/api/dashboard,/health`: confirm `GET /timeline` absent from stdout but visible in `app.log` at DEBUG.
4. Trigger deliberate 404 on quiet path; confirm WARNING appears in journald.
5. Set `KODO_PANEL_ACCESS_LOG_LEVEL=off`; confirm uvicorn startup/shutdown messages still appear (controlled by `UVICORN_LEVEL`).
6. Set `KODO_SERVE_ACCESS_LOG=false`; boot; call sumi endpoint; confirm Ray Serve logs show no access entries.
7. Force a 500; verify ERROR/WARNING appears in `app.log` unchanged.

#### Infra Snippet

```ini
# kodosumi_infra — environments/{odin,loki}/systemd/koco.service [Service] additions

# D6: Access-log verbosity (ticket #71)
Environment=KODO_PANEL_ACCESS_LOG_LEVEL=WARNING
Environment=KODO_ACCESS_LOG_QUIET_PATHS=/timeline,/sumi,/api/dashboard,/health
Environment=KODO_SERVE_ACCESS_LOG=false
```

#### Risk

**Medium-low.** `uvicorn.access` level change is purely additive (safe default). Biggest risk: `LoggingMiddleware` settings-injection — if `scope["app"].state` is not yet populated during a very early request, `self._quiet_paths` falls back to `[]` (log everything), which is safe. `SERVE_ACCESS_LOG` override is in-memory only; does not rewrite the operator YAML — document precedence clearly. Rollback: unset the three env vars and restart `koco`.

#### Resolved Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Single vs two knobs | Two: `PANEL_ACCESS_LOG_LEVEL` (uvicorn.access) + `ACCESS_LOG_QUIET_PATHS` (kodo middleware) | Kodo middleware is more valuable (duration + user); must not suppress 400/500 on quiet paths |
| Settings injection | Lazy via `scope["app"].state` (cached on `self._quiet_paths` after first access) | No change to middleware registration; Litestar guarantees state populated before first request |
| `SERVE_ACCESS_LOG` scope | Always override in-memory after load; env is authoritative; file not rewritten | 12-factor: env vars trump config files; operator file is preserved on disk |

---

### D7-severity — Severity Hygiene

**Tickets:** #79 (+ pairs with #71)
**Suggested branch:** `fix/79-severity-hygiene`

#### Design Summary

Systematic reclassification of 22 mis-leveled logger call sites across 8 files: 5 silent bare-except blocks that swallow errors without logging; 3 DEBUG calls that should be WARNING/ERROR; 2 INFO calls in except blocks for real failures (should be WARNING); 4 `logger.critical` calls demoted to `logger.error`; 4 `logger.warning` success-companion calls demoted to `logger.info`; addition of `exc_info=True` to 4 existing ERROR calls in `registry.py`. No logic or exception propagation changes.

#### Files Touched

| File | Change |
|------|--------|
| `kodosumi/runner/main.py` | line 338: bare `except` + `files=None` → `logger.warning(..., exc_info=True)`; line 457: bare `except: pass` → `logger.error(..., exc_info=True)` |
| `kodosumi/runner/reconcile.py` | line 130: `DEBUG` → `WARNING`; line 287: `INFO "already relaunched"` → `DEBUG`; line 351: `INFO "reconcile: failed"` → `WARNING` |
| `kodosumi/spooler.py` | line 150: `INFO "reconcile sweep: N frozen"` → `WARNING`; line 162: `WARNING` when result in `(resumed, failed)`, `INFO` when `skip`; line 219–220: bare `except: pass` → `logger.debug`; line 313: `CRITICAL "no spooler found"` → `ERROR` |
| `kodosumi/service/app.py` | line 142: `WARNING "Masumi sync failed"` → `ERROR`; line 208: bare `except: user="-"` → `logger.debug` |
| `kodosumi/service/expose/control.py` | line 91–92: bare `except` → `logger.warning`; line 123–124: bare `except` → `logger.debug`; line 405–406: bare `except` → `logger.warning` |
| `kodosumi/service/expose/boot.py` | line 389–390: bare `except` → `logger.debug`; line 2376–2383: add `logger.error` alongside SSE `WARNING` yield; boot/refresh top-level exceptions → add `logger.error` |
| `kodosumi/service/inputs/outputs.py` | lines 261, 269, 341, 349: `CRITICAL "failed to kill/archive"` → `ERROR`; lines 263, 271, 343, 351: `WARNING "killed/archived"` → `INFO` |
| `kodosumi/service/sumi/control.py` | line 896: `WARNING "start_job prepare failed"` → `ERROR` + `exc_info=True` |
| `kodosumi/service/expose/registry.py` | lines 238, 324, 360: add `exc_info=True` to existing ERROR calls |
| `kodosumi/service/execution_index.py` | line 128: add `db_path.exists()` guard; `DEBUG` → `WARNING` only for unreadable existing DBs |

#### Key Code Anchors

All file:line anchors are in the files-touched table above. See the audit table in the design spec for the complete category breakdown (A: silent swallows, B: DEBUG→WARNING, C: INFO→WARNING, D: CRITICAL→ERROR, E: companion demotions, F: missing exc_info).

#### Unit Tests

- `test_runner_shutdown_logs_error_not_silent` — monkeypatch queue actor to raise `RuntimeError`; call `runner.shutdown()`; assert `logger.error` called with `exc_info=True`
- `test_runner_fs_ls_failure_logs_warning` — monkeypatch `tracer.fs().ls()` to raise; call `runner.start()` up to `fs.ls` point; assert `logger.warning` called
- `test_reconcile_db_open_warns` — `db_path.exists()` but `chmod 0o000`; call `read_payment_state()`; assert `caplog` contains `WARNING "reconcile: cannot open"`
- `test_reconcile_failed_job_is_warning` — expired `pay_by_time`, mock `get_payment_status` returns `onChainState=None`; assert `caplog` level == `WARNING` for "reconcile: failed"
- `test_spooler_frozen_payment_is_warning` — `_scan_frozen_payments()` returns non-empty list; assert `logger.warning` (not `logger.info`)
- `test_masumi_sync_failure_is_error` — patch `cache.sync_payments()` to raise; run one loop iteration; assert `caplog` ERROR "Masumi sync"
- `test_kill_runner_failure_is_error_not_critical` — monkeypatch `kill_runner()` to raise; trigger delete; assert no CRITICAL, exactly one ERROR "failed to kill"
- `test_archive_failure_is_error_not_critical` — monkeypatch `Path.rename()` to raise; assert no CRITICAL, exactly one ERROR "failed to archive"
- `test_ray_serve_status_silent_swallow_warns` — monkeypatch `HTTPXClient` to raise `ConnectError`; call `get_ray_serve_status()`; assert `caplog WARNING "Ray Serve status query failed"`; empty dict still returned
- `test_sumi_prepare_failure_is_error` — monkeypatch `runner.prepare.remote()` to raise; call `_submit_job()`; assert `caplog ERROR` record

#### Staging Tests (odin)

1. Set invalid MASUMI token; wait 5 min; `journalctl -u kodosumi-panel` shows ERROR (not WARNING) for "Masumi sync ... failed".
2. Kill Ray actor then delete execution from panel; verify ERROR "failed to kill" (not CRITICAL) and no 500 response.
3. Force reconcile sweep with `RECONCILE_INTERVAL=60s` and orphaned payment fixture; verify spooler.log shows WARNING for frozen jobs and WARNING for failed reconcile.
4. Run a clean execution start/finish cycle; verify zero WARNING/ERROR/CRITICAL lines in panel.log.
5. Inject corrupt sqlite3 DB; verify `reconcile.py:130` emits WARNING to spooler.log.

#### Infra Snippet

None required.

#### Risk

**Low.** Pure log-level reclassifications; no logic or exception propagation changes. Review note: any Loki alert rule keyed on CRITICAL for `kill`/`archive` failures will no longer fire — audit alert rules before deploying. The new WARNING in `get_ray_serve_status()` fires on every boot if Ray Dashboard is unreachable; pair with D6 (noise reduction) in the same release.

#### Resolved Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| `spooler.py:101 logger.critical("failed to save")` | Keep CRITICAL | Irreversible data loss for that job — warrants alerting |
| `spooler.py:146 logger.critical("reconcile sweep scan failed")` | Demote to ERROR | Best-effort; next sweep retries; reserve CRITICAL for data-loss events |
| `spooler.py:236 logger.critical("failed listing actor names")` | Keep CRITICAL | Spooler completely blind — service-wide data loss condition |
| `reconcile.py:130 exc_info` | No `exc_info` | sqlite3 error messages are self-describing; traceback excessive for WARNING |
| `execution_index.py:128` | Add `db_path.exists()` guard | Absent DB is race-normal; only warn for unreadable existing DBs |

---

### D8-correlation — Correlation IDs

**Tickets:** #70 + #80
**Suggested branch:** `feat/70-80-correlation-ids`

#### Design Summary

Two correlation gaps closed: **#70** — add a single `logger.info` line in `_submit_job()` after `job_id` is confirmed and `prepare()` returns, carrying `fid`, `sokosumi_job` (`identifier_from_purchaser`), `blockchain`, and `input_hash`. Remove all 7 `sumi_debug.log` file writes and replace with `logger.debug()`. **#80** — add `_log_meta_correlation()` helper in `Spooler.retrieve()` that emits a correlation log when the first `meta` event arrives in the batch, extracting `fid`, `username`, `entry_point`, `sumi_endpoint`, and `identifier_from_purchaser` from the meta payload. Enrich the existing `finished` log at spooler.py:203 with `username`.

#### Files Touched

| File | Change |
|------|--------|
| `kodosumi/service/sumi/control.py` | Add `logger.info` correlation line after `job_id` confirmed + `prepare()` returns (after line 900); remove 7 `sumi_debug.log` blocks (lines 768–771, 849–853, 1334–1337, etc.) → `logger.debug()`; enrich `logger.warning` at line 896 with `job_id` and `identifier_from_purchaser` |
| `kodosumi/spooler.py` | Add `_log_meta_correlation(fid, username, batch)` helper; call in `retrieve()` batch loop before `save()`; enrich `finished` log at line 203 with `username` |
| `kodosumi/runner/main.py` | No logic changes required; meta event (line 306) already serializes all correlation fields |

#### Key Code Anchors

- `sumi/control.py:768–771` — first `sumi_debug.log` write (→ `logger.debug`)
- `sumi/control.py:849–853` — agent response log block (→ `logger.debug`)
- `sumi/control.py:900` — correlation `logger.info` insertion point (after `prepare()` returns)
- `sumi/control.py:896–899` — existing warning (enrich with correlation fields)
- `spooler.py:203` — `finished {fid} with {n} records` (enrich with `username`)
- `spooler.py:166` — `retrieve()` start (meta correlation call added in batch loop)
- `runner/main.py:306` — `_put_async(EVENT_META, serialize({fid, username, entry_point, extra:{...}}))` (data source; unchanged)

#### Unit Tests

- `test_submit_job_correlation_log` — mock `proxy_forward`, `ray.get_actor`, `runner.prepare.remote()`; call `_submit_job()` with `identifier_from_purchaser='test-job-id'`; assert `logger.info` called once with `fid=`, `sokosumi_job=test-job-id`, `input_hash=`
- `test_submit_job_correlation_log_free_agent` — `agentIdentifier=None`; assert log contains `blockchain=-`
- `test_submit_job_correlation_log_prepare_failure` — `runner.prepare.remote()` raises; assert `logger.warning` includes both `job_id` AND `identifier_from_purchaser`
- `test_spooler_meta_correlation` — `_log_meta_correlation(fid='abc123', username='user1', batch=[{kind='meta', payload=...}])`; assert log contains `fid=abc123`, `sokosumi_job=sokosumi-99`, `sumi=my-agent/run`
- `test_spooler_meta_correlation_non_sumi_job` — meta event with no `extra` key; assert `sokosumi_job=-` and no exception
- `test_spooler_meta_correlation_bad_payload` — `payload='not-json'`; assert no exception propagates
- `test_no_debug_file_write` — patch `builtins.open`; call `_submit_job()`; assert `open` never called with path ending in `sumi_debug.log`

#### Staging Tests (odin)

1. POST `start_job` with `identifier_from_purchaser='test-correlation-01'`; grep `app.log` for `start_job created fid=` within 2s; verify `fid`, `sokosumi_job=test-correlation-01`, `input_hash` on same line.
2. Within 5s, grep `spooler.log` for `job_start fid=<same-fid>`; verify `sokosumi_job` and `sumi_endpoint` match request.
3. Run free (non-paid) Sumi job; confirm `blockchain=-` and `sokosumi_job=-`.
4. Run paid Sumi job through payment; confirm `blockchain=<blockchainIdentifier>` (not `-`).
5. `grep app.log 'sumi_debug.log'` → no matches post-deploy.
6. Confirm `/srv/kodosumi/data/sumi_debug.log` receives no new writes (mtime constant).
7. Grep `spooler.log` for `job_done fid=`; confirm `user=` present and non-empty.

#### Infra Snippet

None required.

#### Risk

**Low.** All additive log statements and removal of debug file writes. The `sumi_debug.log` removal is the highest-risk part: if any operator monitoring tool was grepping that file, it stops receiving data (data moves to `app.log` via `logger.debug`). The `_log_meta_correlation` helper is wrapped in bare `except Exception: pass` — a malformed meta payload can never crash the spooler. The `logger.info` in `_submit_job` is inside the existing try/except; on paid agents it fires after `prepare()` (~2–5s delay vs HTTP response), but synchronously before `_submit_job` returns.

#### Resolved Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| `LoggingMiddleware` KODOSUMI_LAUNCH sniff | Keep middleware as-is; handler-level log only | Response-header sniffing in `send_wrapper` is fragile and route-specific |
| Spooler correlation source | Parse meta from first batch in `retrieve()` | Zero new remote calls; reuses existing meta event data; no protocol changes |
| `sumi_debug.log` removal | Remove in this PR | Hardcoded prod path breaks test isolation; data moves to Loki at DEBUG level |

---

### D9-lifecycle — Job Lifecycle Events + Heartbeat

**Tickets:** #76
**Suggested branch:** `feat/76-job-lifecycle-heartbeat`

#### Design Summary

Add six structured lifecycle events (`job_created`, `job_dispatched`, `container_start`, `container_ready/failed`, `job_heartbeat`, `job_finished/failed`) to the existing message queue as a new `EVENT_LIFECYCLE = "lifecycle"` kind. Add `_heartbeat_loop()` coroutine in `Spooler.retrieve()` that fires every `HEARTBEAT_INTERVAL` seconds, emits heartbeat rows to the per-execution SQLite DB, and logs a WARNING for jobs exceeding `HUNG_JOB_THRESHOLD`. D12 format is a dependency for the final JSON envelope; payload is forward-compatible.

#### Files Touched

| File | Change |
|------|--------|
| `kodosumi/const.py` | Add `EVENT_LIFECYCLE = "lifecycle"`; add to `MAIN_EVENTS`; add lifecycle event name constants; add `HEARTBEAT_INTERVAL` and `HUNG_JOB_THRESHOLD` defaults |
| `kodosumi/runner/main.py` | `Launch()`: emit `job_created` + `job_dispatched` via `runner.emit_lifecycle.remote()`; `Runner.__init__`: `self._created_at = now()`; `Runner.start()`: emit `container_ready`; `Runner.run()` finally: emit `job_finished`/`job_failed`; add `emit_lifecycle()` remote method |
| `kodosumi/runner/tracer.py` | Add `lifecycle()` async method to `Tracer`; no-op `lifecycle()` to `TracerMock` |
| `kodosumi/spooler.py` | `retrieve()`: launch `_heartbeat_loop` as `asyncio.Task` after `setup_database()`; cancel in finally; add `self._heartbeat_tasks: dict` and interval/threshold params to `Spooler.__init__` |
| `kodosumi/config.py` | Add `HEARTBEAT_INTERVAL: float = 30.0`, `HUNG_JOB_THRESHOLD: float = 300.0` |

#### Key Code Anchors

- `const.py:22` — `EVENT_*` constants block (add `EVENT_LIFECYCLE`)
- `runner/main.py:583` — `runner.run.remote()` in `Launch()` (emit `job_created`/`job_dispatched` before/after)
- `runner/main.py:69` — `Runner.__init__` (add `self._created_at = now()`)
- `runner/main.py:271` — `Runner.start()` after first `_put_async(EVENT_STATUS, STATUS_STARTING)` (emit `container_ready`)
- `runner/main.py:253` — `Runner.run()` finally block (emit `job_finished`/`job_failed`)
- `spooler.py:166` — `retrieve()` start (launch heartbeat task)
- `spooler.py:242` — `asyncio.create_task(self.retrieve(...))` (emit `container_start` lifecycle event here)
- `spooler.py:46` — `Spooler.__init__` (add `heartbeat_interval`, `hung_job_threshold`, `_heartbeat_tasks`)
- `spooler.py:287` — `spooler.main()` (pass settings to Spooler constructor)

#### Unit Tests

- `test_runner_lifecycle_events` — `Runner.__init__` sets `_created_at`; `start()` emits `container_ready` with `elapsed>=0`; `run()` emits `job_finished` on clean exit and `job_failed` on exception (TracerMock + mock Ray queue)
- `test_emit_lifecycle_payload` — `emit_lifecycle()` returns valid JSON with `event`, `fid`, `elapsed`, `agent` keys
- `test_event_lifecycle_in_main_events` — `import EVENT_LIFECYCLE from kodosumi.const; assert EVENT_LIFECYCLE in MAIN_EVENTS`
- `test_heartbeat_loop_n_heartbeats` — mock `runner.is_active.remote()` returns `True` N times then `False`; assert N heartbeat rows in in-memory sqlite3 DB
- `test_heartbeat_loop_hung_job_event` — elapsed > `hung_job_threshold`; assert `job_hung` event row written and WARNING in caplog
- `test_heartbeat_task_cancelled_in_finally` — mock `asyncio.Task.cancel()`; verify called in `retrieve()` finally block
- `test_settings_heartbeat_env_override` — `KODO_HEARTBEAT_INTERVAL=60`, `KODO_HUNG_JOB_THRESHOLD=600`; assert `Settings()` returns overrides
- `test_tracer_mock_lifecycle_noop` — `TracerMock.lifecycle()` is awaitable and raises no exception

#### Staging Tests (odin)

1. Submit job; after completion, inspect `execution/sqlite3.db`: `SELECT kind, message FROM monitor WHERE kind='lifecycle' ORDER BY id`; confirm rows for `job_created`, `job_dispatched`, `container_ready`, `job_finished`.
2. Submit job that sleeps >300s with `KODO_HUNG_JOB_THRESHOLD=60`; verify `job_hung` event in DB and `grep 'hung job detected' spooler.log`.
3. After job completes, wait 2×HEARTBEAT_INTERVAL; confirm no new `job_heartbeat` rows appear (task cancelled correctly).
4. Kill Ray actor mid-job; confirm heartbeat stops and no `job_finished` row (actor killed before finally — expected gap; document).
5. Run existing Sumi e2e test; compare monitor table; only new `lifecycle` kind rows should appear.

#### Infra Snippet

```ini
# /srv/kodosumi/.env additions for odin/loki
KODO_HEARTBEAT_INTERVAL=30
KODO_HUNG_JOB_THRESHOLD=300

# For staging test of hung-job detection:
# KODO_HUNG_JOB_THRESHOLD=60

# Only panel + spooler need restart (not ray-head):
# sudo systemctl restart kodosumi-panel kodosumi-spooler
```

#### Risk

**Medium.** The heartbeat task doubles asyncio task count during peak load (~15 agents on loki → +15 tasks, negligible). The `conn` handle passed to `_heartbeat_loop` is the same sqlite3 connection used by `retrieve()`'s `save()` calls — both on the same asyncio thread, no concurrency hazard. Risk: a bug in `_heartbeat_loop` preventing `retrieve()`'s finally block — mitigated by wrapping the loop body in `try/except Exception: logger.error(...); break`. The `job_hung` log is purely observational with no side-effects.

#### Resolved Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Container image:tag in events | Omit for v1.2.0 (emit `image=null`) | No stable Python API to read from Ray; coupling to Ray internals creates fragility |
| `job_created`/`job_dispatched` write path | Via `runner.emit_lifecycle.remote()` (queue) | Direct DB write from ServeAPI worker is fragile (`EXEC_DIR` may not be mounted on all workers) |
| Hung job enforcement | Observability only (heartbeat + WARNING log); no actor kill | Enforcement is a destructive action requiring its own design review (payment state, HITL) |

---

### D10-dephealth — Dependency & Node Health

**Tickets:** #77 + #75
**Suggested branch:** `feat/75-77-dep-node-health`

#### Design Summary

Refactor `get_health_status()` in `helper.py` to aggregate `ray.nodes()` into `alive_count`/`dead_count`/`dead[]`/`alive[]` rather than dumping the raw list. Add a periodic `_node_health_loop()` background task in `app.py` (mirrors `_masumi_sync_loop` pattern) that checks GCS connectivity, Serve controller reachability, and (stub for) redis_mem thresholds every `NODE_HEALTH_INTERVAL` seconds; stores results in `app.state["node_health"]`; logs WARNING on state change (ok→degraded only). Surface results in `GET /health/` response body and `routes.html` dashboard.

#### Files Touched

| File | Change |
|------|--------|
| `kodosumi/helper.py` | Add `_aggregate_nodes(nodes: list[dict]) -> dict`; refactor `get_health_status()` to use it; add `node_health_cache` param |
| `kodosumi/service/health.py` | `health_status()`: add `node_health` from `app.state` to payload; add `degraded: bool` field (option 2: keep HTTP 200) |
| `kodosumi/service/app.py` | Add `_node_health_loop()` async function + `_extract_redis_mb()` helper; wire into `startup()` / `shutdown()` |
| `kodosumi/config.py` | Add `NODE_HEALTH_INTERVAL: int = 60`, `NODE_HEALTH_REDIS_WARN_MB: int = 512`, `NODE_HEALTH_REDIS_CRIT_MB: int = 700` |
| `kodosumi/service/admin/templates/routes.html` | Replace bare node list (routes.html:150–157) with colored badge; add `node_health` sub-section |
| `tests/test_node_health.py` | New: pure unit tests for aggregation, threshold logic, health endpoint derivation |

#### Key Code Anchors

- `helper.py:203–218` — `get_health_status()` (refactored)
- `helper.py:218` — insert `_aggregate_nodes()` after this line
- `service/health.py:14` — `health_status()` handler
- `service/app.py:126–143` — `_masumi_sync_loop` (model for `_node_health_loop`)
- `service/app.py:160` — startup handler (wire new loop)
- `service/app.py:177` — shutdown handler (cancel new task)
- `routes.html:149–157` — node list (replace with badge template)
- `config.py:141` — settings block (add new fields after this line)

#### Unit Tests

- `test_aggregate_nodes_all_alive` — 3 alive nodes; assert `dead_count=0, alive_count=3, dead=[]`
- `test_aggregate_nodes_one_dead` — 1 dead node with `NodeName="worker1"`; assert `dead_count=1, dead[0]["name"]=="worker1"`
- `test_aggregate_nodes_empty` — `_aggregate_nodes([])` returns all zeros without error
- `test_get_health_status_structure` — mock `ray.nodes()` + `ray.get_actor` raises; assert keys `ray_nodes, spooler_status, node_health, kodosumi_version`
- `test_get_health_status_passes_cache` — `get_health_status(node_health_cache={"overall":"warning"})`; assert result includes it
- `test_health_endpoint_200_all_alive` — Litestar test client; all-alive; assert status 200
- `test_health_endpoint_degraded_field_when_dead` — one dead node; assert `ray_nodes.dead_count==1` and `degraded==True` (status still 200 per option 2)
- `test_extract_redis_mb_known_shape` — `_extract_redis_mb({"data":{"memoryInfo":{"usedBytes": 600*1024*1024}}}) == 600.0`
- `test_extract_redis_mb_missing_key` — `_extract_redis_mb({}) == 0.0` (no exception)
- `test_node_health_loop_writes_to_state` — run one loop iteration; mock `ray.nodes()` + `httpx`; assert `state["node_health"]["overall"] in ("ok","warning","critical")`

#### Staging Tests (odin)

1. Stop ray-worker on odin-ray-worker1; wait 2×NODE_HEALTH_INTERVAL; `GET /health/` → verify `ray_nodes.dead_count >= 1` and `degraded==True`; restart and re-check.
2. Open `/admin/routes` in browser; verify red badge `X alive / 1 dead` with dead worker name; goes green after restart.
3. Set `KODO_NODE_HEALTH_INTERVAL=10`; tail `app.log`; confirm state-change WARNING appears within 20s of worker stop/start.
4. Verify boot flow still works end-to-end after health endpoint changes (regression).
5. Curl `/health/` without auth; confirm 200 and no 401.

#### Infra Snippet

```ini
# /etc/kodosumi/kodo.env additions for D10
KODO_NODE_HEALTH_INTERVAL=60
KODO_NODE_HEALTH_REDIS_WARN_MB=512
KODO_NODE_HEALTH_REDIS_CRIT_MB=700
```

#### Risk

**Medium.** Two concrete risks: (1) Renaming `ray_status` → `ray_nodes` in `get_health_status()` is a breaking change for external `/health` JSON consumers (Prometheus scrapes, monitoring scripts) — mitigate by keeping `ray_status` as an alias for one release cycle pointing to the raw list. (2) `_node_health_loop` calls `ray.nodes()` in an asyncio task — `ray.nodes()` is synchronous and can block the event loop if GCS is slow — wrap in `asyncio.to_thread()` (same pattern as `_build_execution_index` at app.py:150).

#### Resolved Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| `/health` status code on dead node | Keep HTTP 200 + add `degraded: bool` and `overall: str` fields | `/health` is consumed by boot-step health checks; 503 requires auditing all callers — defer to v1.3 |
| Dead-node logging frequency | Log on state change only (debounce with `previous_overall` variable) | Every-cycle WARNING at 60s interval would spam logs during extended outage |
| Redis memory monitoring | Stub only for v1.2.0 (return 0); implement Prometheus alertmanager instead | Dashboard `/api/cluster_status` schema is undocumented across Ray 2.x versions |

---

### D11-bootlog — Boot/Startup Logging

**Tickets:** #78
**Suggested branch:** `feat/78-boot-startup-logging`

#### Design Summary

Two targeted additions: (1) A 5-line startup banner in `create_app()` logging `kodosumi/__version__`, git SHA (resolved from `KODO_GIT_SHA` env var, then `subprocess`, then `"unknown"`), `APP_SERVER`, `RAY_SERVER`, `RAY_DASHBOARD`, `RAY_SERVE_ADDRESS`, and `EXEC_DIR` — emitted to both `logger` (screen+file) and `audit` (audit.log). (2) Expanded per-app boot result logging in `_real_boot_process()`: `RUNNING` → `audit.info`; `DEPLOY_FAILED/UNHEALTHY` → `audit.warning` with Ray error reason (truncated to 200 chars); `TIMEOUT` → `audit.error`; a final `BOOT SUMMARY` audit line with version, git SHA, counts, failed names, and duration.

#### Files Touched

| File | Change |
|------|--------|
| `kodosumi/__init__.py` | Add `__git_sha__` constant: check `os.environ["KODO_GIT_SHA"]` first, then `subprocess.run(["git","rev-parse","--short","HEAD"], timeout=2)`, fallback `"unknown"` |
| `kodosumi/service/app.py` | In `create_app()` at line 279: replace current 2-line banner with 5-line structured banner; add `get_audit_logger` import; emit to both `logger` and `audit` |
| `kodosumi/service/expose/boot.py` | In `_real_boot_process()` at lines 2716–2723: expand per-app audit loop with reason/level; add `BOOT SUMMARY` audit line after step E (line 2785); add `import kodosumi` at top of file |

#### Key Code Anchors

- `__init__.py:1` — `__version__` declaration (add `__git_sha__` immediately after)
- `service/app.py:279–285` — current startup log block (replace)
- `service/app.py:35` — `from kodosumi.log import app_logger, logger` (add `get_audit_logger`)
- `boot.py:2716–2723` — per-expose audit loop (expand)
- `boot.py:2784` — `boot_duration` computation (add `BOOT SUMMARY` line after this)
- `boot.py:26` — `get_audit_logger` import (already present in boot.py)

#### Unit Tests

- `test_git_sha_env_override` — `monkeypatch os.environ["KODO_GIT_SHA"]="abc123"`; assert resolved SHA == `"abc123"`
- `test_git_sha_subprocess_fallback` — unset env var; mock `subprocess.run` returns `"def456"`; assert `"def456"`
- `test_git_sha_subprocess_failure_fallback` — mock `subprocess.run` raises `FileNotFoundError`; assert `"unknown"`
- `test_startup_banner_lines` — call `build_startup_banner(version, git_sha, settings)`; assert each expected key present: `"kodosumi/"`, `"git="`, `"app_server"`, `"ray_server"`, `"ray_dashboard"`, `"ray_serve"`, `"exec_dir"`
- `test_startup_banner_logging` — mock `logger` and `audit_logger`; call `emit_startup_banner()`; assert both called ≥ len(banner_lines) times; assert `ADMIN_PASSWORD` not in any logged string
- `test_per_app_audit_lines_running` — fake `final_statuses` with one `RUNNING` app; assert exactly one `audit.info` containing `"RUNNING"`
- `test_per_app_audit_lines_deploy_failed` — one `DEPLOY_FAILED` with reason; assert `audit.warning` containing `"DEPLOY_FAILED"` and reason (truncated to 200)
- `test_per_app_audit_lines_timeout` — one `TIMEOUT`; assert `audit.error` containing `"TIMEOUT"`
- `test_boot_summary_line` — call summary-emit helper with known values; assert all of version, git_sha, `total=5`, `running=3`, `failed=2`, failed names, duration present in `audit.info` call

#### Staging Tests (odin)

1. `koco serve`; confirm 5-line startup banner on stdout before first uvicorn log, containing version, non-empty git SHA, correct `RAY_SERVER/APP_SERVER`.
2. Trigger full boot via Admin UI; `tail -50 data/audit.log`; confirm startup banner, per-app RUNNING lines, and BOOT SUMMARY line with correct counts.
3. Add expose with invalid `import_path`; boot; confirm `audit.log` has WARNING with `DEPLOY_FAILED` and non-empty reason substring.
4. Set `KODO_GIT_SHA=test-sha-override` in `.env`; restart; check `audit.log` for `git=test-sha-override`; unset and restart to confirm subprocess fallback.

#### Infra Snippet

```ini
## environments/{odin,loki}/systemd/panel.service [Service] additions

Environment="KODO_GIT_SHA=__GIT_SHA__"

## In deploy script, replace __GIT_SHA__ at deploy time:
## GIT_SHA=$(git -C /srv/kodosumi/kodosumi rev-parse --short HEAD 2>/dev/null || echo unknown)
## sed -i "s/__GIT_SHA__/${GIT_SHA}/" /etc/systemd/system/panel.service
## systemctl daemon-reload
```

#### Risk

**Low.** All changes are additive log statements. The `subprocess.run` call has a hard 2-second timeout and `"unknown"` fallback — cannot block startup. The D12 dependency means the `BOOT SUMMARY` line format may change; isolate format logic into a small helper function for easy migration. The only new import is `kodosumi` in `boot.py` — surfaces immediately in tests if missing.

#### Resolved Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Git SHA resolution | `KODO_GIT_SHA` env var first, then subprocess, then `"unknown"` | Zero build-pipeline changes; infra repo already manages systemd env vars; subprocess works in local dev |
| Banner destination | Both `logger` (screen+file) and `audit` (audit.log) | High-value for ops debugging; emitted exactly once at startup; not chatty |
| DEPLOY_FAILED reason truncation | 200 chars | Consistent with existing pattern at boot.py:1005; full detail available in Boot UI SSE stream |

---

### D13-paymentobs — Payment Timeout Observability

**Tickets:** #54
**Suggested branch:** `feat/54-payment-timeout-observability`

#### Design Summary

Split the single `PaymentTimeoutError` into three semantically distinct leaf classes: `BuyerNoActionTimeoutError` (deadline expired, on-chain state never observed), `PaymentDeadlineTimeoutError` (deadline expired with indeterminate last state), and `PaymentRejectedError` (terminal non-FundsLocked on-chain state observed before deadline). All three are backward-compatible subclasses of the existing `PaymentTimeoutError`. Each raise site emits a structured `EVENT_PAYMENT` event via an optional async `on_event` callback before raising, giving operators a log trail with blockchain coordinates. Add `KNOWN_TERMINAL_STATES` frozenset constant to `payment.py`.

#### Files Touched

| File | Change |
|------|--------|
| `kodosumi/runner/payment.py` | Add 3 exception subclasses; add `KNOWN_TERMINAL_STATES` frozenset; rewrite `wait_for_funds_locked` loop (2 raise sites with correct semantics); add `on_event: Optional[Callable] = None` parameter; emit `EVENT_PAYMENT` before each raise |
| `kodosumi/runner/main.py` | Pass `on_event=self._put_payment_event` into `_await_payment`; add `_put_payment_event()` helper method; add `agent_identifier` forward to `wait_for_funds_locked` |

#### Key Code Anchors

- `payment.py:16–33` — `PaymentError` hierarchy (add 3 subclasses here)
- `payment.py:181` — `wait_for_funds_locked` signature (add `agent_identifier`, `on_event` params)
- `payment.py:205–222` — wait loop (rewrite: `PaymentRejectedError` on terminal state, `BuyerNoActionTimeoutError` / `PaymentDeadlineTimeoutError` on deadline)
- `payment.py:202` — deadline parsing from milliseconds (add comment about `payByTime` epoch-ms vs ISO ambiguity)
- `runner/main.py:225–243` — `_await_payment` (pass `agent_identifier` and `on_event=self._put_payment_event`)
- `runner/main.py:237` — `payment["pay_conf"].get("agentIdentifier", "")` (already available)

#### Unit Tests

- `test_buyer_no_action_error_fields` — instantiate `BuyerNoActionTimeoutError`; assert `.blockchain_identifier/.network/.agent_identifier/.deadline_iso`; assert `isinstance(e, PaymentTimeoutError)`
- `test_payment_rejected_error_fields` — `PaymentRejectedError` with `on_chain_state="RefundRequested"`
- `test_deadline_timeout_error_fields` — `PaymentDeadlineTimeoutError` with `last_state`
- `test_deadline_expired_state_never_seen` — mock `get_payment_status` returning `None`; fake deadline past; assert `BuyerNoActionTimeoutError`
- `test_deadline_expired_state_none_whole_time` — mock returns `{"onChainState": None}`; assert `BuyerNoActionTimeoutError`
- `test_rejected_state_raises_immediately` — mock returns `{"onChainState": "RefundRequested"}`; deadline in future; assert `PaymentRejectedError` raised immediately
- `test_rejected_all_known_terminal_states` — parametrize over `KNOWN_TERMINAL_STATES`; assert each raises `PaymentRejectedError` before deadline
- `test_funds_locked_returns_payment` — mock returns `{"onChainState": "FundsLocked"}`; assert return without raising
- `test_on_event_callback_called_on_buyer_no_action` — `AsyncMock` as `on_event`; assert called once with `step="timeout", reason="buyer_no_action"`, correct `blockchainIdentifier`
- `test_on_event_callback_called_on_rejection` — `on_chain_state="RefundRequested"`; assert `reason="rejected"`, `onChainState="RefundRequested"`
- `test_on_event_none_does_not_raise` — omit `on_event`; deadline expires; assert `BuyerNoActionTimeoutError` raised with no `AttributeError`
- `test_agent_identifier_in_exception_message` — `agent_identifier="agent:abc"`; assert `str(exception)` contains `"agent:abc"`

#### Staging Tests (odin)

1. Start Sumi job on real Masumi Preprod agent; let `pay_by_time` deadline expire; verify: (a) `EVENT_PAYMENT` row with `step="timeout"`, `reason="buyer_no_action"` appears before `EVENT_ERROR`; (b) `EVENT_ERROR` contains `BuyerNoActionTimeoutError` with `blockchainIdentifier`; (c) Sumi status returns `status="error"`.
2. Trigger `PaymentRejectedError` via monkeypatch returning `RefundRequested`; verify `EVENT_PAYMENT` row with `reason="rejected"` and `on_chain_state="RefundRequested"`.
3. Verify backward compat: `except PaymentTimeoutError` in `_await_payment` catches all three new subclasses; execution reaches `STATUS_ERROR` cleanly.

#### Infra Snippet

None required.

#### Risk

**Low-Medium.** All changes are additive (new exception subclasses, new optional callback param, enriched event payload). Existing `except PaymentTimeoutError` catch-blocks continue to work. The only behavioral change: the non-FundsLocked branch (payment.py:214) now raises `PaymentRejectedError` immediately (not after deadline) — result is identical from Runner's perspective (`STATUS_ERROR`). Callback defaults to `None`; no existing caller breaks. Existing test coverage for `wait_for_funds_locked` deadline/rejection cases is thin — new unit tests provide the safety net.

#### Resolved Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Event emission from `MasumiClient` | Optional async `on_event` callback | Keeps event close to raise site; cannot be skipped by other callers; avoids circular import |
| `PaymentRejectedError` inheritance | Subclass of `PaymentTimeoutError` (kept for v1.2.0) | Existing catch-blocks continue to work; semantic refactor deferred to v1.3 |
| `last_state` tracking | Track for any non-FundsLocked non-None; treat only `KNOWN_TERMINAL_STATES` as immediately terminal | Defensive against Masumi API evolution introducing new intermediate states |

---

## Open Decisions for Sign-Off

The following decisions carry architecture, behavior, or payment-semantics implications that require human approval before implementation begins. All other open decisions in the 13 specs have been resolved above using the agent's recommendation.

---

### OD-1 — D1: Availability cache shared mutable state (Ticket #60)

**Question:** Should `_check_availability` add a 10-second TTL module-level dict cache (`_availability_cache: dict[str, tuple[float, AvailabilityResponse]] = {}`) to debounce burst calls from Sokosumi?

**Options:**
- No cache — dashboard calls are cheap; module-level mutable state is an anti-pattern in async Litestar
- **10s TTL dict cache** (recommended) — `time.monotonic()` for TTL; shared per Litestar worker process; acceptable staleness window

**Recommendation:** 10s TTL cache. The availability endpoint is called per-purchaser-check from Sokosumi; dashboard result is valid for ≥10s; cost of staleness (one incorrect "available" in a 10s window after a crash) is lower than N simultaneous dashboard calls. **Needs sign-off because it introduces shared mutable state at the module level.**

---

### OD-2 — D1: Dashboard unreachable fallback during boot step D (Ticket #73)

**Question:** When `check_app_running()` itself raises a `ConnectError` (dashboard temporarily unreachable), what state should step D write to `meta.state`?

**Options:**
- Write `"dead"` — conservative; agent disappears until next boot
- Write `"alive"` — optimistic; risks showing actually-dead agent as available
- **Fall back to HEAD probe as tiebreaker** (recommended) — `ConnectError` → `check_flow_health()` → write result of HEAD

**Recommendation:** HEAD tiebreaker. Limits blast radius of temporary dashboard outage without changing behaviour for the normal scale-to-zero case. **Needs sign-off because it determines what agents are visible in `/sumi/` during a partial outage.**

---

### OD-3 — D2 / #62 — **RESOLVED 2026-06-25: removed from release**

Decision: **not a kodosumi-core feature.** Fix is `min_replicas: 1` (+ `downscale_delay_s ≥ LOCK_EXPIRES`) in the HITL agents' expose `bootstrap` (`expose.db`, agent-dev owned). The code keep-alive (short ping) is ineffective and was rejected; lock-resume for crash resilience is a separate future ticket, not v1.2.0. #62 removed from milestone v1.2.0 and routed to agent-dev.

---

### OD-4 — D3 / #56 — **RESOLVED 2026-06-25: bound paid HITL wait by payment deadline (no guard, no refund change)**

**Business rule (fixed, owner):** HITL unresolved → refund (no result delivered). This is intentional and stays. kodosumi cannot know how long the human needs, so the earlier "pre-emptive guard" idea is **dropped**. `LOCK_EXPIRES` is **not** changed.

**The only fix:** for a **paid** job, when `submitResultTime` passes (the refund moment), the agent currently keeps waiting to the 3h lock and then hits a confusing 404 on submit. Instead → **end the lock cleanly with a clear `payment window expired → refunded` status** (mirrors `reconcile.py:72-73` for orphaned jobs). Refund outcome unchanged; just stop burning compute and report clearly. Non-paid HITL agents: unchanged (full `LOCK_EXPIRES`). This overlaps #54 (payment observability) and may share code.

_Files: bound the lock `expires` by `min(lock_expires, submitResultTime)` for paid jobs in `runner/main.py`; clear terminal status. No `config.py` `LOCK_EXPIRES` change._

---

### OD-5 — D5: Spooler health guard at both entry points (Ticket #74)

**Question:** Should the `check_spooler_health()` guard be applied only to `_submit_job()` (Sumi/paid path) or also to `Launch()` (browser panel path)?

**Options:**
- Only `_submit_job()` — protects revenue-critical path; panel users can retry
- **Both `Launch()` and `_submit_job()`** (recommended) — consistent policy; single helper call; ~2ms overhead per job start

**Recommendation:** Both entry points. **Needs sign-off because adding a `ray.get()` call to `Launch()` changes the latency profile of every browser-panel job start.**

---

### OD-6 — D10 / #75 — **RESOLVED 2026-06-25: per-service status + 200/503 (best practice)**

`/health` returns a simple per-service block and the HTTP status reflects overall health (IETF `health+json` convention):
```json
{ "status": "pass",                         // pass | warn | fail
  "services": { "spooler": "active", "panel": "active",
                "ray_head": "active", "ray_workers": "3/3 active" } }
```
- **HTTP 200** when `status` is `pass`/`warn`; **HTTP 503** when `status` is `fail` (e.g. spooler not draining, no alive worker). Errors are shown as errors → uptime/Prometheus fire automatically.
- Per-service derived from: spooler drain-liveness (see SpoolerLock false-health finding — check *actual draining*, not actor presence), `ray.nodes()` aggregated to alive/dead per role, panel self.
- **Verified safe (grep 2026-06-25):** only callers of `get_health_status()` are the `/health` route and the admin panel page (`panel.py:45`, direct function call). No internal code keys on the 200 status; boot checks (`check_flow_health`/`check_app_running`) hit Serve apps, not `/health`. Keep the existing raw `ray_status`/`spooler_status` fields (or update `panel.py`) for backward compatibility.

---

### OD-7 — D13: Immediate raise on non-FundsLocked terminal state (Ticket #54)

**Question:** Should `PaymentRejectedError` be raised immediately when any `KNOWN_TERMINAL_STATES` on-chain state is observed, or should the loop continue until the deadline before raising?

**Options:**
- **Raise immediately** (recommended) — terminal state is deterministic; continuing is wasteful and confusing
- Continue polling — in case Masumi transitions through intermediate states (defensive)

**Recommendation:** Raise immediately for `KNOWN_TERMINAL_STATES`. For unknown non-FundsLocked states (Masumi API evolution), continue polling until deadline and raise `PaymentDeadlineTimeoutError`. **Needs sign-off because this changes payment flow termination semantics: a `RefundRequested` observation now terminates the job immediately rather than waiting for the deadline.**

---

## Consolidated Staging Test Plan (odin)

Run all staging tests on `odin` in implementation order. Each sub-heading corresponds to the spec section.

### D12 — Structured Log Format
1. Deploy; `tail app.log`; start job via `/sumi/{expose}/start_job`; confirm JSON line with `"event":"sumi.start_job"` within 5s.
2. `ls /srv/kodosumi/data/sumi_debug.log` → must not exist or grow.
3. `logcli query '{job="kodosumi-app"} | json' --limit=5` → confirm `fid/event/agent` labels indexed.
4. Full job end-to-end; `tail spooler.log` → confirm valid JSON with `event=spooler.finished`.
5. Check all Grafana/Loki dashboards for regex-based panels; document any that need updating.

### D1 — Scale-to-Zero Probing
6. Deploy expose with `min_replicas=0`. Boot. `GET /sumi/` → expose present with `state="alive"`.
7. `GET /sumi/{expose}/{meta}/availability` while replicas=0 → `{status: "available"}`; verify `ray serve status` replica count unchanged.
8. 10 concurrent `/availability` requests; all <1s; replica count unchanged.
9. Simulate dashboard unreachable; verify HEAD fallback in `audit.log`.

### D7 — Severity Hygiene
10. Invalid MASUMI token; wait 5 min; `journalctl` shows ERROR for "Masumi sync failed".
11. Kill Ray actor; delete from panel; verify ERROR "failed to kill" (not CRITICAL), no 500.
12. Force reconcile with orphaned payment fixture; spooler.log shows WARNING for frozen jobs.

### D6 — Access-Log Verbosity
13. Baseline line count before deploy.
14. Deploy with `KODO_PANEL_ACCESS_LOG_LEVEL=WARNING`; confirm journald koco lines/h drops ~50%.
15. With `KODO_ACCESS_LOG_QUIET_PATHS=/timeline,...`; `GET /timeline` absent from stdout, present in `app.log` at DEBUG.
16. Deliberate 404 on quiet path; confirm WARNING in journald.
17. `KODO_SERVE_ACCESS_LOG=false`; boot; call sumi endpoint; confirm no Ray Serve access entries.

### D4 — Spooler Watchdog
18. Deploy `spooler.service` (Type=notify, WatchdogSec=120). `systemctl status spooler` → `active (running)` within 30s.
19. `systemctl show spooler -p WatchdogTimestampMonotonic` updating every ~1s.
20. Start spooler with Ray stopped; confirm `systemctl start` times out with `failed`.
21. `kill -STOP` spooler PID; after 120s confirm watchdog kill in journald.

### D5 — Spooler Drain + Fail-Loud
22. Kill spooler mid-flight; restart; verify no events missing; job eventually shows `finished`.
23. With spooler stopped: `POST /admin/{expose}/run` → HTTP 503; no Runner actor created.
24. With spooler stopped: `POST /sumi/{expose}/start_job` → error response; no Runner actor.
25. Restart spooler; confirm both entry points succeed without panel restart.

### D3 — Payment Deadline Guard
26. Sumi job with `submit_result_by_time=120`; no input; after 120s → status=`error` with `PaymentGuardError`.
27. Same job; provide input within 60s → job completes normally.
28. Panel-launched job with `tracer.lock()` → no `PaymentGuardError`; `_payment_deadline` stays `None`.

### D2 — HITL Keep-Alive
29. Agent with `tracer.lock()` waiting 15 min; verify Ray dashboard `ongoing_requests > 0` and replica count stable.
30. `provide_input` during lock wait → job completes; no "connection refused" in panel logs.
31. After lock resolves; `ray list actors` → Runner actor gone; keepalive not still running.
32. `KODO_HITL_KEEPALIVE_INTERVAL=0`; short lock (<5 min); no ping calls in Serve access logs.

### D8 — Correlation IDs
33. `POST start_job` with `identifier_from_purchaser='test-correlation-01'`; grep `app.log` for `start_job created fid=` within 2s with `sokosumi_job=test-correlation-01`.
34. Within 5s: grep `spooler.log` for `job_start fid=<same-fid>`; verify `sokosumi_job` and `sumi_endpoint` match.
35. `grep app.log 'sumi_debug.log'` → no matches.

### D11 — Boot Logging
36. `koco serve` → 5-line startup banner on stdout before first uvicorn log.
37. Full boot via Admin UI → `tail audit.log`; confirm BOOT EXPOSE RUNNING lines + BOOT SUMMARY.
38. Add expose with invalid `import_path`; boot; confirm `audit.log` has WARNING with `DEPLOY_FAILED` + reason substring.

### D9 — Lifecycle Events
39. Submit job; after completion: `SELECT kind, message FROM monitor WHERE kind='lifecycle'`; confirm `job_created`, `job_dispatched`, `container_ready`, `job_finished` rows.
40. Job sleeping >300s with `KODO_HUNG_JOB_THRESHOLD=60`; verify `job_hung` event in DB and `grep 'hung job detected' spooler.log`.
41. After job completes; wait 2×HEARTBEAT_INTERVAL; no new `job_heartbeat` rows.

### D10 — Dependency & Node Health
42. Stop ray-worker; wait 2×NODE_HEALTH_INTERVAL; `GET /health/` → `ray_nodes.dead_count >= 1`, `degraded==True`.
43. Open `/admin/routes` → red badge with dead worker name; goes green after restart.
44. Full boot flow still works after `/health` response-body change (regression).

### D13 — Payment Timeout Observability
45. Masumi Preprod job; let `pay_by_time` expire; verify `EVENT_PAYMENT` row with `reason="buyer_no_action"` before `EVENT_ERROR`.
46. Verify `except PaymentTimeoutError` catches all three new subclasses cleanly.

---

## Risk Register

| Ticket | Spec | Risk Level | Mitigation |
|--------|------|-----------|------------|
| #81 | D12-format | Medium-low | Audit promtail config before deploy; unit test asserts `json.loads()` succeeds on every emitted record |
| #73/#60 | D1-scaletozero | Medium-low | `ConnectError` fallback to HEAD; no schema changes; GET→HEAD downgrade is safe for FastAPI endpoints |
| ~~#62~~ | ~~D2-hitlkeepalive~~ | **Removed** | Out of v1.2.0 scope — agent-dev/expose.db config (`min_replicas:1`), not kodosumi-core. See OD-3. |
| #56 | D3-paymentguard | Low–medium | Runtime guard only; **no `LOCK_EXPIRES` change** (non-payment jobs unaffected); align on payment side via `submit_result_by_time` |
| #69 | D4-spoolerwatchdog | Low | `_sd_notify` is best-effort; no-op outside systemd; deploy kodosumi package atomically with unit file |
| #74 | D5-spoolerloss | Medium | 10s backstop alongside sentinel; `try/finally` ensures `ray.kill` always called; health check timeout 2s |
| #71 | D6-accesslog | Medium-low | In-memory only override for serve config; middleware falls back to `[]` on early startup; all defaults preserve current behaviour |
| #79 | D7-severity | Low | Pure log reclassifications; review Loki CRITICAL alert rules before deploy; pair with D6 |
| #70/#80 | D8-correlation | Low | `sumi_debug.log` removal — document; `_log_meta_correlation` wrapped in `except Exception: pass` |
| #76 | D9-lifecycle | Medium | Heartbeat task wrapped in broad `try/except`; same sqlite3 connection on same asyncio thread; negligible task count increase |
| #75/#77 | D10-dephealth | Medium | Keep `ray_status` alias for one release cycle; wrap `ray.nodes()` in `asyncio.to_thread()`; redis_mem is stub only |
| #78 | D11-bootlog | Low | `subprocess.run` has 2s timeout + `"unknown"` fallback; all changes additive; D12 format change isolated in helper function |
| #54 | D13-paymentobs | Low-Medium | All new exception subclasses; backward-compatible; behavioral change (immediate raise on terminal state) requires payment flow verification on staging |
