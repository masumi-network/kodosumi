from datetime import datetime, timezone
from unittest.mock import patch

import aiosqlite
import pytest

from kodosumi.service.masumi.cache import MasumiCache


@pytest.mark.asyncio
async def test_weekly_trend_orders_weeks_across_new_year(tmp_path):
    cache = MasumiCache(tmp_path / "masumi_cache.db")
    await cache.init_db()
    async with aiosqlite.connect(cache.db_path) as conn:
        await conn.executemany(
            """
            INSERT INTO payments (
                id, created_at, network, on_chain_state, requested_amount
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("december", "2025-12-29T10:00:00Z", "Preprod", "Withdrawn", 100),
                ("january", "2026-01-05T11:00:00Z", "Preprod", "ResultSubmitted", 200),
                ("same-week", "2026-01-01T12:00:00Z", "Preprod", "Withdrawn", 300),
            ],
        )
        await conn.commit()

    with patch("kodosumi.service.masumi.cache.datetime", wraps=datetime) as clock:
        clock.now.return_value = datetime(2026, 1, 10, tzinfo=timezone.utc)
        trend = await cache.get_weekly_trend("Preprod")

    assert [week["week"] for week in trend] == ["12/29", "01/05"]
    assert [week["revenue"] for week in trend] == [400, 200]
    assert [week["delivered"] for week in trend] == [2, 1]
    assert [week["rate"] for week in trend] == [1.0, 1.0]
