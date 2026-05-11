"""Temporal workflow definitions for durable agent execution (PR 3).

The AgentWorkflow wraps the existing Runner pattern with Temporal's
durability guarantees: automatic retries, crash recovery, and
pause/resume/cancel signals.

Default EXECUTION_MODE=direct — this module is only used when
KODO_EXECUTION_MODE=temporal is set.
"""
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Dict, Optional

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    pass


@dataclass
class AgentJobInput:
    """Input for the AgentWorkflow — serialized across Temporal boundary."""
    fid: str
    username: str
    app_url: str
    entry_point: str  # must be string (not callable) for serialization
    panel_url: str
    jwt: str
    inputs: Optional[Dict[str, Any]] = None
    extra: Optional[Dict[str, Any]] = None
    execution_timeout: int = 3600  # seconds


@dataclass
class AgentJobResult:
    """Result from the AgentWorkflow."""
    fid: str
    status: str
    error: Optional[str] = None


@workflow.defn
class AgentWorkflow:
    """Durable wrapper around kodosumi's Runner execution.

    The workflow executes the `execute_agent` activity, which in turn
    creates a Ray Runner actor. Temporal provides:
    - Automatic retries on worker crash (up to 3 attempts)
    - Heartbeat monitoring (120s timeout)
    - Pause/resume/cancel signals
    - Status queries without touching kodosumi's DB
    """

    def __init__(self):
        self._paused = False
        self._cancelled = False
        self._status = "pending"
        self._error = None

    @workflow.run
    async def run(self, job_input: AgentJobInput) -> AgentJobResult:
        self._status = "running"

        if self._cancelled:
            self._status = "cancelled"
            return AgentJobResult(
                fid=job_input.fid, status="cancelled")

        try:
            result = await workflow.execute_activity(
                "execute_agent",
                job_input,
                start_to_close_timeout=timedelta(
                    seconds=job_input.execution_timeout),
                heartbeat_timeout=timedelta(seconds=120),
                retry_policy=RetryPolicy(
                    maximum_attempts=3,
                    backoff_coefficient=2.0,
                    initial_interval=timedelta(seconds=5),
                    maximum_interval=timedelta(seconds=60),
                ),
            )
            self._status = "completed"
            return result
        except Exception as e:
            self._status = "failed"
            self._error = str(e)
            return AgentJobResult(
                fid=job_input.fid, status="failed", error=str(e))

    @workflow.signal
    async def pause(self):
        self._paused = True
        self._status = "paused"

    @workflow.signal
    async def resume(self):
        self._paused = False
        self._status = "running"

    @workflow.signal
    async def cancel_workflow(self):
        self._cancelled = True
        self._status = "cancelled"

    @workflow.query
    def get_status(self) -> str:
        return self._status

    @workflow.query
    def is_paused(self) -> bool:
        return self._paused

    @workflow.query
    def get_error(self) -> Optional[str]:
        return self._error
