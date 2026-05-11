"""Temporal worker process for kodosumi (PR 3).

Connects to a Temporal server, registers the AgentWorkflow and
execute_agent activity, and runs until stopped.

Usage:
    koco temporal-worker [--temporal-host HOST] [--temporal-namespace NS]

The worker needs Ray connectivity to create Runner actors.
"""
import asyncio

from kodosumi import helper
from kodosumi.log import logger


async def run_worker(settings):
    """Start the Temporal worker with AgentWorkflow and execute_agent."""
    from temporalio.client import Client
    from temporalio.worker import Worker

    from kodosumi.activities import execute_agent
    from kodosumi.workflows import AgentWorkflow

    logger.info(
        f"connecting to Temporal at {settings.TEMPORAL_HOST} "
        f"(namespace={settings.TEMPORAL_NAMESPACE})")

    client = await Client.connect(
        settings.TEMPORAL_HOST,
        namespace=settings.TEMPORAL_NAMESPACE)

    worker = Worker(
        client,
        task_queue=settings.TEMPORAL_TASK_QUEUE,
        workflows=[AgentWorkflow],
        activities=[execute_agent],
    )

    logger.info(
        f"temporal worker started on queue={settings.TEMPORAL_TASK_QUEUE}")

    await worker.run()


def main(settings):
    """Entry point: initialize Ray, then run the Temporal worker."""
    helper.ray_init(settings)
    try:
        asyncio.run(run_worker(settings))
    except KeyboardInterrupt:
        logger.info("temporal worker stopped.")
    finally:
        helper.ray_shutdown()
