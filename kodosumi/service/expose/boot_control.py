"""
Controller for the boot and shutdown endpoints of an expose.

Starts and stops the Ray Serve application of an expose and streams the
progress of a running boot back to the admin panel.

All endpoints require operator role authentication.
"""

import asyncio
import logging
import uuid

import litestar
from litestar import Request, delete, get, post
from litestar.datastructures import State
from litestar.response import Stream, Template
from sqlalchemy import select

from kodosumi.dtypes import Role
from kodosumi.service.expose.boot import (
    BootStep,
    boot_lock,
    get_ray_serve_address_from_config,
    run_shutdown,
    start_boot_background,
)
from kodosumi.service.jwt import operator_guard
from kodosumi.service.expose import db

logger = logging.getLogger(__name__)


async def get_username(user_id: str, state: State) -> str:
    """
    Look up username from user ID.

    Args:
        user_id: UUID string of the user
        state: Litestar state containing session_maker_class

    Returns:
        Username string, or the user_id if lookup fails
    """
    try:
        session = state["session_maker_class"]()
        async with session:
            query = select(Role).where(Role.id == uuid.UUID(user_id))
            result = await session.execute(query)
            role = result.scalar_one_or_none()
            if role:
                return role.name
    except Exception:
        pass
    return user_id  # Fallback to ID if lookup fails


class BootControl(litestar.Controller):
    """Controller for boot/shutdown endpoints."""

    path = "/boot"
    tags = ["Boot"]
    guards = [operator_guard]

    @post(
        "",
        summary="Boot all enabled exposures",
        description="Start Ray Serve deployment for all enabled exposures. Returns streaming text output.",
        operation_id="boot_start",
    )
    async def boot(
        self,
        request: Request,
        state: State,
        force: bool = False,
    ) -> Stream:
        """
        Execute boot process with streaming output.

        The boot runs as a background task so it continues even if
        the client disconnects. The initiator subscribes to the
        message stream just like late joiners.

        Args:
            force: Override existing boot lock if True
        """
        # Get settings
        ray_dashboard = state["settings"].RAY_DASHBOARD
        # Get Ray Serve address from serve config (with fallback to settings)
        ray_serve_address = get_ray_serve_address_from_config(
            fallback=state["settings"].RAY_SERVE_ADDRESS
        )
        app_server = state["settings"].APP_SERVER
        boot_timeout = state["settings"].BOOT_HEALTH_TIMEOUT

        # Get auth cookies from request
        auth_cookies = dict(request.cookies)

        # Get username for audit logging
        owner = await get_username(request.user, state) if request.user else "operator"

        # Start boot as background task
        started = await start_boot_background(
            ray_dashboard=ray_dashboard,
            ray_serve_address=ray_serve_address,
            app_server=app_server,
            auth_cookies=auth_cookies,
            force=force,
            owner=owner,
            boot_timeout=boot_timeout
        )

        if not started and not force:
            # Boot already in progress, return error
            async def already_running():
                yield "[ERROR] Boot already in progress. Use force=true to override.\n"
            return Stream(already_running(), media_type="text/plain")

        # Subscribe to message stream (same as late joiner)
        queue = boot_lock.subscribe()

        async def generate():
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=0.5)
                        yield f"{msg}\n"
                        if msg.step in (BootStep.COMPLETE, BootStep.ERROR):
                            break
                    except asyncio.TimeoutError:
                        if not boot_lock.is_locked and queue.empty():
                            break
                        continue
            finally:
                boot_lock.unsubscribe(queue)

        return Stream(generate(), media_type="text/plain")

    @get(
        "",
        summary="Get boot status",
        description="Get current boot status and messages if boot is in progress.",
        operation_id="boot_status",
    )
    async def boot_status(self, state: State) -> dict:
        """Get current boot lock status."""
        return {
            "locked": boot_lock.is_locked,
            "lock_time": boot_lock.lock_time,
            "messages": [str(m) for m in boot_lock.messages]
        }

    @get(
        "/stream",
        summary="Stream boot messages",
        description="Subscribe to boot message stream (for operators joining an in-progress boot).",
        operation_id="boot_stream",
    )
    async def boot_stream(self, state: State) -> Stream:
        """Stream boot messages to client."""
        if not boot_lock.is_locked:
            async def no_boot():
                yield "No boot in progress\n"
            return Stream(no_boot(), media_type="text/plain")

        queue = boot_lock.subscribe()

        async def generate():
            try:
                while True:
                    try:
                        # Short timeout to check for new messages
                        msg = await asyncio.wait_for(queue.get(), timeout=0.5)
                        yield f"{msg}\n"
                        if msg.step in (BootStep.COMPLETE, BootStep.ERROR):
                            break
                    except asyncio.TimeoutError:
                        # If lock released and queue empty, we're done
                        if not boot_lock.is_locked and queue.empty():
                            break
                        continue
            finally:
                boot_lock.unsubscribe(queue)

        return Stream(generate(), media_type="text/plain")

    @delete(
        "",
        summary="Shutdown Ray Serve",
        description="Execute serve shutdown command.",
        operation_id="boot_shutdown",
        status_code=200,
    )
    async def shutdown(self, request: Request, state: State) -> Stream:
        """Execute shutdown with streaming output."""
        # Get app server and auth cookies for flow register call
        app_server = str(request.base_url).rstrip("/")
        auth_cookies = dict(request.cookies) if request.cookies else None

        # Get username for audit logging
        owner = await get_username(request.user, state) if request.user else "operator"

        async def generate():
            async for msg in run_shutdown(app_server, auth_cookies, owner):
                yield f"{msg}\n"

        return Stream(generate(), media_type="text/plain")

    @post(
        "/refresh/{name:str}",
        summary="Refresh single expose",
        description="Refresh a single expose by: disable → boot → enable → boot.",
        operation_id="boot_refresh_expose",
        status_code=200,
    )
    async def refresh_expose(
        self,
        name: str,
        request: Request,
        state: State,
    ) -> Stream:
        """
        Refresh a single expose.

        This runs the full refresh cycle:
        1. Disable the expose
        2. Run boot process (removes the expose's flows)
        3. Enable the expose
        4. Run boot process again (re-adds the expose's flows)
        """
        from kodosumi.service.expose.boot import run_refresh_expose

        # Check if expose exists
        await db.init_database()
        expose = await db.get_expose(name)
        if not expose:
            async def not_found():
                yield f"[ERROR] Expose '{name}' not found\n"
            return Stream(not_found(), media_type="text/plain")

        # Get config from state
        ray_dashboard = state["settings"].RAY_DASHBOARD
        ray_serve_address = get_ray_serve_address_from_config()
        app_server = state["settings"].APP_SERVER
        auth_cookies = dict(request.cookies) if request.cookies else None

        async def generate():
            async for msg in run_refresh_expose(
                expose_name=name,
                ray_dashboard=ray_dashboard,
                ray_serve_address=ray_serve_address,
                app_server=app_server,
                auth_cookies=auth_cookies,
            ):
                yield f"{msg}\n"

        return Stream(generate(), media_type="text/plain")


class BootUIControl(litestar.Controller):
    """Controller for boot UI pages."""

    path = "/admin/expose/boot"
    tags = ["Boot UI"]
    guards = [operator_guard]

    @get(
        "",
        summary="Boot screen",
        description="Display the boot console screen.",
        operation_id="boot_page",
    )
    async def boot_page(self, state: State) -> Template:
        """Render the boot screen."""
        return Template("expose/boot.html", context={
            "is_locked": boot_lock.is_locked,
            "messages": [str(m) for m in boot_lock.messages]
        })

    @get(
        "/shutdown",
        summary="Shutdown confirmation screen",
        description="Display shutdown confirmation dialog.",
        operation_id="shutdown_page",
    )
    async def shutdown_page(self, state: State) -> Template:
        """Render the shutdown confirmation screen."""
        return Template("expose/shutdown.html", context={})

    @get(
        "/refresh/{name:str}",
        summary="Refresh expose screen",
        description="Display boot console for refreshing a single expose.",
        operation_id="refresh_expose_page",
    )
    async def refresh_expose_page(self, name: str, state: State) -> Template:
        """Render the boot console for refreshing a specific expose."""
        return Template("expose/boot.html", context={
            "is_locked": boot_lock.is_locked,
            "messages": [str(m) for m in boot_lock.messages],
            "refresh_expose": name,
        })
