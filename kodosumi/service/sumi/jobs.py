"""
Job submission and job status for the Sumi protocol.

Starts a flow on behalf of a Masumi buyer, heals a missing agent identifier
from the registry, and reads the status of a job back out of the event log.
"""

import asyncio
import json
import logging
import sqlite3
import time
from typing import Literal, Optional, Union

import yaml

from litestar import Request
from litestar.exceptions import HTTPException
import ray

from kodosumi import dtypes
from kodosumi.const import (
    ANNONYMOUS, KODOSUMI_LAUNCH, NAMESPACE, STATUS_END, STATUS_ERROR,
    STATUS_PAYMENT)
from kodosumi.helper import ProxyRequest, proxy_forward
from kodosumi.service.expose.models import ExposeMeta
from kodosumi.service.sumi.flows import (
    _extract_result_string, _fetch_input_schema, _format_service_id,
    _parse_meta_data)
from kodosumi.service.sumi.hash import create_input_hash
from kodosumi.service.sumi.models import (
    JobStatusResponse, StartJobErrorResponse, StartJobRequest)
from kodosumi.service.sumi.schema import convert_mip003_indices_to_values

logger = logging.getLogger(__name__)


async def _heal_agent_identifier(
    expose_name: str,
    meta: ExposeMeta,
    meta_data_dict: dict,
    state,
) -> Optional[str]:
    """Query Masumi registry for agentIdentifier and persist it in expose meta."""
    from kodosumi.service.expose.registry import get_registration_status
    from kodosumi.service.expose.db import get_expose, update_expose_meta

    registration_id = meta_data_dict.get("registrationId", "")
    network = meta_data_dict.get("network") or ""
    if not network:
        row = await get_expose(expose_name)
        network = (row or {}).get("network", "")
    if not network or not registration_id:
        return None

    try:
        settings = state["settings"]
        masumi_cfg = settings.get_masumi(network)
    except (ValueError, KeyError):
        return None

    try:
        reg = await get_registration_status(
            masumi_cfg,
            registration_id=registration_id,
            # Without the rail the registry list falls back to V1 and a V2
            # registration is never found.
            payment_source_type=meta_data_dict.get("paymentSourceType"),
        )
    except Exception:
        return None

    if not reg:
        return None
    agent_id = reg.get("agentIdentifier", "")
    if not agent_id:
        return None

    # Persist back to expose meta
    try:
        row = await get_expose(expose_name)
        if row and row.get("meta"):
            import yaml
            outer = yaml.safe_load(row["meta"])
            if isinstance(outer, list):
                for entry in outer:
                    if not isinstance(entry, dict):
                        continue
                    data_str = entry.get("data", "")
                    if not data_str:
                        continue
                    inner = yaml.safe_load(data_str)
                    if isinstance(inner, dict) and inner.get("registrationId") == registration_id:
                        inner["agentIdentifier"] = agent_id
                        entry["data"] = yaml.dump(inner, default_flow_style=False, allow_unicode=True)
                        new_meta = yaml.dump(outer, default_flow_style=False, allow_unicode=True)
                        await update_expose_meta(expose_name, new_meta)
                        logger.info("Self-healed agentIdentifier for %s", expose_name)
                        break
    except Exception as e:
        logger.warning("Failed to persist healed agentIdentifier for %s: %s", expose_name, e)

    return agent_id


async def _advance_pending_migration(
    expose_name: str,
    meta: ExposeMeta,
    meta_data_dict: dict,
    network: str,
    state,
) -> dict:
    """
    Finish a V1 to V2 migration whose new mint already confirmed.

    The admin panel is the only other caller, so a flow whose operator has
    the page closed would keep quoting the replaced agent identifier and
    the old rail for every buyer. Returns the metadata to price this job
    with, unchanged when there is nothing to advance.
    """
    from kodosumi.service.expose.db import get_expose
    from kodosumi.service.expose.flow_meta import get_flow_meta
    from kodosumi.service.expose.migration import (
        advance_migration, pending_migration)

    if state is None or not pending_migration(meta_data_dict):
        return meta_data_dict

    try:
        row = await get_expose(expose_name)
        if not row:
            return meta_data_dict
        masumi_cfg = state["settings"].get_masumi(
            network or row.get("network") or "")
        # allow_burn stays off: a job must never burn an agent.
        migration = await advance_migration(
            masumi_cfg, row, expose_name, meta.url, meta_data_dict)
        if not (migration and migration.get("updatedYaml")):
            return meta_data_dict
        row = await get_expose(expose_name) or row
        return get_flow_meta(row, meta.url) or meta_data_dict
    except Exception as e:
        # A job must not fail because a migration could not advance.
        logger.warning(
            "Could not advance the migration of %s%s: %s",
            expose_name, meta.url, e)
        return meta_data_dict


async def _submit_job(
    expose_name: str,
    meta_name: str,
    meta: ExposeMeta,
    network: str,
    data: StartJobRequest,
    app_server: str,
    ray_serve_address: str,
    request: Request,
    state = None,
) -> Union[JobStatusResponse, StartJobErrorResponse]:
    """
    Submit a job to a service endpoint.

    Uses the shared proxy_forward utility to ensure consistent header handling
    with ProxyControl.forward.

    Args:
        expose_name: Name of the expose
        meta_name: Name of the meta entry (empty for root)
        meta: ExposeMeta with endpoint URL
        network: Blockchain network (e.g., "Preprod", "Mainnet")
        data: StartJobRequest with input data
        app_server: App server URL
        ray_serve_address: Ray Serve HTTP address
        request: Original request for user/cookie forwarding

    Returns:
        JobStatusResponse on success, StartJobErrorResponse on failure
    """
    # DEBUG: Log incoming request BEFORE forwarding to agent
    with open("/srv/kodosumi/data/sumi_debug.log", "a") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] start_job: {expose_name}/{meta_name}\n")
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] identifier_from_purchaser: {data.identifier_from_purchaser}\n")
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] input_data: {json.dumps(data.input_data, default=str)}\n")

    # Convert MIP-003 index arrays to string values for option/radio fields
    # Masumi sends [1] for second option, agents expect "Man"
    schema = await _fetch_input_schema(ray_serve_address, meta)
    converted_input = convert_mip003_indices_to_values(data.input_data, schema)

    service_id = _format_service_id(expose_name, meta_name)
    meta_data_dict = _parse_meta_data(meta.data)
    # A migration that confirmed on chain has to reach the serving path,
    # not only the admin panel: this job must be priced on the new rail.
    meta_data_dict = await _advance_pending_migration(
        expose_name, meta, meta_data_dict, network, state)
    agent_identifier = meta_data_dict.get("agentIdentifier")
    registration_id = meta_data_dict.get("registrationId")

    # Self-heal: if registrationId exists but agentIdentifier is missing,
    # query the Masumi registry live and persist the result (#58).
    if registration_id and not agent_identifier:
        agent_identifier = await _heal_agent_identifier(
            expose_name, meta, meta_data_dict, state
        )
        if not agent_identifier:
            raise HTTPException(
                status_code=400,
                detail="Agent registration is incomplete (registrationId set but agentIdentifier missing). "
                       "Wait for on-chain confirmation or re-register."
            )

    # Paid agents require identifier_from_purchaser for payment validation.
    # Without it, anyone could start jobs on paid agents without paying.
    if agent_identifier and not data.identifier_from_purchaser:
        raise HTTPException(
            status_code=400,
            detail="identifier_from_purchaser is required for paid agents"
        )

    input_hash = create_input_hash(data.input_data, data.identifier_from_purchaser)
    endpoint_url = ray_serve_address.rstrip("/") + meta.url
    # Extract application root URL (without entry path) for KODOSUMI_BASE header.
    # Lock routes (/_lock_/) are registered on ServeAPI root, not under entry paths.
    # This mirrors the old behavior: "X-Kodosumi-Base": f"/-/{expose_name}"
    app_root_url = ray_serve_address.rstrip("/") + "/" + expose_name
    started_at = time.time()

    # Extra metadata stored with the job
    # Include network so Runner can initialize payment without DB access.
    # paymentSourceType and supportedPaymentSourceIndex are written into the
    # flow metadata by a Web3CardanoV2 registration. The Runner needs both to
    # create the payment on the right rail.
    extra = {
        "identifier_from_purchaser": data.identifier_from_purchaser,
        "input_hash": input_hash,
        "sumi_endpoint": service_id,
        "agentIdentifier": agent_identifier,
        "network": network,
        "paymentSourceType": meta_data_dict.get("paymentSourceType"),
        "supportedPaymentSourceIndex": meta_data_dict.get(
            "supportedPaymentSourceIndex"),
        "raw_input_data": data.input_data,  # Debug: raw input from Sumi start_job
    }

    def _error_response(error_msg: str) -> StartJobErrorResponse:
        return StartJobErrorResponse(error=error_msg)

    try:
        user = request.user
    except Exception:
        user = ANNONYMOUS
    try:
        # Use shared proxy utility with consistent header handling
        # base is the application root URL (without entry path) - lock routes
        # are registered on ServeAPI root, not under entry paths
        proxy_config = ProxyRequest(
            target_url=endpoint_url,
            method="POST",
            user=user,
            base=app_root_url,
            app_url=app_server,
            json_body=converted_input or {},
            headers=dict(request.headers),
            cookies=dict(request.cookies),
            extra=extra,
            timeout=30.0,
        )

        resp = await proxy_forward(proxy_config)

        # DEBUG: Log agent response
        with open("/srv/kodosumi/data/sumi_debug.log", "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] agent response: status={resp.status_code}\n")
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] agent headers: {dict(resp.headers)}\n")
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] agent body: {resp.content.decode()[:2000]}\n")

        if resp.status_code != 200:
            return _error_response(
                f"Service returned HTTP {resp.status_code}: {resp.content.decode()}"
            )

        response_data = resp.json()

        # Check for job ID in response - can come from KODOSUMI_LAUNCH header or response body
        job_id = resp.headers.get(KODOSUMI_LAUNCH) or response_data.get("result") or response_data.get("fid")

        if not job_id:
            errors = response_data.get("errors")
            if errors:
                # Validation errors - format them as error message
                error_msg = "; ".join(f"{k}: {v}" for k, v in errors.items())
                return _error_response(error_msg)
            return _error_response("Service did not return a job ID (fid)")

        # Call prepare on the Runner actor to get payment init data.
        # prepare() is idempotent — if start() already called it,
        # returns the cached result.
        blockchain_id = None
        pay_by_time = None
        submit_result_time = None
        unlock_time = None
        ext_dispute_unlock_time = None
        seller_vkey = None
        payment_source_type = None
        source_index = None
        try:
            # Use asyncio.to_thread to avoid blocking the event loop
            runner = await asyncio.to_thread(ray.get_actor, job_id, namespace=NAMESPACE)
            prepare_data = await runner.prepare.remote()
            if prepare_data:
                pay_data = prepare_data["pay_data"]
                blockchain_id = prepare_data["blockchain_identifier"]
                pay_by_time = int(pay_data["payByTime"]) if pay_data.get("payByTime") else None
                submit_result_time = int(pay_data["submitResultTime"]) if pay_data.get("submitResultTime") else None
                unlock_time = int(pay_data["unlockTime"]) if pay_data.get("unlockTime") else None
                ext_dispute_unlock_time = int(pay_data["externalDisputeUnlockTime"]) if pay_data.get("externalDisputeUnlockTime") else None
                sc_wallet = pay_data.get("SmartContractWallet") or {}
                seller_vkey = sc_wallet.get("walletVkey")
                # Read the rail from the payment config the Runner used, not
                # from the flow metadata: the payment was created with that
                # value and the seller signature covers it.
                pay_conf = prepare_data.get("pay_conf") or {}
                payment_source_type = pay_conf.get("paymentSourceType")
                source_index = pay_conf.get("supportedPaymentSourceIndex")
        except Exception as e:
            # Actor not found, or payment init was refused. A free flow can
            # carry on; a registered one cannot, see the check below.
            logger.warning(
                "prepare() failed for job %s of %s%s: %s",
                job_id, expose_name, meta.url, e,
            )

        # A registered agent is a paid agent. Without a blockchainIdentifier
        # the buyer has nothing to pay against, so answering "running" would
        # hide the failure behind a job that goes on to error inside the
        # runner. Report the payment failure instead.
        #
        # The cause stays in the log. A Ray remote call raises with the whole
        # remote traceback attached, and this response goes to an external
        # Sumi consumer, which the rest of this module never gives internals
        # to either.
        if agent_identifier and not blockchain_id:
            logger.error(
                "Refusing job %s of %s%s: no payment for a registered agent",
                job_id, expose_name, meta.url,
            )
            return _error_response(
                "Payment could not be initialized for this agent. "
                "Try again later, or contact the agent operator."
            )

        return JobStatusResponse(
            job_id=job_id,
            status="awaiting_payment" if blockchain_id else "running",
            identifierFromPurchaser=data.identifier_from_purchaser,
            input_hash=input_hash,
            agentIdentifier=agent_identifier,
            blockchainIdentifier=blockchain_id,
            payByTime=pay_by_time,
            submitResultTime=submit_result_time,
            unlockTime=unlock_time,
            externalDisputeUnlockTime=ext_dispute_unlock_time,
            sellerVKey=seller_vkey,
            paymentSourceType=payment_source_type,
            supportedPaymentSourceIndex=source_index,
            startedAt=started_at,
            updatedAt=time.time(),
        )

    except Exception as e:
        return _error_response(f"Failed to submit job: {type(e).__name__}: {e}")


async def _get_job_status_from_db(
    conn: sqlite3.Connection, job_id: str
) -> tuple:
    """
    Query job status from the monitor database.

    Maps Kodosumi status to MIP-003 status.

    Returns:
        Tuple of (JobStatusResponse, pending_lock_ids) where pending_lock_ids
        is a set of lock IDs that are awaiting input.
    """
    cursor = conn.cursor()

    # Get timestamps
    cursor.execute("""
        SELECT MIN(timestamp), MAX(timestamp) FROM monitor
    """)
    first_ts, last_ts = cursor.fetchone()

    # Get current status
    cursor.execute("""
        SELECT message FROM monitor WHERE kind = 'status'
        ORDER BY timestamp DESC, id DESC
        LIMIT 1
    """)
    row = cursor.fetchone()
    kodo_status = row[0] if row else None

    # Get final result (MIP-003 requires string)
    cursor.execute("""
        SELECT message FROM monitor WHERE kind = 'final'
        ORDER BY timestamp DESC, id DESC
        LIMIT 1
    """)
    row = cursor.fetchone()
    final_result = None
    if row:
        try:
            parsed = dtypes.DynamicModel.model_validate_json(row[0])
            final_result = _extract_result_string(parsed.model_dump())
        except Exception:
            # Fallback: return raw string on parse failure
            final_result = row[0] if isinstance(row[0], str) else str(row[0])

    # Get error if any — sanitize traceback for external consumers.
    # The full traceback is preserved in the monitor DB for admin debugging,
    # but external Sumi consumers only see the exception message (last line).
    cursor.execute("""
        SELECT message FROM monitor WHERE kind = 'error'
        ORDER BY timestamp DESC, id DESC
        LIMIT 1
    """)
    row = cursor.fetchone()
    error_msg = None
    if row and row[0]:
        raw_error = row[0]
        lines = [l.strip() for l in raw_error.strip().split("\n") if l.strip()]
        if lines:
            last_line = lines[-1]
            # Remove exception class prefix (e.g. "kodosumi.error.KodosumiError: ")
            for sep in (": ",):
                if sep in last_line:
                    _, _, msg = last_line.partition(sep)
                    error_msg = msg
                    break
            else:
                error_msg = last_line

    # Get meta for identifier_from_purchaser
    cursor.execute("""
        SELECT message FROM monitor WHERE kind = 'meta'
        ORDER BY timestamp DESC, id DESC
        LIMIT 1
    """)
    row = cursor.fetchone()
    identifier = None
    agent_identifier = None
    if row:
        try:
            meta_data = dtypes.DynamicModel.model_validate_json(row[0])
            meta_dict = meta_data.root.get("dict", {})
            extra = meta_dict.get("extra", {})
            if isinstance(extra, dict):
                identifier = extra.get("identifier_from_purchaser")
                agent_identifier = extra.get("agentIdentifier")
        except Exception:
            pass

    # Get payment data from EVENT_PAYMENT records
    blockchain_id = None
    pay_by_time = None
    submit_result_time = None
    unlock_time = None
    ext_dispute_unlock_time = None
    seller_vkey = None
    payment_source_type = None
    source_index = None
    cursor.execute("""
        SELECT message FROM monitor WHERE kind = 'payment'
        ORDER BY timestamp ASC
    """)
    for (msg,) in cursor.fetchall():
        try:
            pay_event = dtypes.DynamicModel.model_validate_json(msg)
            pay_dict = pay_event.root.get("dict", {})
            if pay_dict.get("step") == "initialized":
                blockchain_id = pay_dict.get("blockchainIdentifier")
                pd = pay_dict.get("pay_data", {})
                pay_by_time = int(pd["payByTime"]) if pd.get("payByTime") else None
                submit_result_time = int(pd["submitResultTime"]) if pd.get("submitResultTime") else None
                unlock_time = int(pd["unlockTime"]) if pd.get("unlockTime") else None
                ext_dispute_unlock_time = int(pd["externalDisputeUnlockTime"]) if pd.get("externalDisputeUnlockTime") else None
                sc_wallet = pd.get("SmartContractWallet") or {}
                seller_vkey = sc_wallet.get("walletVkey")
                # V2 rail markers. Absent on V1 payments and on payment
                # events written before this field existed.
                payment_source_type = pay_dict.get("paymentSourceType")
                source_index = pay_dict.get("supportedPaymentSourceIndex")
        except Exception:
            pass

    # Check for locks (awaiting_input)
    cursor.execute("""
        SELECT kind, message FROM monitor
        WHERE kind IN ('lock', 'lease')
        ORDER BY timestamp ASC
    """)
    locks = set()
    for kind, msg in cursor.fetchall():
        try:
            d = dtypes.DynamicModel.model_validate_json(msg)
            lid = d.root["dict"]["lid"]
            if kind == "lock":
                locks.add(lid)
            else:
                locks.discard(lid)
        except Exception:
            pass

    # Map Kodosumi status to MIP-003 status
    # Kodosumi statuses: starting, running, payment, finished, error
    # MIP-003 statuses: awaiting_payment, awaiting_input, running, completed, failed
    #
    # Runner emits EVENT_STATUS with these values:
    # - STATUS_PAYMENT ("payment") when awaiting payment
    # - STATUS_RUNNING ("running") after payment confirmed (main.py:303)
    # - STATUS_END ("finished") on success
    # - STATUS_ERROR ("error") on failure
    mip_status: Literal[
        "awaiting_payment", "awaiting_input", "running", "completed", "failed"
    ]
    if kodo_status == STATUS_END:
        mip_status = "completed"
    elif kodo_status == STATUS_ERROR:
        mip_status = "failed"
    elif kodo_status == STATUS_PAYMENT:
        mip_status = "awaiting_payment"
    elif locks:
        # Pending locks indicate awaiting human input
        mip_status = "awaiting_input"
    else:
        mip_status = "running"

    # Calculate runtime
    runtime = None
    if first_ts and last_ts:
        runtime = last_ts - first_ts

    response = JobStatusResponse(
        job_id=job_id,
        status=mip_status,
        result=final_result if mip_status == "completed" else None,
        error=error_msg if mip_status == "failed" else None,
        input_schema=None,  # Populated by caller when awaiting_input
        identifierFromPurchaser=identifier,
        agentIdentifier=agent_identifier,
        blockchainIdentifier=blockchain_id,
        payByTime=pay_by_time,
        submitResultTime=submit_result_time,
        unlockTime=unlock_time,
        externalDisputeUnlockTime=ext_dispute_unlock_time,
        sellerVKey=seller_vkey,
        paymentSourceType=payment_source_type,
        supportedPaymentSourceIndex=source_index,
        startedAt=first_ts,
        updatedAt=last_ts,
        runtime=runtime,
    )
    return response, locks
