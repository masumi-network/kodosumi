"""Job submission must not depend on an optional debug file directory."""

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kodosumi.const import KODOSUMI_LAUNCH
from kodosumi.service.sumi import jobs
from kodosumi.service.sumi.models import (
    InputSchemaResponse, JobStatusResponse, StartJobErrorResponse,
    StartJobRequest,
)
from kodosumi.service.expose.models import ExposeMeta


@pytest.mark.parametrize("failed_open_at", [1, 2])
@pytest.mark.parametrize("status_code", [200, 503])
async def test_submission_without_debug_directory(failed_open_at, status_code):
    opens = 0

    def unavailable_debug_file(*args, **kwargs):
        nonlocal opens
        opens += 1
        if opens >= failed_open_at:
            raise FileNotFoundError("Debug directory does not exist")
        return io.StringIO()

    response = MagicMock()
    response.status_code = status_code
    response.headers = {KODOSUMI_LAUNCH: "job-123"}
    response.content = b"service unavailable" if status_code != 200 else b"{}"
    response.json.return_value = {}
    runner = MagicMock()
    runner.prepare.remote = AsyncMock(return_value={
        "blockchain_identifier": "chain-123",
        "pay_data": {},
        "pay_conf": {"paymentSourceType": "Web3CardanoV2",
                     "supportedPaymentSourceIndex": 0},
    })
    request = MagicMock(user="buyer", headers={}, cookies={})

    with patch.object(jobs, "open", side_effect=unavailable_debug_file,
                      create=True), \
            patch.object(jobs, "_fetch_input_schema", AsyncMock(
                return_value=InputSchemaResponse())), \
            patch.object(jobs, "proxy_forward", AsyncMock(
                return_value=response)) as forward, \
            patch.object(jobs.ray, "get_actor", return_value=runner):
        result = await jobs._submit_job(
            expose_name="meme-copy", meta_name="",
            meta=ExposeMeta(url="/meme-copy", data="agentIdentifier: agent-123"),
            network="Mainnet",
            data=StartJobRequest(identifier_from_purchaser="buyer-123",
                                 input_data={"text": "test meme"}),
            app_server="https://app.example",
            ray_serve_address="https://ray.example",
            request=request,
        )

    forward.assert_awaited_once()
    if status_code == 200:
        assert isinstance(result, JobStatusResponse)
        assert result.job_id == "job-123"
        assert result.status == "awaiting_payment"
        assert result.blockchainIdentifier == "chain-123"
        runner.prepare.remote.assert_awaited_once()
    else:
        assert isinstance(result, StartJobErrorResponse)
        assert result.error == "Service returned HTTP 503: service unavailable"
        runner.prepare.remote.assert_not_awaited()
