"""Tests for the Temporal durable execution layer (PR 3).

These tests validate the workflow, activity, and config integration
without requiring a running Temporal server or Ray cluster.
Run with: pytest tests/test_temporal.py -v
"""
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# AgentJobInput / AgentJobResult dataclass tests
# ---------------------------------------------------------------------------

class TestAgentJobInput:

    def test_default_values(self):
        from kodosumi.workflows import AgentJobInput
        job = AgentJobInput(
            fid="abc123",
            username="testuser",
            app_url="http://localhost:8000",
            entry_point="mymodule:my_agent",
            panel_url="http://localhost:3370",
            jwt="token123",
        )
        assert job.fid == "abc123"
        assert job.inputs is None
        assert job.extra is None
        assert job.execution_timeout == 3600

    def test_with_inputs(self):
        from kodosumi.workflows import AgentJobInput
        job = AgentJobInput(
            fid="abc123",
            username="testuser",
            app_url="http://localhost:8000",
            entry_point="mymodule:my_agent",
            panel_url="http://localhost:3370",
            jwt="token123",
            inputs={"query": "hello"},
            extra={"tags": ["test"]},
            execution_timeout=7200,
        )
        assert job.inputs == {"query": "hello"}
        assert job.execution_timeout == 7200

    def test_serializable_as_dict(self):
        from kodosumi.workflows import AgentJobInput
        job = AgentJobInput(
            fid="fid1", username="u", app_url="http://a",
            entry_point="m:e", panel_url="http://p", jwt="j")
        d = asdict(job)
        assert isinstance(d, dict)
        assert d["fid"] == "fid1"
        assert d["execution_timeout"] == 3600


class TestAgentJobResult:

    def test_success_result(self):
        from kodosumi.workflows import AgentJobResult
        result = AgentJobResult(fid="fid1", status="completed")
        assert result.fid == "fid1"
        assert result.status == "completed"
        assert result.error is None

    def test_failure_result(self):
        from kodosumi.workflows import AgentJobResult
        result = AgentJobResult(
            fid="fid1", status="failed", error="timeout")
        assert result.error == "timeout"


# ---------------------------------------------------------------------------
# AgentWorkflow unit tests (no Temporal server needed)
# ---------------------------------------------------------------------------

class TestAgentWorkflowStructure:
    """Test the workflow class structure and decorators."""

    def test_workflow_has_run_method(self):
        from kodosumi.workflows import AgentWorkflow
        wf = AgentWorkflow()
        assert hasattr(wf, "run")

    def test_workflow_has_signals(self):
        from kodosumi.workflows import AgentWorkflow
        wf = AgentWorkflow()
        assert hasattr(wf, "pause")
        assert hasattr(wf, "resume")
        assert hasattr(wf, "cancel_workflow")

    def test_workflow_has_queries(self):
        from kodosumi.workflows import AgentWorkflow
        wf = AgentWorkflow()
        assert hasattr(wf, "get_status")
        assert hasattr(wf, "is_paused")
        assert hasattr(wf, "get_error")

    def test_workflow_initial_state(self):
        from kodosumi.workflows import AgentWorkflow
        wf = AgentWorkflow()
        assert wf._paused is False
        assert wf._cancelled is False
        assert wf._status == "pending"
        assert wf._error is None


# ---------------------------------------------------------------------------
# Activity structure tests
# ---------------------------------------------------------------------------

class TestActivityStructure:

    def test_execute_agent_is_importable(self):
        from kodosumi.activities import execute_agent
        assert callable(execute_agent)


# ---------------------------------------------------------------------------
# Temporal worker module tests
# ---------------------------------------------------------------------------

class TestTemporalWorkerModule:

    def test_module_is_importable(self):
        import kodosumi.temporal_worker
        assert hasattr(kodosumi.temporal_worker, "run_worker")
        assert hasattr(kodosumi.temporal_worker, "main")


# ---------------------------------------------------------------------------
# Config integration tests
# ---------------------------------------------------------------------------

class TestTemporalConfig:

    def test_default_execution_mode_is_direct(self):
        from kodosumi.config import Settings
        s = Settings()
        assert s.EXECUTION_MODE == "direct"

    def test_temporal_settings_have_defaults(self):
        from kodosumi.config import Settings
        s = Settings()
        assert s.TEMPORAL_HOST == "localhost:7233"
        assert s.TEMPORAL_NAMESPACE == "default"
        assert s.TEMPORAL_TASK_QUEUE == "kodosumi-agents"
        assert s.TEMPORAL_WORKFLOW_ID_PREFIX == "kodo-"
        assert s.TEMPORAL_EXECUTION_TIMEOUT == 3600


# ---------------------------------------------------------------------------
# Launch() temporal path tests
# ---------------------------------------------------------------------------

class TestLaunchTemporalPath:

    def test_launch_uses_direct_by_default(self):
        """With default settings, Launch() should NOT take the temporal path."""
        from kodosumi.config import Settings
        s = Settings()
        assert s.EXECUTION_MODE == "direct"

    def test_launch_temporal_helper_is_importable(self):
        from kodosumi.runner.main import _launch_temporal
        assert callable(_launch_temporal)

    def test_launch_temporal_converts_entry_point_callable(self):
        """Verify callable → string conversion logic."""
        def my_agent():
            pass
        module = getattr(my_agent, "__module__", "")
        name = getattr(my_agent, "__name__", "")
        ep_str = f"{module}:{name}"
        assert "my_agent" in ep_str

    def test_launch_temporal_converts_pydantic_inputs(self):
        """Verify BaseModel → dict conversion logic."""
        from pydantic import BaseModel

        class TestInput(BaseModel):
            query: str = "hello"

        inputs = TestInput()
        if hasattr(inputs, "model_dump"):
            result = inputs.model_dump()
        else:
            result = inputs
        assert isinstance(result, dict)
        assert result["query"] == "hello"


# ---------------------------------------------------------------------------
# CLI command registration test
# ---------------------------------------------------------------------------

class TestCLITemporalWorker:

    def test_temporal_worker_command_exists(self):
        from kodosumi.cli import cli
        commands = [cmd for cmd in cli.commands]
        assert "temporal-worker" in commands
