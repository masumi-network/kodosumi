"""
Tests for the register and deregister endpoints of the admin panel.

Both act on chain, so every refusal is checked on its own: a mint or a
burn that should not have happened cannot be taken back.
"""

import pytest
import yaml
from unittest.mock import AsyncMock, MagicMock, patch

from litestar.exceptions import ClientException

from kodosumi.config import MasumiConfig
from kodosumi.service.expose.registry_control import RegistryControl

REGISTER = RegistryControl.register.fn
DEREGISTER = RegistryControl.deregister.fn


def _state(network="Preprod") -> dict:
    settings = MagicMock()
    settings.sumi_address = "https://host"
    settings.get_masumi.return_value = MasumiConfig(
        network=network,
        base_url="https://test.masumi.network/api/v1",
        token="test-token",
        poll_interval=1.0,
    )
    return {"settings": settings}


def _row() -> dict:
    return {"name": "expose", "network": "Preprod", "meta": "[]"}


def _wallet() -> dict:
    return {
        "walletVkey": "vkey1",
        "walletAddress": "addr_test1w",
        "sourceId": "src1",
        "network": "Preprod",
        "paymentSourceType": "Web3CardanoV1",
        "smartContractAddress": "addr_test1contract",
    }


def _patches(meta=None, wallets=None):
    return {
        "init": patch(
            "kodosumi.service.expose.registry_control.db.init_database",
            new_callable=AsyncMock),
        "row": patch(
            "kodosumi.service.expose.registry_control.db.get_expose",
            new_callable=AsyncMock, return_value=_row()),
        "meta": patch(
            "kodosumi.service.expose.registry_control.get_flow_meta",
            return_value=meta if meta is not None else {}),
        "wallets": patch(
            "kodosumi.service.expose.registry.list_wallets",
            new_callable=AsyncMock,
            return_value=[_wallet()] if wallets is None else wallets),
        "register": patch(
            "kodosumi.service.expose.registry.register_agent",
            new_callable=AsyncMock, return_value={"id": "reg1"}),
        "deregister": patch(
            "kodosumi.service.expose.registry.deregister_agent",
            new_callable=AsyncMock,
            return_value={"state": "DeregistrationRequested"}),
    }


class TestDeregisterGuards:

    @pytest.mark.asyncio
    async def test_refuses_a_missing_flow_url(self):
        # Without the guard an omitted flow_url used to select the first
        # flow of the expose and burn that agent instead.
        mocks = _patches(meta={"agentIdentifier": "agent-1"})
        with mocks["init"], mocks["row"], mocks["meta"], \
                mocks["deregister"] as deregister:
            with pytest.raises(ClientException) as err:
                await DEREGISTER(None, name="expose", data={},
                                 state=_state())
        assert err.value.status_code == 422
        deregister.assert_not_called()


class TestRegisterPricingErrors:
    """agentPricing is hand edited, so a bad shape is a 422, not a 500."""

    @pytest.mark.asyncio
    async def test_a_scalar_agent_pricing_is_refused(self):
        mocks = _patches(meta={"display": "A", "agentPricing": "Free"})
        with mocks["init"], mocks["row"], mocks["meta"], \
                mocks["wallets"], mocks["register"] as register:
            with pytest.raises(ClientException) as err:
                await REGISTER(
                    None, name="expose",
                    data={"flow_url": "/flow", "wallet_vkey": "vkey1"},
                    state=_state())
        assert err.value.status_code == 422
        assert "agentPricing" in err.value.detail
        register.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_non_numeric_dialog_amount_is_refused(self):
        mocks = _patches(meta={"display": "A"})
        with mocks["init"], mocks["row"], mocks["meta"], \
                mocks["wallets"], mocks["register"] as register:
            with pytest.raises(ClientException) as err:
                await REGISTER(
                    None, name="expose",
                    data={"flow_url": "/flow", "wallet_vkey": "vkey1",
                          "pricing_type": "Fixed", "amount": "lots",
                          "currency": "ADA"},
                    state=_state())
        assert err.value.status_code == 422
        register.assert_not_called()
