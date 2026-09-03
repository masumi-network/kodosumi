"""Masumi wallet discovery for one expose network."""

import litestar
from litestar import get
from litestar.datastructures import State
from litestar.exceptions import NotFoundException

from kodosumi.service.expose import db
from kodosumi.service.expose.wallet_inventory import WalletReport
from kodosumi.service.jwt import operator_guard


class WalletsControl(litestar.Controller):
    path = "/expose/{name:str}/wallets"
    tags = ["Registry"]
    guards = [operator_guard]

    @get(
        "",
        summary="List wallets for expose network",
        description="List available selling wallets from Masumi Payment API for the expose's configured network.",
        operation_id="registry_wallets",
    )
    async def list_wallets(self, name: str, state: State) -> dict:
        await db.init_database()
        row = await db.get_expose(name)
        if not row:
            raise NotFoundException(detail=f"Expose '{name}' not found")

        network = row.get("network")
        if not network:
            return {
                "wallets": [],
                "error": "No network configured. Set network first.",
            }

        try:
            masumi = state["settings"].get_masumi(network)
        except ValueError as e:
            return {"wallets": [], "error": str(e)}

        from kodosumi.service.expose.registry import list_wallets
        report = WalletReport()
        try:
            wallets = await list_wallets(masumi, report=report)
        except Exception as e:
            return {
                "wallets": [],
                "error": f"Cannot reach Masumi API: {e}. "
                         "Check KODO_MASUMI token.",
            }

        if not wallets:
            # The node answers a token that may not read this network with a
            # normal empty 200, so the empty list alone cannot name its own
            # cause. The report carries what the token could see.
            return {
                "wallets": [],
                "error": report.describe_empty(network),
            }

        return {"wallets": wallets, "network": network}
