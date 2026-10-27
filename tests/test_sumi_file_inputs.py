"""
Tests for file inputs of start_job.

The input schema tells a Sumi buyer that a file field takes a URL
(outputFormat "url"). A buyer is anonymous, so the agent cannot read the
Kodosumi file store either. A bare file name used to pass start_job, the
buyer paid, and the agent then failed with 401 fetching it from /files.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from kodosumi.const import KODOSUMI_LAUNCH
from kodosumi.service.expose.models import ExposeMeta
from kodosumi.service.inputs.forms import InputFiles, InputText, Model, Submit
from kodosumi.service.sumi.jobs import _submit_job
from kodosumi.service.sumi.models import (
    JobStatusResponse, StartJobErrorResponse, StartJobRequest)
from kodosumi.service.sumi.schema import convert_model_to_schema

# The Ad-Campaign form, through the converter that builds the real schema.
SCHEMA = convert_model_to_schema(Model(
    InputText(name="website", label="Website"),
    InputFiles(name="logo", label="Logo"),
    Submit("Run"),
).get_model())


def _agent_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {KODOSUMI_LAUNCH: "job1"}
    resp.content = b"{}"
    resp.json.return_value = {}
    return resp


async def _start(input_data):
    """Run start_job for a free flow; return (result, forward, runner)."""
    request = MagicMock(user="someone", headers={}, cookies={})
    runner = MagicMock()
    runner.prepare.remote = AsyncMock(return_value=None)
    with patch("kodosumi.service.sumi.jobs._fetch_input_schema",
               new_callable=AsyncMock, return_value=SCHEMA), \
            patch("kodosumi.service.sumi.jobs.proxy_forward",
                  new_callable=AsyncMock,
                  return_value=_agent_response()) as forward, \
            patch("kodosumi.service.sumi.jobs.ray.get_actor",
                  return_value=runner):
        result = await _submit_job(
            expose_name="ad-campaign", meta_name="",
            meta=ExposeMeta(url="/ad-campaign/", data="display: Ad\n"),
            network="Mainnet",
            data=StartJobRequest(identifier_from_purchaser="purchaser",
                                 input_data=input_data),
            app_server="https://app", ray_serve_address="https://ray",
            request=request, state=None)
    return result, forward, runner


class TestFileInputsThatCannotBeFetched:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("logo", [
        "apple_logo.png",                                  # bare file name
        ["https://cdn.example.com/a.png", "b.png"],        # one bad entry
        "http://",                                         # scheme, no host
        "https://@",                                       # netloc, no host
        "http://[::1",                                     # urlparse raises
        " https://cdn.example.com/logo.png",               # agent sees a name
        "ftp://files.example.com/logo.png",                # not http(s)
        {"items": {"u1": {"filename": "logo.png"}}},       # panel upload shape
    ])
    async def test_is_refused_before_a_job_or_payment_exists(self, logo):
        result, forward, runner = await _start(
            {"website": "https://www.apple.com", "logo": logo})
        assert isinstance(result, StartJobErrorResponse)
        assert "logo" in result.error
        # Nothing reached the agent, so no runner and no payment request.
        forward.assert_not_awaited()
        runner.prepare.remote.assert_not_awaited()


class TestFileInputsThatStillRun:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("input_data", [
        {"logo": "https://cdn.example.com/logo.png"},
        {"logo": ["https://a.example.com/1.png", "http://b.example.com/2.png"]},
        {"logo": ""},                                      # optional, left empty
        {"logo": None},
        {"logo": False},                                   # agent ignores falsy
        {},                                                # optional, omitted
        {"website": "apple_logo.png"},                     # text, not a file
    ])
    async def test_reaches_the_agent(self, input_data):
        result, forward, _ = await _start(input_data)
        assert isinstance(result, JobStatusResponse)
        assert result.job_id == "job1"
        forward.assert_awaited_once()
