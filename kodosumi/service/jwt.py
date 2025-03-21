from jose import JWTError, jwt
from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException
from litestar.middleware import (AbstractAuthenticationMiddleware,
                                 AuthenticationResult)

from kodosumi import helper
from kodosumi.config import InternalSettings
from kodosumi.dtypes import Token

TOKEN_KEY = "kodosumi_jwt"
HEADER_KEY = "KODOSUMI_API_KEY"
DEFAULT_TIME_DELTA = 86400  # 1 day in seconds
ALGORITHM = "HS256"
JWT_SECRET = InternalSettings().SECRET_KEY


def decode_jwt_token(encoded_token: str) -> Token:
    try:
        payload = jwt.decode(
            token=encoded_token, key=JWT_SECRET, algorithms=[ALGORITHM])
        return Token(**payload)
    except JWTError as e:
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
    