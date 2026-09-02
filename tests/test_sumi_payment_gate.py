"""
Tests for the payment gate of start_job.

A registered agent is a paid agent. When the payment cannot be created,
start_job must say so. Answering "running" without a blockchainIdentifier
tells the buyer there is nothing to pay while the job goes on to fail
inside the runner.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

from kodosumi.const import KODOSUMI_LAUNCH
from kodosumi.service.expose.models import ExposeMeta
from kodosumi.service.sumi.jobs import _submit_job
from kodosumi.service.sumi.models import (
    InputSchemaResponse, JobStatusResponse, StartJobErrorResponse,
    StartJobRequest)


def _meta(**overrides) -> ExposeMeta:
    data = {
        "display": "My Agent",
        "agentIdentifier": "v1-agent",
        "registrationId": "v1-reg",
    }
    data.update(overrides)
    lines = [f"{key}: {value}" for key, value in data.items()
             if value is not None]
    return ExposeMeta(url="/flow", data="\n".join(lines) + "\n")


def _agent_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {KODOSUMI_LAUNCH: "job1"}
    resp.content = b"{}"
    resp.json.return_value = {}
    return resp


def _request():
    request = MagicMock()
    request.user = "someone"
    request.headers = {}
    request.cookies = {}
    return request


def _payment(blockchain_identifier="chain-id"):
    return {
        "blockchain_identifier": blockchain_identifier,
        "pay_data": {"payByTime": "1", "submitResultTime": "2",
                     "unlockTime": "3", "externalDisputeUnlockTime": "4",
                     "SmartContractWallet": {"walletVkey": "seller"}},
        "pay_conf": {"paymentSourceType": "Web3CardanoV2",
                     "supportedPaymentSourceIndex": 0},
    }


async def _run(meta, prepare_result=None, prepare_error=None):
    runner = MagicMock()
    if prepare_error is not None:
        runner.prepare.remote = AsyncMock(side_effect=prepare_error)
    else:
        runner.prepare.remote = AsyncMock(return_value=prepare_result)

    with patch("builtins.open", mock_open()), \
            patch("kodosumi.service.sumi.jobs._fetch_input_schema",
                  new_callable=AsyncMock,
                  return_value=InputSchemaResponse()), \
            patch("kodosumi.service.sumi.jobs.proxy_forward",
                  new_callable=AsyncMock, return_value=_agent_response()), \
            patch("kodosumi.service.sumi.jobs.ray.get_actor",
                  return_value=runner):
        return await _submit_job(
            expose_name="expose",
            meta_name="",
            meta=meta,
            network="Preprod",
            data=StartJobRequest(
                identifier_from_purchaser="purchaser",
                input_data={"prompt": "hi"}),
            app_server="https://app",
            ray_serve_address="https://ray",
            request=_request(),
            state=None,
        )


class TestPaidFlowWithoutAPayment:

    @pytest.mark.asyncio
    async def test_a_refused_payment_is_reported_as_an_error(self):
        # A V2 flow whose supportedPaymentSourceIndex is missing gets its
        # payment refused by the node. The failure used to be swallowed.
        result = await _run(
            _meta(paymentSourceType="Web3CardanoV2"),
            prepare_error=RuntimeError(
                "Payment init failed at https://node/api/v1: "
                "supportedPaymentSourceIndex required\n  File \"main.py\""))
        assert isinstance(result, StartJobErrorResponse)
        assert "Payment could not be initialized" in result.error
        # The buyer is an external MIP-003 consumer. A Ray remote call
        # raises with the whole remote traceback attached.
        assert "https://node" not in result.error
        assert "File \"main.py\"" not in result.error

    @pytest.mark.asyncio
    async def test_a_missing_runner_is_reported_as_an_error(self):
        result = await _run(_meta(), prepare_error=ValueError("no actor"))
        assert isinstance(result, StartJobErrorResponse)

    @pytest.mark.asyncio
    async def test_no_payment_for_a_registered_agent_is_an_error(self):
        # prepare() answers None when the runner found no payment config.
        # For a registered agent that means the job would run unpriced.
        result = await _run(_meta(), prepare_result=None)
        assert isinstance(result, StartJobErrorResponse)
        assert "Payment could not be initialized" in result.error


class TestFlowsThatStillRun:

    @pytest.mark.asyncio
    async def test_an_unregistered_flow_runs_free(self):
        meta = ExposeMeta(url="/flow", data="display: My Agent\n")
        result = await _run(meta, prepare_result=None)
        assert isinstance(result, JobStatusResponse)
        assert result.status == "running"
        assert result.blockchainIdentifier is None

    @pytest.mark.asyncio
    async def test_a_paid_flow_reports_the_rail_it_paid_on(self):
        result = await _run(
            _meta(paymentSourceType="Web3CardanoV2",
                  supportedPaymentSourceIndex=0),
            prepare_result=_payment())
        assert isinstance(result, JobStatusResponse)
        assert result.status == "awaiting_payment"
        assert result.blockchainIdentifier == "chain-id"
        assert result.paymentSourceType == "Web3CardanoV2"
        # Index 0 is a real selection, not an absent one.
        assert result.supportedPaymentSourceIndex == 0
