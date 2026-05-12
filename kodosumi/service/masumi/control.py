"""
Masumi Payment Dashboard API controller.

Provides backend endpoints that aggregate payment data from the local
MasumiCache SQLite database and expose.db for display in the frontend
Masumi Dashboard.
"""

import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite
import httpx
import yaml

from litestar import Controller, get, post
from litestar.datastructures import State

from kodosumi.service.masumi.cache import MasumiCache, get_cache, classify_payment

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Koios API constants
# ---------------------------------------------------------------------------

KOIOS_MAINNET = "https://api.koios.rest/api/v1"
KOIOS_PREPROD = "https://preprod.koios.rest/api/v1"

# USDM policy IDs (without asset name)
USDM_POLICY = {
    "Mainnet": "c48cbb3d5e57ed56e276bc45f99ab39abe94e6cd7ac39fb402da47ad",
    "Preprod": "16a55b2a349361ff88c03788f93e1e966e5d689605d044fef722ddde",
}

# USDM full on-chain unit hex per network
USDM_UNIT = {
    "Mainnet": "c48cbb3d5e57ed56e276bc45f99ab39abe94e6cd7ac39fb402da47ad0014df105553444d",
    "Preprod": "16a55b2a349361ff88c03788f93e1e966e5d689605d044fef722ddde0014df10745553444d",
}

CURRENCY_DECIMALS = 6


def _base_to_human(amount: int) -> float:
    """Convert base units (integer) to human-readable amount (divide by 1_000_000)."""
    return round(amount / (10 ** CURRENCY_DECIMALS), 6)


def _lovelace_to_ada(lovelace: int) -> float:
    """Convert lovelace to ADA (divide by 1_000_000)."""
    return round(lovelace / 1_000_000, 6)


# ---------------------------------------------------------------------------
# Agent name mapping helper
# ---------------------------------------------------------------------------

async def _get_agent_map(expose_db_path: Path) -> Dict[str, str]:
    """
    Build a mapping of agentIdentifier prefix (60 chars) to display name.

    Reads from expose.db for all rows that have a non-null network column,
    which indicates Masumi-enabled agents.
    """
    mapping: Dict[str, str] = {}
    if not expose_db_path.exists():
        return mapping

    try:
        async with aiosqlite.connect(str(expose_db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT name, display, meta FROM expose WHERE network IS NOT NULL"
            ) as cursor:
                rows = [dict(r) for r in await cursor.fetchall()]
    except Exception as exc:
        logger.warning("Could not read expose.db for agent map: %s", exc)
        return mapping

    for row in rows:
        meta_str = row.get("meta") or ""
        if not meta_str:
            continue
        try:
            outer = yaml.safe_load(meta_str)
            if not isinstance(outer, list) or not outer:
                continue
            inner_str = outer[0].get("data") or ""
            inner = yaml.safe_load(inner_str) if inner_str else {}
            if not isinstance(inner, dict):
                continue
            ai = inner.get("agentIdentifier") or ""
            if ai:
                label = (
                    inner.get("display")
                    or row.get("display")
                    or row.get("name")
                    or ai[:20]
                )
                mapping[ai[:60]] = label
        except Exception:
            pass

    return mapping


async def _resolve_unknown_agents(
    unknown_prefixes: List[str],
    cache_db_path: Path,
    masumi_configs: dict,
) -> Dict[str, str]:
    """Look up unknown agentIdentifier prefixes in the Masumi Registry."""
    from kodosumi.service.expose.registry import get_registration_status

    resolved: Dict[str, str] = {}
    if not unknown_prefixes or not masumi_configs:
        return resolved

    # Get full identifiers from cache
    full_ids: Dict[str, str] = {}
    try:
        async with aiosqlite.connect(str(cache_db_path)) as conn:
            for prefix in unknown_prefixes[:20]:
                async with conn.execute(
                    "SELECT DISTINCT agent_identifier FROM payments "
                    "WHERE agent_identifier LIKE ? LIMIT 1",
                    (prefix + "%",),
                ) as cursor:
                    row = await cursor.fetchone()
                    if row and row[0]:
                        full_ids[prefix] = row[0]
    except Exception:
        return resolved

    for prefix, full_id in full_ids.items():
        for cfg in masumi_configs.values():
            try:
                reg = await get_registration_status(
                    cfg, agent_identifier=full_id
                )
                if reg:
                    name = reg.get("name") or ""
                    if not name:
                        meta = reg.get("Metadata", {})
                        name = meta.get("name", "")
                    if name:
                        resolved[prefix] = f"{name} (Legacy)"
                        break
            except Exception:
                continue

    return resolved


# ---------------------------------------------------------------------------
# Koios wallet balance helper
# ---------------------------------------------------------------------------

async def _fetch_koios_balances(
    addresses: List[str], registry_network: str
) -> Dict[str, Dict[str, float]]:
    """
    Query Koios for ADA and USDM balances for a list of addresses.

    Returns a dict keyed by address with ``{"ada": float, "usdm": float}``.
    """
    if not addresses:
        return {}

    base = KOIOS_MAINNET if registry_network == "Mainnet" else KOIOS_PREPROD
    usdm_unit = USDM_UNIT.get(registry_network, USDM_UNIT["Mainnet"])

    result: Dict[str, Dict[str, float]] = {
        addr: {"ada": 0.0, "usdm": 0.0} for addr in addresses
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{base}/address_info",
                json={"_addresses": addresses},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            if resp.status_code != 200:
                logger.warning("Koios address_info failed: HTTP %s", resp.status_code)
                return result

            data = resp.json()
            if not isinstance(data, list):
                return result

            for entry in data:
                addr = entry.get("address") or ""
                if addr not in result:
                    continue
                # ADA balance in lovelace
                lovelace = int(entry.get("balance") or 0)
                result[addr]["ada"] = _lovelace_to_ada(lovelace)

                # USDM balance from UTxO token list
                for utxo in entry.get("utxo_set") or []:
                    for asset in utxo.get("asset_list") or []:
                        unit = (asset.get("policy_id") or "") + (asset.get("asset_name") or "")
                        if unit == usdm_unit:
                            qty = int(asset.get("quantity") or 0)
                            result[addr]["usdm"] = result[addr].get("usdm", 0.0) + _base_to_human(qty)
    except Exception as exc:
        logger.warning("Koios balance query failed: %s", exc)

    return result


# ---------------------------------------------------------------------------
# Period filtering helper (used by /agents)
# ---------------------------------------------------------------------------

_PERIOD_CUTOFFS = {
    "today": lambda now: now.replace(hour=0, minute=0, second=0, microsecond=0),
    "yesterday": lambda now: (
        now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    ),
    "7d": lambda now: now - timedelta(days=7),
    "30d": lambda now: now - timedelta(days=30),
    "total": lambda now: None,
}


def _period_start(period: str) -> Optional[datetime]:
    """Return UTC start datetime for the given period label, or None for 'total'."""
    now = datetime.now(timezone.utc)
    factory = _PERIOD_CUTOFFS.get(period)
    if factory is None:
        return None
    return factory(now)


def _period_end(period: str) -> Optional[datetime]:
    """Return UTC end datetime for 'yesterday' (exclusive), or None otherwise."""
    if period == "yesterday":
        now = datetime.now(timezone.utc)
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    return None


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------


class MasumiDashboardAPI(Controller):
    """API endpoints for the Masumi Payment Dashboard."""

    tags = ["Masumi"]

    # Guards are applied at the Router level in app.py.

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_cache(self, state: State) -> MasumiCache:
        """Return a MasumiCache instance for the configured data directory."""
        return get_cache(state["settings"].EXEC_DIR)

    def _expose_db_path(self, state: State) -> Path:
        """Return the path to expose.db."""
        from kodosumi.service.expose.db import EXPOSE_DATABASE
        return Path(EXPOSE_DATABASE)

    def _get_masumi_config(self, state: State, network: str):
        """Get MasumiConfig for the given network (raises ValueError if not found)."""
        return state["settings"].get_masumi(network)

    # ------------------------------------------------------------------
    # GET /networks
    # ------------------------------------------------------------------

    @get("/networks", summary="List Masumi networks")
    async def get_networks(self, state: State) -> Dict[str, Any]:
        """
        Return the list of configured Masumi networks derived from
        KODO_MASUMI / KODO_MASUMI0..9 environment variables.
        """
        networks_cfg = state["settings"].masumi_networks
        networks = [
            {
                "name": cfg.network,
                "base_url": cfg.base_url,
                "registry_network": cfg.registry_network,
            }
            for cfg in networks_cfg.values()
        ]
        return {"networks": networks}

    # ------------------------------------------------------------------
    # GET /summary
    # ------------------------------------------------------------------

    @get("/summary", summary="Revenue summary by period")
    async def get_summary(
        self,
        state: State,
        network: str = "Mainnet",
    ) -> Dict[str, Any]:
        """
        Aggregate revenue cards, per-period breakdown and failure breakdown
        for the given Masumi network.

        All monetary amounts are returned in human-readable units
        (base units divided by 1_000_000).
        """
        cache = self._get_cache(state)
        await cache.init_db()

        periods_raw = await cache.get_revenue_summary(network)

        # Convert base unit amounts to human-readable
        periods_out = []
        for label, p in periods_raw.items():
            delivered = p.get("delivered", 0)
            utxo_fail = p.get("utxo_fail", 0)
            timeout_count = p.get("timeout", 0)
            buyer_invalid = p.get("buyer_invalid", 0)
            paid_attempts = delivered + utxo_fail + timeout_count
            rate = round(delivered / paid_attempts * 100, 1) if paid_attempts > 0 else 0.0
            periods_out.append({
                "period": label,
                "revenue": _base_to_human(p.get("revenue", 0)),
                "delivered": delivered,
                "utxo_fail": utxo_fail,
                "timeout": timeout_count,
                "buyer_invalid": buyer_invalid,
                "rate": rate,
            })

        # Build cards from "total" period
        total = periods_raw.get("total", {})
        total_delivered = total.get("delivered", 0)
        total_revenue_base = total.get("revenue", 0)
        total_utxo = total.get("utxo_fail", 0)
        total_timeout = total.get("timeout", 0)
        total_buyer_invalid = total.get("buyer_invalid", 0)
        total_disputed = total.get("disputed", 0)
        total_pending = total.get("pending", 0)
        total_paid = total_delivered + total_utxo + total_timeout + total_buyer_invalid + total_disputed

        lost_attempts = total_utxo + total_timeout + total_disputed
        lost_revenue_base = 0

        # Compute delivery rate
        if total_paid > 0:
            delivery_rate = round(total_delivered / total_paid * 100, 1)
        else:
            delivery_rate = 0.0

        # Compute agent errors (total paid minus delivered = all non-delivery outcomes)
        agent_errors = total_timeout

        # For lost_revenue we need per-record amounts — pull from failure breakdown below
        # We'll compute it together with the breakdown query
        async with aiosqlite.connect(str(cache.db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT on_chain_state, requested_amount, error_note, result_hash
                FROM payments WHERE network = ?
                """,
                (network,),
            ) as cursor:
                all_rows = [dict(r) for r in await cursor.fetchall()]

        failure_buckets: Dict[str, Dict[str, Any]] = {
            "masumi_utxo": {"count": 0, "amount": 0,
                            "label": "UTxO Exhausted",
                            "responsibility": "Masumi Infrastructure"},
            "masumi_state_error": {"count": 0, "amount": 0,
                                   "label": "State Error",
                                   "responsibility": "Masumi Infrastructure"},
            "buyer_invalid": {"count": 0, "amount": 0,
                              "label": "Invalid Payment",
                              "responsibility": "Buyer/External"},
            "unresolved_timeout": {"count": 0, "amount": 0,
                                   "label": "Payment Timeout / Agent Error",
                                   "responsibility": "Mixed"},
            "disputed": {"count": 0, "amount": 0,
                         "label": "Disputed",
                         "responsibility": "Unclear"},
        }

        for row in all_rows:
            cat = classify_payment(row)
            amt = row.get("requested_amount") or 0
            if cat in failure_buckets:
                failure_buckets[cat]["count"] += 1
                if cat != "buyer_invalid":
                    failure_buckets[cat]["amount"] += amt

        # Convert failure amounts to human-readable
        for bucket in failure_buckets.values():
            bucket["amount"] = _base_to_human(bucket["amount"])

        lost_revenue = sum(b["amount"] for b in failure_buckets.values())

        # Sync state
        async with aiosqlite.connect(str(cache.db_path)) as conn:
            async with conn.execute(
                "SELECT last_sync_at, total_synced FROM sync_state WHERE network = ?",
                (network,),
            ) as cursor:
                sync_row = await cursor.fetchone()

        last_sync = sync_row[0] if sync_row else None
        total_cached = sync_row[1] if sync_row else 0

        cards = {
            "revenue": _base_to_human(total_revenue_base),
            "delivery_rate": delivery_rate,
            "lost_revenue": lost_revenue,
            "pending": total_pending,
            "total_paid": total_paid,
            "delivered": total_delivered,
            "agent_errors": agent_errors,
        }

        return {
            "cards": cards,
            "periods": periods_out,
            "failure_breakdown": failure_buckets,
            "last_sync": last_sync,
            "total_cached": total_cached or await cache.get_payment_count(network),
        }

    # ------------------------------------------------------------------
    # GET /agents
    # ------------------------------------------------------------------

    @get("/agents", summary="Per-agent revenue breakdown")
    async def get_agents(
        self,
        state: State,
        network: str = "Mainnet",
        period: str = "total",
    ) -> Dict[str, Any]:
        """
        Return per-agent revenue and failure statistics for the given network.

        The ``period`` parameter filters records to: ``today``, ``yesterday``,
        ``7d``, ``30d``, or ``total`` (no filter).

        All monetary amounts are in human-readable units (base / 1_000_000).
        """
        cache = self._get_cache(state)
        await cache.init_db()

        expose_db = self._expose_db_path(state)
        agent_map = await _get_agent_map(expose_db)

        # Determine time window for filtering
        start_dt = _period_start(period)
        end_dt = _period_end(period)

        # Fetch all payments for the network (period filter applied in Python)
        async with aiosqlite.connect(str(cache.db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT agent_identifier, on_chain_state, requested_amount,
                       error_note, result_hash, created_at
                FROM payments
                WHERE network = ?
                """,
                (network,),
            ) as cursor:
                rows = [dict(r) for r in await cursor.fetchall()]

        # Apply period filter
        if start_dt is not None or end_dt is not None:
            filtered = []
            for row in rows:
                raw_ts = row.get("created_at") or ""
                try:
                    ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except (ValueError, AttributeError):
                    continue
                if start_dt is not None and ts < start_dt:
                    continue
                if end_dt is not None and ts >= end_dt:
                    continue
                filtered.append(row)
            rows = filtered

        # Bucket by agent identifier prefix
        def _empty_bucket(identifier: str, name: str) -> Dict[str, Any]:
            return {
                "name": name,
                "expose_name": identifier,
                "revenue": 0,
                "delivered": 0,
                "utxo_fail": 0,
                "timeout": 0,
                "buyer_invalid": 0,
                "disputed": 0,
                "pending": 0,
                "lost_revenue": 0,
            }

        def _match_agent(agent_id: Optional[str]) -> str:
            if not agent_id:
                return "unknown"
            best = ""
            for prefix in agent_map:
                if agent_id.startswith(prefix) and len(prefix) > len(best):
                    best = prefix
            return best or "unknown"

        buckets: Dict[str, Dict[str, Any]] = {}
        # Pre-seed with configured agents so they show up even at zero
        for prefix, name in agent_map.items():
            buckets[prefix] = _empty_bucket(prefix, name)

        for row in rows:
            key = _match_agent(row.get("agent_identifier"))
            if key not in buckets:
                name = agent_map.get(key, key if key != "unknown" else "Unknown")
                buckets[key] = _empty_bucket(key, name)

            bucket = buckets[key]
            amount = row.get("requested_amount") or 0
            cat = classify_payment(row)

            if cat == "delivered":
                bucket["delivered"] += 1
                bucket["revenue"] += amount
            elif cat in ("masumi_utxo", "masumi_state_error"):
                bucket["utxo_fail"] += 1
                bucket["lost_revenue"] += amount
            elif cat == "unresolved_timeout":
                bucket["timeout"] += 1
                bucket["lost_revenue"] += amount
            elif cat == "buyer_invalid":
                bucket["buyer_invalid"] += 1
            elif cat == "disputed":
                bucket["disputed"] += 1
                bucket["lost_revenue"] += amount
            elif cat == "pending":
                bucket["pending"] += 1

        # Convert base amounts, compute rate, sort
        agents_out = []
        for key, b in buckets.items():
            if key == "unknown" and b["delivered"] == 0 and b["utxo_fail"] == 0 and b["timeout"] == 0:
                continue
            paid_attempts = b["delivered"] + b["utxo_fail"] + b["timeout"]
            rate = round(b["delivered"] / paid_attempts * 100, 1) if paid_attempts > 0 else 0.0
            agents_out.append({
                "name": b["name"],
                "expose_name": b["expose_name"],
                "revenue": _base_to_human(b["revenue"]),
                "delivered": b["delivered"],
                "utxo_fail": b["utxo_fail"],
                "timeout": b["timeout"],
                "buyer_invalid": b["buyer_invalid"],
                "rate": rate,
                "lost_revenue": _base_to_human(b["lost_revenue"]),
            })

        # Resolve "Unknown" agents via Masumi Registry lookup
        unknown_prefixes = [
            a["expose_name"] for a in agents_out
            if a["name"] in ("Unknown", "unknown") and a["expose_name"] != "unknown"
        ]
        if unknown_prefixes:
            try:
                cache = self._get_cache(state)
                resolved = await _resolve_unknown_agents(
                    unknown_prefixes, cache.db_path,
                    state["settings"].masumi_networks,
                )
                for a in agents_out:
                    if a["expose_name"] in resolved:
                        a["name"] = resolved[a["expose_name"]]
            except Exception:
                pass

        agents_out.sort(key=lambda d: d["revenue"], reverse=True)
        return {"agents": agents_out}

    # ------------------------------------------------------------------
    # GET /trend
    # ------------------------------------------------------------------

    @get("/trend", summary="Weekly revenue trend")
    async def get_trend(
        self,
        state: State,
        network: str = "Mainnet",
        weeks: int = 10,
    ) -> Dict[str, Any]:
        """
        Return a per-week revenue and failure summary for the last *weeks*
        calendar weeks.

        Revenue values are converted to human-readable units (base / 1_000_000).
        """
        cache = self._get_cache(state)
        await cache.init_db()

        weekly_raw = await cache.get_weekly_trend(network=network, weeks=weeks)

        weeks_out = []
        for entry in weekly_raw:
            weeks_out.append({
                "week": entry["week"],
                "revenue": _base_to_human(entry.get("revenue", 0)),
                "delivered": entry.get("delivered", 0),
                "utxo_fail": entry.get("utxo_fail", 0),
                "timeout": entry.get("timeout", 0),
                "rate": round(entry.get("rate", 0.0) * 100, 1),
            })

        return {"weeks": weeks_out}

    # ------------------------------------------------------------------
    # GET /wallets
    # ------------------------------------------------------------------

    @get("/wallets", summary="Wallet balances from Cardano blockchain")
    async def get_wallets(
        self,
        state: State,
        network: str = "Mainnet",
    ) -> Dict[str, Any]:
        """
        Fetch wallet addresses from Masumi /payment-source and query
        the Koios API for ADA and USDM balances.

        Returns selling wallets, buying wallets and combined totals.
        """
        try:
            masumi_cfg = self._get_masumi_config(state, network)
        except ValueError as exc:
            return {
                "selling_wallets": [],
                "buying_wallets": [],
                "totals": {"ada": 0.0, "usdm": 0.0},
                "error": str(exc),
            }

        from kodosumi.service.expose.registry import list_wallets as _list_masumi_wallets
        from kodosumi.helper import HTTPXClient

        # Fetch selling wallets from Masumi payment-source endpoint
        selling_raw = await _list_masumi_wallets(masumi_cfg)

        # Also fetch buying wallets from the same endpoint
        buying_raw: List[Dict[str, Any]] = []
        try:
            url = (
                f"{masumi_cfg.base_url}/payment-source"
                f"?network={masumi_cfg.registry_network}"
            )
            headers = {"accept": "application/json", "token": masumi_cfg.token}
            async with HTTPXClient() as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json().get("data", {})
                    for source in data.get("PaymentSources", []):
                        for wallet in source.get("BuyingWallets", []):
                            buying_raw.append({
                                "walletAddress": wallet.get("walletAddress", ""),
                                "note": wallet.get("note", ""),
                            })
        except Exception as exc:
            logger.warning("Could not fetch buying wallets: %s", exc)

        # Collect all addresses for Koios bulk query
        selling_addresses = [w.get("walletAddress", "") for w in selling_raw if w.get("walletAddress")]
        buying_addresses = [w.get("walletAddress", "") for w in buying_raw if w.get("walletAddress")]
        all_addresses = list(set(selling_addresses + buying_addresses))

        balances = await _fetch_koios_balances(all_addresses, masumi_cfg.registry_network)

        selling_out = []
        for w in selling_raw:
            addr = w.get("walletAddress", "")
            bal = balances.get(addr, {"ada": 0.0, "usdm": 0.0})
            selling_out.append({
                "note": w.get("note", ""),
                "address": addr,
                "ada": bal["ada"],
                "usdm": bal["usdm"],
            })

        buying_out = []
        for w in buying_raw:
            addr = w.get("walletAddress", "")
            bal = balances.get(addr, {"ada": 0.0, "usdm": 0.0})
            buying_out.append({
                "note": w.get("note", ""),
                "address": addr,
                "ada": bal["ada"],
                "usdm": bal["usdm"],
            })

        total_ada = sum(b["ada"] for b in balances.values())
        total_usdm = sum(b["usdm"] for b in balances.values())

        return {
            "selling_wallets": selling_out,
            "buying_wallets": buying_out,
            "totals": {"ada": round(total_ada, 6), "usdm": round(total_usdm, 6)},
        }

    # ------------------------------------------------------------------
    # POST /sync
    # ------------------------------------------------------------------

    @post("/sync", summary="Trigger payment cache sync")
    async def trigger_sync(
        self,
        state: State,
        network: str = "Mainnet",
    ) -> Dict[str, Any]:
        """
        Manually trigger a sync of payment data from the Masumi Payment API
        into the local cache database.
        """
        try:
            masumi_cfg = self._get_masumi_config(state, network)
        except ValueError as exc:
            return {"success": False, "error": str(exc), "new_records": 0, "network": network}

        cache = self._get_cache(state)
        await cache.init_db()

        try:
            new_records = await cache.sync_payments(
                base_url=masumi_cfg.base_url,
                token=masumi_cfg.token,
                network=masumi_cfg.registry_network,
            )
        except Exception as exc:
            logger.error("Masumi sync failed for network %s: %s", network, exc)
            return {
                "success": False,
                "error": str(exc),
                "new_records": 0,
                "network": network,
            }

        now_iso = datetime.now(timezone.utc).isoformat()
        return {
            "success": True,
            "new_records": new_records,
            "network": network,
            "synced_at": now_iso,
        }
