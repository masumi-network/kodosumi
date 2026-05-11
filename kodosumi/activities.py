"""Temporal activity definitions for agent execution (PR 3).

The execute_agent activity wraps create_runner() — the same function
used by the direct execution path. All event streaming, forms, and
locks work identically. Temporal adds durability around the execution
boundary, not inside it.
"""
import asyncio

import ray
from temporalio import activity

from kodosumi.workflows import AgentJobInput, AgentJobResult


@activity.defn
async def execute_agent(job_input: AgentJobInput) -> AgentJobResult:
    """Execute an agent flow via Ray Runner, with Temporal heartbeats.

    This activity:
    1. Creates a Ray Runner actor (same as direct Launch path)
    2. Starts execution via runner.run.remote()
    3. Monitors is_active() and sends heartbeats every 5s
    4. Returns when the runner completes

    Heartbeats allow Temporal to detect worker crashes and retry
    the activity on another worker.
    """
    from kodosumi.const import NAMESPACE
    from kodosumi.runner.main import create_runner

    fid, runner = create_runner(
        username=job_input.username,
        app_url=job_input.app_url,
        entry_point=job_input.entry_point,
        inputs=job_input.inputs,
        extra=job_input.extra,
        jwt=job_input.jwt,
        panel_url=job_input.panel_url,
        fid=job_input.fid,
    )

    runner.run.remote()  # type: ignore
    activity.heartbeat(f"started {fid}")

    try:
        while True:
            await asyncio.sleep(5)
            activity.heartbeat(f"monitoring {fid}")

            try:
                done, _ = ray.wait(
                    [runner.is_active.remote()], timeout=1.0)
                if done:
                    ret = await asyncio.gather(*done)
                    if ret and ret[0] is False:
                        break
            except Exception:
                # Runner actor may have been killed or crashed
                break

        return AgentJobResult(fid=fid, status="completed")

    except asyncio.CancelledError:
        # Temporal is cancelling us (workflow cancelled or timed out)
        try:
            ray.kill(runner)
        except Exception:
            pass
        return AgentJobResult(
            fid=fid, status="cancelled",
            error="Activity cancelled by Temporal")
    except Exception as e:
        return AgentJobResult(
            fid=fid, status="failed", error=str(e))
