import uuid

import jwt
from jwt.exceptions import InvalidTokenError
from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException
from litestar.handlers.base import BaseRouteHandler
from litestar.middleware import (AbstractAuthenticationMiddleware,
                                 AuthenticationResult)
from sqlalchemy import select

from kodosumi import helper
from kodosumi.const import (TOKEN_KEY, HEADER_KEY, DEFAULT_TIME_DELTA,
                            ALGORITHM, JWT_SECRET)
from kodosumi.dtypes import Role, Token

def decode_jwt_token(encoded_token: str) -> Token:
    try:
        payload = jwt.decode(
            encoded_token, JWT_SECRET, algorithms=[ALGORITHM])
        return Token(**payload)
    except InvalidTokenError as e:
        raise NotAuthorizedException("Invalid token") from e


def encode_jwt_token(role_id: str) -> str:
    token = Token(
        exp=helper.now() + DEFAULT_TIME_DELTA, 
        iat=helper.now(), 
        sub=role_id)
    return jwt.encode(token.model_dump(), JWT_SECRET, algorithm=ALGORITHM)


def parse_token(scope) -> Token:
    auth_header = scope.headers.get(HEADER_KEY, None)
    auth_cookie = scope.cookies.get(TOKEN_KEY, None)
    auth = auth_header or auth_cookie
    if not auth:
        raise NotAuthorizedException()
    return decode_jwt_token(encoded_token=auth)


class JWTAuthenticationMiddleware(AbstractAuthenticationMiddleware):
    async def authenticate_request(
            self, connection: ASGIConnection) -> AuthenticationResult:
        token = parse_token(connection)
        return AuthenticationResult(user=token.sub, auth=token)
    

async def operator_guard(connection: ASGIConnection,
                         _: BaseRouteHandler) -> None:
    try:
        user = connection.user
    except:
        raise NotAuthorizedException("User not authorized")
    try:
        session = connection.app.state["session_maker_class"]()
        async with session:
            query = select(Role).where(Role.id == uuid.UUID(user))
            result = await session.execute(query)
            role = result.scalar_one_or_none()
            if not role or not role.operator:
                raise NotAuthorizedException("User not authorized")
    except NotAuthorizedException:
        raise
    except Exception:
        raise NotAuthorizedException("User not authorized")


async def sumi_network_guard(connection: ASGIConnection,
                             _: BaseRouteHandler) -> None:
    """
    Guard for /sumi endpoints with expose_name in path.

    Requires JWT authentication only when expose.network is None.
    If network is not None (Preprod/Mainnet), the endpoint is public
    as authentication is managed by external blockchain system.
    """
    from kodosumi.service.expose import db as expose_db

    expose_name = connection.path_params.get("expose_name")
    if not expose_name:
        return

    await expose_db.init_database()
    row = await expose_db.get_expose(expose_name.lower())
    if row is None:
        return  # Let handler return 404

    if row.get("network") is not None:
        return  # Blockchain auth, public

    # No network = requires JWT auth
    parse_token(connection)


async def sumi_job_network_guard(connection: ASGIConnection,
                                 _: BaseRouteHandler) -> None:
    """
    Guard for /sumi endpoints with job_id/fid in path.

    Looks up the job's expose to determine auth requirement.
    If the job's expose has network set, the endpoint is public.
    """
    from kodosumi.service.expose import db as expose_db

    job_id = connection.path_params.get("job_id") or connection.path_params.get("fid")
    if not job_id:
        return

    expose_name = await _get_expose_from_job(job_id, connection.app.state)
    if not expose_name:
        return  # Job not found, let handler deal with it

    await expose_db.init_database()
    row = await expose_db.get_expose(expose_name)
    if row is None or row.get("network") is not None:
        return  # Public

    parse_token(connection)


async def _get_expose_from_job(job_id: str, state) -> str | None:
    """Look up expose name from job's meta record (extra.sumi_endpoint)."""
    import json
    import aiosqlite
    from pathlib import Path

    exec_dir = Path(state["settings"].EXEC_DIR)

    for user_dir in exec_dir.iterdir():
        if not user_dir.is_dir():
            continue
        db_file = user_dir / job_id / "sqlite3.db"
        if db_file.exists():
            async with aiosqlite.connect(str(db_file)) as conn:
                cursor = await conn.execute(
                    "SELECT message FROM monitor WHERE kind = 'meta' LIMIT 1"
                )
                row = await cursor.fetchone()
                if row:
                    meta = json.loads(row[0])
                    # extra.sumi_endpoint = "expose_name" or "expose_name/meta_name"
                    sumi_endpoint = meta.get("extra", {}).get("sumi_endpoint")
                    if sumi_endpoint:
                        return sumi_endpoint.split("/")[0]
    return None
