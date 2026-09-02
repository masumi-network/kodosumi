"""
Tests for the flow meta and agent field helpers of the registry endpoints.

Both were extracted out of RegistryControl so the registration and the
migration endpoint read and write a flow the same way.
"""

import pytest
import yaml
from unittest.mock import AsyncMock, patch

from kodosumi.service.expose.flow_meta import get_flow_meta, update_flow_meta
from kodosumi.service.expose.registration import (
    build_agent_fields, rail_fields, sumi_api_base_url)


def _row(flows: dict) -> dict:
    """Build an expose row whose meta lists one entry per flow url."""
    meta = [{"url": url, "data": yaml.dump(data)} for url, data in flows.items()]
    return {"meta": yaml.dump(meta)}


class TestBuildAgentFields:

    def test_reads_display_and_tags(self):
        fields = build_agent_fields(
            {"display": "My Agent", "description": "d", "tags": ["a", "b"]},
            "expose-name")
        assert fields["name"] == "My Agent"
        assert fields["description"] == "d"
        assert fields["tags"] == ["a", "b"]

    def test_falls_back_to_the_expose_name(self):
        assert build_agent_fields({}, "expose-name")["name"] == "expose-name"

    def test_author_keys_are_renamed_for_the_registry(self):
        fields = build_agent_fields(
            {"author": {"name": "n", "contact_email": "e@x", "organization": "o"}},
            "x")
        assert fields["author"] == {
            "name": "n", "contactEmail": "e@x", "organization": "o"}

    def test_missing_optional_blocks_stay_none(self):
        fields = build_agent_fields({}, "x")
        assert fields["author"] is None
        assert fields["capability"] is None
        assert fields["legal"] is None

    def test_capability_version_is_stringified(self):
        fields = build_agent_fields({"capability": {"name": "c", "version": 2}}, "x")
        assert fields["capability"] == {"name": "c", "version": "2"}

    def test_non_list_tags_are_dropped(self):
        assert build_agent_fields({"tags": "not-a-list"}, "x")["tags"] == []


class TestSumiApiBaseUrl:

    def test_joins_without_a_double_slash(self):
        assert sumi_api_base_url("http://host:3370/", "/app/flow") == \
            "http://host:3370/sumi/app/flow"

    def test_keeps_a_plain_address(self):
        assert sumi_api_base_url("http://host:3370", "/app/flow") == \
            "http://host:3370/sumi/app/flow"


class TestRailFields:
    """A flow with no marker is a V1 registration, not an unknown one."""

    def test_absent_marker_reads_as_v1(self):
        fields = rail_fields({"agentIdentifier": "id1"})
        assert fields["paymentSourceType"] == "Web3CardanoV1"
        assert fields["supportedPaymentSourceIndex"] is None

    def test_v2_marker_and_index_are_passed_through(self):
        fields = rail_fields({
            "paymentSourceType": "Web3CardanoV2",
            "supportedPaymentSourceIndex": 0,
        })
        assert fields["paymentSourceType"] == "Web3CardanoV2"
        # Index 0 is a real selection and must not read as absent.
        assert fields["supportedPaymentSourceIndex"] == 0

    def test_previous_registration_is_reported(self):
        previous = {"agentIdentifier": "old", "paymentSourceType": "Web3CardanoV1"}
        assert rail_fields({"previousRegistration": previous})[
            "previousRegistration"] == previous


class TestGetFlowMeta:

    def test_returns_the_data_of_the_matching_flow(self):
        row = _row({"/a": {"display": "A"}, "/b": {"display": "B"}})
        assert get_flow_meta(row, "/b") == {"display": "B"}

    def test_unknown_flow_returns_none(self):
        assert get_flow_meta(_row({"/a": {}}), "/missing") is None

    def test_expose_without_meta_returns_none(self):
        assert get_flow_meta({}, "/a") is None


class TestUpdateFlowMeta:

    @pytest.mark.asyncio
    async def test_writes_the_updated_flow_back(self):
        row = _row({"/a": {"display": "A"}})
        with patch("kodosumi.service.expose.flow_meta.db.update_expose_meta",
                   new_callable=AsyncMock) as mock_write:
            updated = await update_flow_meta(
                row, "expose", "/a", {"registrationId": "reg1"})
        assert yaml.safe_load(updated) == {"display": "A", "registrationId": "reg1"}
        saved = yaml.safe_load(mock_write.call_args.args[1])
        assert yaml.safe_load(saved[0]["data"])["registrationId"] == "reg1"

    @pytest.mark.asyncio
    async def test_none_removes_a_key(self):
        row = _row({"/a": {"display": "A", "registrationId": "reg1"}})
        with patch("kodosumi.service.expose.flow_meta.db.update_expose_meta",
                   new_callable=AsyncMock):
            updated = await update_flow_meta(
                row, "expose", "/a", {"registrationId": None})
        assert yaml.safe_load(updated) == {"display": "A"}

    @pytest.mark.asyncio
    async def test_nested_values_survive_the_round_trip(self):
        row = _row({"/a": {"display": "A"}})
        previous = {"agentIdentifier": "id1", "paymentSourceType": "Web3CardanoV1"}
        with patch("kodosumi.service.expose.flow_meta.db.update_expose_meta",
                   new_callable=AsyncMock):
            updated = await update_flow_meta(
                row, "expose", "/a", {"previousRegistration": previous})
        assert yaml.safe_load(updated)["previousRegistration"] == previous

    @pytest.mark.asyncio
    async def test_base_data_replaces_the_stored_yaml(self):
        row = _row({"/a": {"display": "A"}})
        live = yaml.dump({"display": "edited in the browser"})
        with patch("kodosumi.service.expose.flow_meta.db.update_expose_meta",
                   new_callable=AsyncMock):
            updated = await update_flow_meta(
                row, "expose", "/a", {"registrationId": "reg1"}, base_data=live)
        parsed = yaml.safe_load(updated)
        assert parsed["display"] == "edited in the browser"
        assert parsed["registrationId"] == "reg1"

    @pytest.mark.asyncio
    async def test_unknown_flow_writes_nothing(self):
        row = _row({"/a": {}})
        with patch("kodosumi.service.expose.flow_meta.db.update_expose_meta",
                   new_callable=AsyncMock) as mock_write:
            assert await update_flow_meta(row, "expose", "/missing", {"x": 1}) is None
        mock_write.assert_not_called()


class TestFlowMetaAcceptsHandEditedYaml:
    """The meta column is edited by hand, so it is not trusted input."""

    def test_a_scalar_column_returns_none(self):
        assert get_flow_meta({"meta": "just a string"}, "/a") is None

    def test_entries_that_are_not_mappings_are_skipped(self):
        row = {"meta": yaml.dump(["oops", {"url": "/a", "data": "display: A"}])}
        assert get_flow_meta(row, "/a") == {"display": "A"}

    def test_broken_yaml_returns_none(self):
        assert get_flow_meta({"meta": "[unclosed"}, "/a") is None

    def test_an_empty_flow_url_matches_nothing(self):
        # A request that omits flow_url must not act on the first flow: the
        # registry endpoints burn and mint on chain.
        row = _row({"/a": {"display": "A"}})
        assert get_flow_meta(row, "") is None
        assert get_flow_meta(row, None) is None

    @pytest.mark.asyncio
    async def test_the_writer_keeps_entries_it_cannot_parse(self):
        row = {"meta": yaml.dump(["oops", {"url": "/a", "data": "display: A"}])}
        with patch("kodosumi.service.expose.flow_meta.db.get_expose",
                   new_callable=AsyncMock, return_value=None), \
             patch("kodosumi.service.expose.flow_meta.db.update_expose_meta",
                   new_callable=AsyncMock) as mock_write:
            await update_flow_meta(row, "expose", "/a", {"registrationId": "r"})
        saved = yaml.safe_load(mock_write.call_args.args[1])
        assert saved[0] == "oops"


class TestUpdateFlowMetaConcurrency:

    @pytest.mark.asyncio
    async def test_a_stale_row_does_not_drop_a_sibling_flow(self):
        # Both flows of an expose share one meta column. The caller read the
        # row before /b was registered, so the write has to re-read it.
        stale = _row({"/a": {"display": "A"}, "/b": {"display": "B"}})
        fresh = _row({"/a": {"display": "A"},
                      "/b": {"display": "B", "registrationId": "reg-b"}})
        with patch("kodosumi.service.expose.flow_meta.db.get_expose",
                   new_callable=AsyncMock, return_value=fresh), \
             patch("kodosumi.service.expose.flow_meta.db.update_expose_meta",
                   new_callable=AsyncMock) as mock_write:
            await update_flow_meta(
                stale, "expose", "/a", {"registrationId": "reg-a"})
        saved = yaml.safe_load(mock_write.call_args.args[1])
        by_url = {e["url"]: yaml.safe_load(e["data"]) for e in saved}
        assert by_url["/a"]["registrationId"] == "reg-a"
        assert by_url["/b"]["registrationId"] == "reg-b"

    @pytest.mark.asyncio
    async def test_a_failed_re_read_falls_back_to_the_callers_row(self):
        row = _row({"/a": {"display": "A"}})
        with patch("kodosumi.service.expose.flow_meta.db.get_expose",
                   new_callable=AsyncMock, side_effect=RuntimeError("no db")), \
             patch("kodosumi.service.expose.flow_meta.db.update_expose_meta",
                   new_callable=AsyncMock):
            updated = await update_flow_meta(
                row, "expose", "/a", {"registrationId": "reg1"})
        assert yaml.safe_load(updated)["registrationId"] == "reg1"
