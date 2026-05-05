"""
SQLite cache layer for Masumi payment data.

Syncs payment records from the Masumi Payment API into a local database,
enabling fast dashboard queries without hitting the external API on each load.
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite
import httpx

logger = logging.getLogger(__name__)

# USDM on-chain unit hex identifiers per network
USDM_UNITS = {
    "Preprod": "16a55b2a349361ff88c03788f93e1e966e5d689605d044fef722ddde0014df10745553444d",
    "Mainnet": "c48cbb3d5e57ed56e276bc45f99ab39abe94e6cd7ac39fb402da47ad0014df105553444d",
}

# All currencies use 6 decimal places
CURRENCY_DECIMALS = 6


def _base_to_human(amount: int) -> float:
    """Convert base units (integer) to human-readable amount."""
    return amount / (10 ** CURRENCY_DECIMALS)


def classify_payment(payment_row: Dict[str, Any]) -> str:
    """
    Classify a payment record into a dashboard category.

    Categories:
    - delivered:          We submitted a result (ResultSubmitted or Withdrawn)
    - pending:            Funds are locked, awaiting processing
    - disputed:           Payment is in dispute
    - buyer_invalid:      Buyer sent invalid funds or datum
    - masumi_utxo:        Refunded because no suitable UTXOs available
    - masumi_state_error: Refunded due to unexpected state change
    - unresolved_timeout: Refunded but reason is unclear (timeout or agent error)
    - other:              Anything else not matching the above
    """
    state = payment_row.get("on_chain_state") or ""
    error_note = payment_row.get("error_note") or ""

    if state in ("ResultSubmitted", "Withdrawn"):
        return "delivered"

    if state == "FundsLocked":
        return "pending"

    if state == "Disputed":
        return "disputed"

    if state == "FundsOrDatumInvalid":
        return "buyer_invalid"

    if state == "RefundWithdrawn":
        if "No suitable UTXOs" in error_note or "UTxO Fully Depleted" in error_note:
            return "masumi_utxo"
        if "Unexpected state change" in error_note:
            return "masumi_state_error"
        # No distinguishing error note — could be buyer timeout or agent failure.
        # Cross-referencing with execution logs is left to the dashboard layer.
        return "unresolved_timeout"

    return "other"


def _extract_payment_fields(record: Dict[str, Any], network: str) -> Dict[str, Any]:
    """
    Extract and normalise fields from a raw Masumi API payment record.

    Returns a flat dict suitable for insertion into the payments table.
    """
    requested_funds = record.get("RequestedFunds") or []
    first_fund = requested_funds[0] if requested_funds else {}

    next_action = record.get("NextAction") or {}
    current_tx = record.get("CurrentTransaction") or {}
    smart_wallet = record.get("SmartContractWallet") or {}
    buyer_wallet = record.get("BuyerWallet") or {}

    return {
        "id": record.get("id", ""),
        "created_at": record.get("createdAt", ""),
        "network": network,
        "agent_identifier": record.get("agentIdentifier"),
        "on_chain_state": record.get("onChainState"),
        "requested_amount": int(first_fund.get("amount") or 0),
        "requested_unit": first_fund.get("unit") or "",
        "result_hash": record.get("resultHash"),
        "error_type": next_action.get("errorType"),
        "error_note": next_action.get("errorNote"),
        "previous_state": current_tx.get("previousOnChainState"),
        "seller_wallet": smart_wallet.get("walletAddress"),
        "buyer_wallet": buyer_wallet.get("walletVkey"),
        "raw_json": json.dumps(record),
    }


class MasumiCache:
    """
    Local SQLite cache for Masumi payment records.

    Provides fast, offline-capable queries for the payment dashboard
    by periodically syncing data from the Masumi Payment API.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    async def init_db(self) -> None:
        """Create tables and indexes if they do not already exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.executescript("""
                CREATE TABLE IF NOT EXISTS payments (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    network TEXT NOT NULL,
                    agent_identifier TEXT,
                    on_chain_state TEXT,
                    requested_amount INTEGER DEFAULT 0,
                    requested_unit TEXT DEFAULT '',
                    result_hash TEXT,
                    error_type TEXT,
                    error_note TEXT,
                    previous_state TEXT,
                    seller_wallet TEXT,
                    buyer_wallet TEXT,
                    raw_json TEXT
                );

                CREATE TABLE IF NOT EXISTS sync_state (
                    network TEXT PRIMARY KEY,
                    last_cursor_id TEXT DEFAULT '',
                    last_sync_at TEXT,
                    total_synced INTEGER DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_payments_network
                    ON payments(network);
                CREATE INDEX IF NOT EXISTS idx_payments_agent
                    ON payments(agent_identifier);
                CREATE INDEX IF NOT EXISTS idx_payments_created
                    ON payments(created_at);
                CREATE INDEX IF NOT EXISTS idx_payments_state
                    ON payments(on_chain_state);
            """)
            await conn.commit()

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    async def _get_known_ids(self, network: str) -> set:
        """Return the set of payment IDs already stored for *network*."""
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT id FROM payments WHERE network = ?", (network,)
            ) as cursor:
                rows = await cursor.fetchall()
        return {row[0] for row in rows}

    async def _upsert_payment(
        self, conn: aiosqlite.Connection, fields: Dict[str, Any]
    ) -> bool:
        """
        Insert a new payment record or update on_chain_state for an existing one.

        Returns True when a *new* record was inserted.
        """
        async with conn.execute(
            "SELECT on_chain_state FROM payments WHERE id = ?", (fields["id"],)
        ) as cursor:
            existing = await cursor.fetchone()

        if existing is None:
            await conn.execute(
                """
                INSERT INTO payments (
                    id, created_at, network, agent_identifier, on_chain_state,
                    requested_amount, requested_unit, result_hash,
                    error_type, error_note, previous_state,
                    seller_wallet, buyer_wallet, raw_json
                ) VALUES (
                    :id, :created_at, :network, :agent_identifier, :on_chain_state,
                    :requested_amount, :requested_unit, :result_hash,
                    :error_type, :error_note, :previous_state,
                    :seller_wallet, :buyer_wallet, :raw_json
                )
                """,
                fields,
            )
            return True  # newly inserted

        # State may have advanced on-chain — keep it current.
        if existing[0] != fields["on_chain_state"]:
            await conn.execute(
                """
                UPDATE payments
                SET on_chain_state = :on_chain_state,
                    result_hash     = :result_hash,
                    error_type      = :error_type,
                    error_note      = :error_note,
                    previous_state  = :previous_state,
                    raw_json        = :raw_json
                WHERE id = :id
                """,
                fields,
            )
        return False  # already existed

    async def sync_payments(self, base_url: str, token: str, network: str) -> int:
        """
        Sync payments from the Masumi API into the local cache.

        Strategy
        --------
        The API returns records *newest first*.  We start from the beginning
        (no cursorId) and page forward until every record on a full page is
        already in our database — at that point we have caught up and can stop
        early.  State changes on existing records are applied regardless.

        Pagination uses ``cursorId`` (the ``id`` of the last record on the
        previous page) — NOT an offset parameter, which the API does not
        support.

        Parameters
        ----------
        base_url:
            Base URL of the Masumi Payment API (e.g. ``https://pay.example.com``).
        token:
            API authentication token.
        network:
            Network name as stored in the Masumi config (e.g. ``"Preprod"``).

        Returns
        -------
        int
            Number of new payment records inserted during this sync run.
        """
        headers = {"accept": "application/json", "token": token}
        limit = 50
        cursor_id: Optional[str] = None
        new_count = 0
        page = 0

        async with httpx.AsyncClient(timeout=30.0) as client:
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute("PRAGMA journal_mode=WAL")

                while True:
                    url = (
                        f"{base_url.rstrip('/')}/payment"
                        f"?network={network}&limit={limit}"
                    )
                    if cursor_id:
                        url += f"&cursorId={cursor_id}"

                    try:
                        resp = await client.get(url, headers=headers)
                        resp.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        logger.error(
                            "Masumi sync %s page %d failed: HTTP %s — %s",
                            network,
                            page,
                            exc.response.status_code,
                            exc.response.text,
                        )
                        break
                    except httpx.RequestError as exc:
                        logger.error(
                            "Masumi sync %s page %d request error: %s",
                            network,
                            page,
                            exc,
                        )
                        break

                    data = resp.json()
                    # API wraps results in {"data": {"Payments": [...]}}
                    payments_data = (
                        data.get("data") or {}
                    )
                    records: List[Dict[str, Any]] = (
                        payments_data.get("Payments")
                        or payments_data.get("payments")
                        or (data if isinstance(data, list) else [])
                    )

                    if not records:
                        # No more pages.
                        break

                    all_known = True
                    for record in records:
                        fields = _extract_payment_fields(record, network)
                        if not fields["id"]:
                            continue
                        is_new = await self._upsert_payment(conn, fields)
                        if is_new:
                            new_count += 1
                            all_known = False

                    await conn.commit()

                    # If every record on this page was already in the DB we
                    # have reached the watermark — no need to fetch older pages.
                    if all_known and len(records) == limit:
                        break

                    # Prepare cursor for next page: use id of the *last* record.
                    cursor_id = records[-1].get("id")
                    if not cursor_id or len(records) < limit:
                        # Last page reached.
                        break

                    page += 1

                # Persist sync metadata.
                now_iso = datetime.now(timezone.utc).isoformat()
                await conn.execute(
                    """
                    INSERT INTO sync_state (network, last_sync_at, total_synced)
                    VALUES (:network, :now, :new)
                    ON CONFLICT(network) DO UPDATE SET
                        last_sync_at  = excluded.last_sync_at,
                        total_synced  = sync_state.total_synced + excluded.total_synced
                    """,
                    {"network": network, "now": now_iso, "new": new_count},
                )
                await conn.commit()

        logger.info("Masumi sync %s: %d new payments", network, new_count)
        return new_count

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    async def get_payment_count(self, network: str) -> int:
        """Return the total number of cached payment records for *network*."""
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT COUNT(*) FROM payments WHERE network = ?", (network,)
            ) as cursor:
                row = await cursor.fetchone()
        return row[0] if row else 0

    # ------------------------------------------------------------------
    # Revenue summary
    # ------------------------------------------------------------------

    async def get_revenue_summary(self, network: str) -> Dict[str, Any]:
        """
        Compute a revenue summary broken down by time period.

        Periods returned: ``today``, ``yesterday``, ``7d``, ``30d``, ``total``.

        Each period dict contains:
        - ``revenue``       – total amount delivered (in base units)
        - ``delivered``     – count of delivered payments
        - ``utxo_fail``     – count of masumi_utxo failures
        - ``timeout``       – count of unresolved_timeout refunds
        - ``buyer_invalid`` – count of buyer_invalid payments
        - ``disputed``      – count of disputed payments
        - ``pending``       – count of pending payments
        """
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT id, created_at, on_chain_state, requested_amount,
                       error_note, result_hash
                FROM payments
                WHERE network = ?
                ORDER BY created_at DESC
                """,
                (network,),
            ) as cursor:
                rows = [dict(r) for r in await cursor.fetchall()]

        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_start = today_start - timedelta(days=1)
        week_start = now - timedelta(days=7)
        month_start = now - timedelta(days=30)

        def _empty_period() -> Dict[str, int]:
            return {
                "revenue": 0,
                "delivered": 0,
                "utxo_fail": 0,
                "timeout": 0,
                "buyer_invalid": 0,
                "disputed": 0,
                "pending": 0,
            }

        periods = {
            "today": _empty_period(),
            "yesterday": _empty_period(),
            "7d": _empty_period(),
            "30d": _empty_period(),
            "total": _empty_period(),
        }

        def _record_in(period: Dict[str, int], row: Dict[str, Any]) -> None:
            cat = classify_payment(row)
            amount = row.get("requested_amount") or 0

            if cat == "delivered":
                period["delivered"] += 1
                period["revenue"] += amount
            elif cat == "masumi_utxo" or cat == "masumi_state_error":
                period["utxo_fail"] += 1
            elif cat == "unresolved_timeout":
                period["timeout"] += 1
            elif cat == "buyer_invalid":
                period["buyer_invalid"] += 1
            elif cat == "disputed":
                period["disputed"] += 1
            elif cat == "pending":
                period["pending"] += 1

        for row in rows:
            raw_ts = row.get("created_at") or ""
            try:
                ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                ts = None

            _record_in(periods["total"], row)

            if ts is None:
                continue

            if ts >= month_start:
                _record_in(periods["30d"], row)
            if ts >= week_start:
                _record_in(periods["7d"], row)
            if ts >= today_start:
                _record_in(periods["today"], row)
            elif ts >= yesterday_start:
                _record_in(periods["yesterday"], row)

        return periods

    # ------------------------------------------------------------------
    # Agent breakdown
    # ------------------------------------------------------------------

    async def get_agent_breakdown(
        self, network: str, agent_map: Dict[str, str]
    ) -> List[Dict[str, Any]]:
        """
        Compute per-agent statistics.

        Parameters
        ----------
        network:
            Network name to filter on.
        agent_map:
            Mapping of ``{agent_identifier_prefix: display_name}``.
            Each payment is matched to the longest prefix that fits its
            ``agent_identifier`` column.  Payments that match no prefix
            are grouped under ``"unknown"``.

        Returns
        -------
        list of dicts, one per agent, containing:
        - ``agent``         – display name from agent_map
        - ``identifier``    – matched prefix
        - ``delivered``     – count
        - ``revenue``       – total delivered amount (base units)
        - ``utxo_fail``     – count
        - ``timeout``       – count
        - ``buyer_invalid`` – count
        - ``disputed``      – count
        - ``pending``       – count
        - ``lost_revenue``  – potential revenue from failed/refunded payments
        """
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT id, agent_identifier, on_chain_state,
                       requested_amount, error_note, result_hash
                FROM payments
                WHERE network = ?
                """,
                (network,),
            ) as cursor:
                rows = [dict(r) for r in await cursor.fetchall()]

        def _empty_agent(identifier: str, name: str) -> Dict[str, Any]:
            return {
                "agent": name,
                "identifier": identifier,
                "delivered": 0,
                "revenue": 0,
                "utxo_fail": 0,
                "timeout": 0,
                "buyer_invalid": 0,
                "disputed": 0,
                "pending": 0,
                "lost_revenue": 0,
            }

        buckets: Dict[str, Dict[str, Any]] = {}

        def _match_agent(agent_id: Optional[str]) -> str:
            """Return the longest matching prefix key, or 'unknown'."""
            if not agent_id:
                return "unknown"
            best = ""
            for prefix in agent_map:
                if agent_id.startswith(prefix) and len(prefix) > len(best):
                    best = prefix
            return best or "unknown"

        # Ensure a bucket exists for every configured agent even with 0 records.
        for prefix, name in agent_map.items():
            buckets[prefix] = _empty_agent(prefix, name)
        buckets.setdefault("unknown", _empty_agent("unknown", "unknown"))

        for row in rows:
            key = _match_agent(row.get("agent_identifier"))
            if key not in buckets:
                name = agent_map.get(key, key)
                buckets[key] = _empty_agent(key, name)
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

        # Return sorted: highest revenue first, unknown last.
        result = sorted(
            (v for k, v in buckets.items() if k != "unknown"),
            key=lambda d: d["revenue"],
            reverse=True,
        )
        unknown = buckets.get("unknown")
        if unknown and (unknown["delivered"] or unknown["utxo_fail"] or unknown["timeout"]):
            result.append(unknown)
        return result

    # ------------------------------------------------------------------
    # Weekly trend
    # ------------------------------------------------------------------

    async def get_weekly_trend(
        self, network: str, weeks: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Return a per-week summary for the last *weeks* calendar weeks.

        Each entry contains:
        - ``week``      – ISO week label (``YYYY-Www``)
        - ``revenue``   – total delivered amount (base units)
        - ``delivered`` – count of delivered payments
        - ``utxo_fail`` – count of masumi_utxo / masumi_state_error refunds
        - ``timeout``   – count of unresolved_timeout refunds
        - ``agent_error`` – (reserved, always 0 — future cross-reference)
        - ``rate``      – delivery rate as a float in [0.0, 1.0]
        """
        cutoff = datetime.now(timezone.utc) - timedelta(weeks=weeks)

        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT id, created_at, on_chain_state,
                       requested_amount, error_note, result_hash
                FROM payments
                WHERE network = ?
                  AND created_at >= ?
                ORDER BY created_at ASC
                """,
                (network, cutoff.isoformat()),
            ) as cursor:
                rows = [dict(r) for r in await cursor.fetchall()]

        def _empty_week() -> Dict[str, Any]:
            return {
                "revenue": 0,
                "delivered": 0,
                "utxo_fail": 0,
                "timeout": 0,
                "agent_error": 0,
                "total": 0,
            }

        week_buckets: Dict[str, Dict[str, Any]] = {}

        for row in rows:
            raw_ts = row.get("created_at") or ""
            try:
                ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                continue

            week_start = ts - timedelta(days=ts.weekday())
            label = week_start.strftime("%m/%d")
            if label not in week_buckets:
                week_buckets[label] = _empty_week()
            bucket = week_buckets[label]

            amount = row.get("requested_amount") or 0
            cat = classify_payment(row)
            bucket["total"] += 1

            if cat == "delivered":
                bucket["delivered"] += 1
                bucket["revenue"] += amount
            elif cat in ("masumi_utxo", "masumi_state_error"):
                bucket["utxo_fail"] += 1
            elif cat == "unresolved_timeout":
                bucket["timeout"] += 1

        result: List[Dict[str, Any]] = []
        for label in sorted(week_buckets):
            b = week_buckets[label]
            total = b["total"]
            rate = b["delivered"] / total if total > 0 else 0.0
            result.append(
                {
                    "week": label,
                    "revenue": b["revenue"],
                    "delivered": b["delivered"],
                    "utxo_fail": b["utxo_fail"],
                    "timeout": b["timeout"],
                    "agent_error": b["agent_error"],
                    "rate": round(rate, 4),
                }
            )

        return result


def get_cache(exec_dir: str) -> MasumiCache:
    """
    Convenience factory: build a MasumiCache whose database lives next to
    ``expose.db`` in the ``data/`` directory.

    Parameters
    ----------
    exec_dir:
        Value of ``settings.EXEC_DIR`` (typically ``./data/execution``).
    """
    db_path = Path(exec_dir).parent / "masumi_cache.db"
    return MasumiCache(db_path)
