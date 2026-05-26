# Kodosumi API Cheatsheet

Quick reference for programmatic access to Kodosumi. All examples use `curl`.

## Authentication

```bash
# Login — returns JWT token
curl -X POST https://panel.kodosumi.io/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "name=admin&password=YOUR_PASSWORD"

# Response:
# {"name":"admin","KODOSUMI_API_KEY":"eyJhbG...","id":"ed94f6b7-..."}
```

Use the JWT token in any of these ways:

```bash
# Option 1: Authorization Bearer (standard)
curl -H "Authorization: Bearer eyJhbG..." https://panel.kodosumi.io/expose/

# Option 2: Custom header
curl -H "KODOSUMI_API_KEY: eyJhbG..." https://panel.kodosumi.io/expose/

# Option 3: Cookie
curl -b "kodosumi_jwt=eyJhbG..." https://panel.kodosumi.io/expose/
```

Token expires after 24 hours. Re-login to get a fresh one.

---

## Expose Management (JSON API)

### List all Exposes

```bash
curl -H "Authorization: Bearer $TOKEN" \
  https://panel.kodosumi.io/expose/
```

Response: Array of expose objects.

### Get single Expose

```bash
curl -H "Authorization: Bearer $TOKEN" \
  https://panel.kodosumi.io/expose/my_agent_cc
```

### Create or Update Expose

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  https://panel.kodosumi.io/expose/ \
  -d '{
    "name": "my_agent_cc",
    "enabled": true,
    "network": "Preprod",
    "meta": [{
      "url": "/my_agent_cc/",
      "data": "display: My Agent\ndescription: Does useful things\ntags:\n- AI\n- Demo\nauthor:\n  name: John Doe"
    }]
  }'
```

### Delete Expose

```bash
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  https://panel.kodosumi.io/expose/my_agent_cc
```

### Health Check (all)

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  https://panel.kodosumi.io/expose/health
```

### Health Check (single)

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  https://panel.kodosumi.io/expose/my_agent_cc/health
```

---

## Boot / Deployment (JSON API)

### Start Boot (deploy all enabled Exposes)

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  https://panel.kodosumi.io/boot/
```

Returns: Streaming response with deployment progress.

### Get Boot Status

```bash
curl -H "Authorization: Bearer $TOKEN" \
  https://panel.kodosumi.io/boot/
```

### Shutdown Ray Serve

```bash
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  https://panel.kodosumi.io/boot/
```

### Refresh single Expose

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  https://panel.kodosumi.io/boot/refresh/my_agent_cc
```

---

## Import / Export

### Export all Exposes

```bash
curl -H "Authorization: Bearer $TOKEN" \
  https://panel.kodosumi.io/exchange/export > exposes.json
```

### Import Exposes

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  https://panel.kodosumi.io/exchange/import \
  -d @exposes.json
```

---

## Flows (JSON API)

### List registered Flows

```bash
curl -H "Authorization: Bearer $TOKEN" \
  https://panel.kodosumi.io/flow/
```

### List Tags

```bash
curl -H "Authorization: Bearer $TOKEN" \
  https://panel.kodosumi.io/flow/tags
```

---

## Sumi Protocol (MIP-003 — External Job API)

Sumi endpoints are the standard way for external systems (Sokosumi, other marketplaces) to discover and run agents.

**Auth depends on the agent's configuration:**

| Agent Config | Auth Required? | Why |
|---|---|---|
| `network` set (Preprod/Mainnet) | No — public | Blockchain handles payment/auth |
| `network` not set | **Yes — JWT required** | No external auth system |

### List available services (always public)

```bash
curl https://panel.kodosumi.io/sumi/
```

### Check availability

```bash
# Public agent (has network):
curl https://panel.kodosumi.io/sumi/my_agent_cc/availability

# Private agent (no network) — needs JWT:
curl -H "Authorization: Bearer $TOKEN" \
  https://panel.kodosumi.io/sumi/my_private_agent/availability
```

### Get input schema (MIP-003)

```bash
curl https://panel.kodosumi.io/sumi/my_agent_cc/input_schema
```

### Start a job

```bash
# Paid agent (has agentIdentifier) — identifier_from_purchaser required:
curl -X POST https://panel.kodosumi.io/sumi/my_agent_cc/start_job \
  -H "Content-Type: application/json" \
  -d '{
    "input_data": {"question": "What is AI?"},
    "identifier_from_purchaser": "buyer-wallet-id"
  }'

# Free agent (no agentIdentifier) — no purchaser ID needed:
curl -X POST https://panel.kodosumi.io/sumi/my_free_agent/start_job \
  -H "Content-Type: application/json" \
  -d '{"input_data": {"question": "What is AI?"}}'

# Private agent (no network) — needs JWT:
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  https://panel.kodosumi.io/sumi/my_private_agent/start_job \
  -d '{"input_data": {"question": "What is AI?"}}'
```

Response: `{"job_id":"6a0b9877...","status":"running"}`

### Get job status

```bash
curl https://panel.kodosumi.io/sumi/my_agent_cc/status/6a0b9877...
```

---

## Monitoring (JSON API)

### System Health

```bash
curl -H "Authorization: Bearer $TOKEN" \
  https://panel.kodosumi.io/health/
```

### Running Agents

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "https://panel.kodosumi.io/api/dashboard/running-agents?hours=24"
```

### Masumi Payment Summary

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "https://panel.kodosumi.io/api/masumi/summary?network=Mainnet"
```

### Trigger Payment Sync

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  "https://panel.kodosumi.io/api/masumi/sync?network=Mainnet"
```

---

## User / Profile

### Get own profile (no operator required)

```bash
curl -H "Authorization: Bearer $TOKEN" \
  https://panel.kodosumi.io/role/profile
```

### List all users (operator only)

```bash
curl -H "Authorization: Bearer $TOKEN" \
  https://panel.kodosumi.io/role/
```

---

## HTML Pages (browser only, not for API)

These return HTML, not JSON:

| Path | Page |
|------|------|
| `/admin/flow` | Agent Services |
| `/admin/timeline/view` | Execution Timeline |
| `/admin/dashboard` | Analytics Dashboard |
| `/admin/expose` | Expose Management |
| `/admin/masumi` | Masumi Payment Dashboard |
| `/admin/routes` | Control / Settings |

---

## Quick Start: Deploy an Agent

```bash
# 1. Login
TOKEN=$(curl -s -X POST https://panel.kodosumi.io/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "name=admin&password=YOUR_PASSWORD" | python3 -c "import json,sys; print(json.load(sys.stdin)['KODOSUMI_API_KEY'])")

# 2. Create Expose
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  https://panel.kodosumi.io/expose/ \
  -d '{"name":"my_agent","enabled":true}'

# 3. Boot (deploy to Ray Serve)
curl -X POST -H "Authorization: Bearer $TOKEN" \
  https://panel.kodosumi.io/boot/

# 4. Check health
curl -X POST -H "Authorization: Bearer $TOKEN" \
  https://panel.kodosumi.io/expose/my_agent/health

# 5. Test via Sumi (no auth)
curl https://panel.kodosumi.io/sumi/my_agent/availability
```
