# Masumi Registry Integration — UX Proposal

**Date:** 2026-03-13
**Status:** Draft
**Context:** Expose Edit Page (`/admin/expose/edit/{name}`)

---

## Current State

The expose edit page has 3 sections:

1. **Basic Information** — Name, Display, Network (dropdown), Enabled, State
2. **Bootstrap** — Ray Serve YAML
3. **Flows** — Per-flow meta YAML with Sumi API URL

Registration on the Masumi Registry is currently a **manual curl workflow** (see `docs/masumi-registry.mdx`). The `agentIdentifier` must be manually pasted into the meta YAML.

---

## Goal

Add a **"Masumi Registry"** section to the expose edit page that allows:

1. Selecting a wallet
2. Configuring pricing
3. Registering the agent on-chain
4. Polling for confirmation
5. Auto-saving the `agentIdentifier` into the meta YAML

---

## Proposed Layout

```
┌─────────────────────────────────────────────────────┐
│ Basic Information                                    │
│  Name: [googlemapsintel_cc]  Display: [Google Maps..]│
│  Network: [Preprod ▾]       ☑ Enabled   State: RUN  │
├─────────────────────────────────────────────────────┤
│ Bootstrap                                            │
│  [YAML editor]                                       │
├─────────────────────────────────────────────────────┤
│ Masumi Registry                              NEW     │
│                                                      │
│  ┌─ Registration Status ──────────────────────────┐  │
│  │  ● Not Registered                              │  │
│  │    or                                          │  │
│  │  ● RegistrationConfirmed                       │  │
│  │    Agent ID: 7e8bdaf2...24fd  [📋 Copy]        │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  Wallet: [new one (2a5815...) ▾]                     │
│                                                      │
│  Pricing:                                            │
│    Type: [Fixed ▾]                                   │
│    Amount: [0.01]  Currency: [USDM ▾]                │
│                                                      │
│  [Register Agent]  [Deregister]                      │
│                                                      │
│  ┌─ Transaction ──────────────────────────────────┐  │
│  │  Tx: 3e5ca665...  Status: Confirmed            │  │
│  │  Block: 4504654   Fees: 376508 lovelace        │  │
│  └────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────┤
│ Flows                                                │
│  [/googlemapsintel_cc/analyze]                       │
│  Sumi API URL: https://staging.kodosumi.io/sumi/...  │
│  [YAML editor — agentIdentifier auto-injected]       │
└─────────────────────────────────────────────────────┘
```

---

## Section: Masumi Registry

### Prerequisites

- **Network must be set** (Preprod or Mainnet) in Basic Information. If not set, show: "Set network to enable Masumi registration."
- **KODO_MASUMI env var** must be configured. If missing, show: "KODO_MASUMI not configured. Set it to enable registry."
- **At least one flow** must exist (for the `apiBaseUrl`).

### Fields

| Field | Type | Source | Description |
|-------|------|--------|-------------|
| Registration Status | Read-only badge | Registry API | `Not Registered`, `RegistrationRequested`, `RegistrationInitiated`, `RegistrationConfirmed`, `RegistrationFailed` |
| Agent Identifier | Read-only + copy | Registry API | Shown after confirmation. Auto-saved to meta YAML. |
| Wallet | Dropdown | `GET /payment-source` | Lists all `SellingWallets` for the selected network. Shows wallet name + abbreviated vkey. |
| Pricing Type | Dropdown | User input | `Free`, `Fixed` |
| Amount | Number input | User input | Only shown when `Fixed`. Human-readable (e.g. `0.01`, not `10000`). |
| Currency | Dropdown | Hardcoded | `USDM` (tUSDM on Preprod), `ADA`. Maps to unit hex internally. |
| Transaction | Read-only card | Registry API | Tx hash, status, block, fees. Shown during/after registration. |

### Actions

| Button | When Visible | Action |
|--------|-------------|--------|
| **Register Agent** | Not registered or failed | POST to `/registry` with computed fields |
| **Deregister** | Confirmed | POST to `/registry/deregister` |
| **Refresh Status** | Any registered state | GET from registry, update display |

### Registration Flow (UX)

```
User clicks [Register Agent]
    │
    ▼
Button changes to spinner: "Submitting..."
    │
    ▼
POST /registry → success
    │
    ▼
Status badge: "RegistrationRequested" (yellow)
Auto-poll starts (every 10s)
    │
    ▼
Status badge: "RegistrationInitiated" (blue)
Tx hash shown
    │
    ▼
Status badge: "RegistrationConfirmed" (green) ✓
agentIdentifier shown
    │
    ▼
Auto-inject agentIdentifier into meta YAML
Auto-inject agentPricing into meta YAML
Show success toast: "Agent registered on-chain"
    │
    ▼
[Register Agent] hidden, [Deregister] shown
```

### Error States

| Error | Display |
|-------|---------|
| `UTxO Fully Depleted` | "Wallet has no available UTxOs. Try a different wallet or wait for pending transactions." |
| `RegistrationFailed` | Show error message from API. Enable [Register Agent] for retry. |
| Network timeout | "Could not reach Masumi API. Check KODO_MASUMI configuration." |
| No wallets found | "No selling wallets found for {network}. Create one in the Masumi Payment dashboard." |

### Auto-Inject into Meta YAML

When registration confirms, automatically add/update these fields in the flow meta YAML:

```yaml
# These are auto-managed by Masumi Registry integration:
agentIdentifier: "7e8bdaf2..."
agentPricing:
  - pricingType: Fixed
    fixedPricing:
      - amount: "10000"
        unit: "16a55b2a...745553444d"
```

If the fields already exist in the YAML, update them. If not, append them.

---

## Backend API (New Endpoints)

New endpoints under `/expose/` to support the UI:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/expose/{name}/registry` | Get current registration status from Masumi API |
| `POST` | `/expose/{name}/registry` | Register agent (computes apiBaseUrl, maps pricing) |
| `DELETE` | `/expose/{name}/registry` | Deregister agent |
| `GET` | `/expose/{name}/wallets` | List available wallets for the expose's network |

### `GET /expose/{name}/registry`

Checks if the agent is registered. Looks up by:
1. `agentIdentifier` from meta YAML (if present)
2. Otherwise searches by name in the registry

Returns:
```json
{
  "registered": true,
  "state": "RegistrationConfirmed",
  "agentIdentifier": "7e8bdaf2...",
  "name": "Expose: Google Maps Intelligence with HITL (Bansumi)",
  "pricing": {"type": "Fixed", "amount": "0.01", "currency": "USDM"},
  "wallet": {"vkey": "2a5815...", "address": "addr_test1..."},
  "transaction": {"txHash": "3e5ca6...", "status": "Confirmed", "block": 4504654}
}
```

### `POST /expose/{name}/registry`

Body:
```json
{
  "walletVkey": "2a581594...",
  "pricingType": "Fixed",
  "amount": "0.01",
  "currency": "USDM"
}
```

The backend:
1. Reads expose meta (display, description, tags, author, capability)
2. Computes `apiBaseUrl` from `KODO_SUMI_ADDRESS` + flow URL
3. Converts pricing (human amount → base units, currency → hex unit)
4. POSTs to Masumi Registry API
5. Returns registration response

---

## Currency Mapping (Backend)

| Currency | Network | Unit (hex) | Decimals |
|----------|---------|-----------|----------|
| USDM | Preprod (tUSDM) | `16a55b2a349361ff88c03788f93e1e966e5d689605d044fef722ddde0014df10745553444d` | 6 |
| USDM | Mainnet | TBD | 6 |
| ADA | Any | `""` (empty) | 6 |

Conversion: `base_amount = human_amount × 10^decimals`
Example: `0.01 USDM → 10000 base units`

---

## Implementation Order

1. **Backend**: New endpoints (`/expose/{name}/registry`, `/wallets`)
2. **Frontend**: Masumi Registry section in expose edit template
3. **Auto-inject**: YAML update logic for agentIdentifier + pricing
4. **Polling**: JS polling with status badge updates
5. **Network dropdown**: Filter wallets by selected network

---

## Open Questions

1. **Wallet naming**: The Masumi API doesn't return wallet names ("new one", "one"). Should we maintain a local name mapping, or show vkey abbreviations only?

2. **Multi-flow exposes**: If an expose has multiple flows, which URL becomes the `apiBaseUrl`? First flow? All flows registered separately?

3. **Display name prefix**: We used "Expose: {display}" as the registry name. Should this be configurable or always auto-prefixed?

4. **Deregistration + re-registration**: When deregistering, should we also remove the `agentIdentifier` from meta YAML? What about the pricing fields?

5. **Network change**: If the user changes the network dropdown (e.g. Preprod → Mainnet), what happens to an existing registration? Show warning?
