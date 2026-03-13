# MIP-003 HITL Extension Proposal

**Date:** 2026-03-04
**Status:** Draft for Discussion
**Author:** Kodosumi Team

---

## Summary

This document proposes an extension to MIP-003's Human-in-the-Loop (HITL) handling to support:
1. Multiple concurrent HITL requests per job
2. Explicit lock identification via `lock_id`
3. Clearer targeting in `/provide_input` requests

---

## Current MIP-003 Specification

### Status Response (awaiting_input)

```json
{
  "status": "awaiting_input",
  "input_schema": {
    "input_data": [
      {"id": "linkedin_url", "type": "string", "name": "LinkedIn URL", ...}
    ]
  }
}
```

### Provide Input Request

```json
{
  "job_id": "18d66eed-6af5-4589-b53a-d2e78af657b6",
  "input_schema_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "input_data": {"linkedin_url": "https://linkedin.com/in/alice"}
}
```

**Identification method:** `input_schema_hash` (SHA256 of canonical JSON of `input_schema`)

---

## Limitations Identified

### 1. Single HITL Assumption

The current spec assumes only one HITL request can be pending at a time per job. The `input_schema` is a single object, not an array.

**Problem:** Advanced agentic workflows (e.g., with subagents or parallel tasks) may require multiple concurrent human inputs.

### 2. Hash-Based Identification

Using `input_schema_hash` for identification has challenges:

| Aspect | Challenge |
|--------|-----------|
| **Change detection** | Works well - client can detect if schema changed |
| **Targeting** | Ambiguous with multiple HITLs - which one does the hash refer to? |
| **Statelessness** | Server must re-fetch and re-compute hash to validate |
| **Debugging** | Hash is opaque - harder to trace issues |

### 3. Missing Expiration Context

The current spec doesn't include expiration time for HITL requests, which is important for time-sensitive workflows.

---

## Proposed Extension

### Extended Status Response

Return `input_schema` as an **array** with explicit identifiers:

```json
{
  "status": "awaiting_input",
  "input_schema": [
    {
      "lock_id": "b22c9078-c128-4743-b4ce-ba31debc07fb",
      "expires_at": 1772669596.93,
      "input_data": [
        {
          "id": "response",
          "type": "textarea",
          "name": "Your Response",
          "data": {"placeholder": "Type your response..."},
          "validations": [{"validation": "optional", "value": "true"}]
        }
      ]
    }
  ]
}
```

#### New Fields

| Field | Type | Description |
|-------|------|-------------|
| `lock_id` | string (UUID) | Unique identifier for this specific HITL request |
| `expires_at` | number (Unix timestamp) | When this HITL request expires |

#### Rationale for Array

- Supports single HITL (array with 1 item) - backwards compatible
- Supports multiple concurrent HITLs (array with N items)
- Each item is independently identifiable and addressable

### Extended Provide Input Request

Accept `lock_id` for precise targeting:

```json
{
  "job_id": "69a8a05f2173c6326758a436",
  "lock_id": "b22c9078-c128-4743-b4ce-ba31debc07fb",
  "input_data": {"response": "try again"}
}
```

#### Identification Options

| Field | When to Use |
|-------|-------------|
| `lock_id` | **Recommended.** Direct, precise targeting. Works for single and multiple HITLs. |
| `input_schema_hash` | Optional. For change detection validation. Only works reliably with single HITL. |

#### Proposed Logic

```
IF lock_id provided:
   → Target that specific HITL (precise)

ELIF input_schema_hash provided AND single HITL pending:
   → Use that HITL (MIP-003 backwards compatibility)

ELIF input_schema_hash provided AND multiple HITLs pending:
   → Return error: "Multiple HITLs pending, please use lock_id"
```

---

## Compatibility

### Backwards Compatibility

| Scenario | Behavior |
|----------|----------|
| Single HITL + `input_schema_hash` | Works (existing MIP-003 flow) |
| Single HITL + `lock_id` | Works (new, recommended) |
| Multiple HITLs + `lock_id` | Works (extension) |
| Multiple HITLs + `input_schema_hash` | Error with clear message |

### Client Implementation

Clients (like Sokosumi) would:

1. Poll `/status` endpoint
2. Check if `status === "awaiting_input"`
3. Read `input_schema` array
4. For each item, render form using `input_data`
5. On submit, send `/provide_input` with `job_id` + `lock_id` from that item

---

## Use Cases

### Single HITL (Common Case)

Agent asks user for additional information mid-execution.

```
Job starts → Agent needs input → status: awaiting_input (1 lock) → User provides → Job continues
```

### Multiple HITLs (Advanced Case)

Parallel subagents each need human approval.

```
Job starts → Subagent A needs approval (lock_1)
           → Subagent B needs approval (lock_2)
           → User approves lock_1
           → User approves lock_2
           → Job continues
```

### Sequential HITLs

Agent asks multiple questions over time.

```
Job starts → First question (lock_1) → User answers → lock_1 released
           → Second question (lock_2) → User answers → lock_2 released
           → Job completes
```

---

## Summary of Proposed Changes

| Aspect | Current MIP-003 | Proposed Extension |
|--------|-----------------|-------------------|
| `input_schema` type | Object | Array of objects |
| HITL identifier | `input_schema_hash` | `lock_id` (UUID) |
| Multiple HITLs | Not supported | Supported |
| Expiration | Not included | `expires_at` field |
| `/provide_input` targeting | Hash-based | `lock_id` (preferred) or hash |

---

## Questions for Discussion

1. **Naming:** Is `lock_id` clear, or would `status_id` / `input_id` be better for MIP-003 consistency?

2. **Hash retention:** Should we keep `input_schema_hash` support for change detection, or is `lock_id` sufficient?

3. **Array vs Object:** For single HITL, should clients expect array with 1 item, or should we support both object (single) and array (multiple)?

4. **Expiration handling:** What should happen when a client submits to an expired lock?

---

## References

- [MIP-003 Specification](https://docs.masumi.network/mips/_mip-003)
- [MIP-003 GitHub](https://github.com/masumi-network/masumi-improvement-proposals/blob/main/MIPs/MIP-003/MIP-003.md)
- Kodosumi HITL Implementation: `/sumi/lock/{fid}/{lid}`
