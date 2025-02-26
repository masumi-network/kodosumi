from datetime import datetime
from typing import Any, Dict

from pydantic import BaseModel, RootModel, SecretStr


class DynamicModel(RootModel[Dict[str, Any]]):
    pass


class Login(BaseModel):
    username: str
    password: SecretStr


class User(BaseModel):
    username: str


class Token(BaseModel):
    exp: datetime
    iat: datetime
    sub: str
