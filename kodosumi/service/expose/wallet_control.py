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
            # cause. The report carries what the token could see. It is
            # compared against the node's own network name, never against
            # the name of the KODO_MASUMI entry the operator chose.
            return {
                "wallets": [],
                "error": report.describe_empty(masumi.registry_network),
            }

        # A list can be non-empty and still be missing the one wallet the
        # operator is looking for, when a payment source could not be read.
        # Saying so is what stops the migrate dialog from reporting a
        # transient failure as a missing Web3CardanoV2 wallet.
        warning = report.describe_partial()
        result = {"wallets": wallets, "network": network}
        if warning:
            result["warning"] = warning
        return result
