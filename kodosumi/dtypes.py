import uuid
from enum import Enum
from typing import Any, Dict, Generic, List, Literal, Optional, Self, TypeVar

import bcrypt
from bcrypt import checkpw
from pydantic import (BaseModel, EmailStr, RootModel, field_validator,
                      model_validator)
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

T = TypeVar('T')

class DynamicModel(RootModel[Dict[str, Any]]):
    pass


class Token(BaseModel):
    exp: float
    iat: float
    sub: str


class RoleCreate(BaseModel):
    name: str
    email: EmailStr
    password: str
    operator: bool = False

    @field_validator('password', mode="before")
    def hash_password(cls, password: str) -> str:
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(password.encode(), salt).decode()


class RoleEdit(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    active: Optional[bool] = None
    password: Optional[str] = None
    operator: Optional[bool] = None

    @field_validator('password', mode="before")
    def hash_password(cls, password: Optional[str] = None) -> str | None:
        if password:
            salt = bcrypt.gensalt()
            return bcrypt.hashpw(password.encode(), salt).decode()
        return password
    

class RoleLogin(BaseModel):
    name: str
    password: str
    redirect: Optional[str] = None

class RoleResponse(BaseModel):
    id: uuid.UUID
    name: str
    email: EmailStr
    active: bool

    class Config:
        from_attributes = True


class RegisterFlow(BaseModel):
    url: str | List[str]

    @model_validator(mode='after')
    def validate_urls(self) -> Self:
        if isinstance(self.url, str):
            self.url = [self.url]
        return self


class EndpointResponse(BaseModel):
    uid: str
    method: Literal["GET", "POST", "PUT", "DELETE"]
    url: str
    source: str
    summary: Optional[str]
    description: Optional[str]
    deprecated: Optional[bool]
    author: Optional[str]
    organization: Optional[str]
    tags: List[str]

    class Config:
        from_attributes = True

class Execution(BaseModel):
    fid: str
    summary: Optional[str]
    description: Optional[str]
    author: Optional[str]
    organization: Optional[str]
    status: Optional[str]
    started_at: Optional[float]
    last_update: Optional[float]
    inputs: Optional[str]
    runtime: Optional[float]
    final: Optional[dict]
    error: Optional[List[str]]


class Pagination(BaseModel, Generic[T]):
    items: List[T]
    total: int
    p: int
    pp: int
    lp: int

    def __init__(self, **data: Any) -> None:
        data["pp"] = data.get("pp", 10)
        data["p"] = data.get("p", 0)
        data["total"] = data.get("total", 0)
        data["lp"] = (data["total"] + data["pp"] - 1) // data["pp"] - 1
        super().__init__(**data)

    @model_validator(mode='after')
    def validate_pager(self) -> Self:
        if self.p < 0:
            raise ValueError("p must be greater than or equal to 0")
        if self.p > self.lp and self.lp >= 0:
            raise ValueError(f"p must be less than or equal to {self.lp}")
        if self.pp < 1:
            raise ValueError("pp must be greater than or equal to 1")
        return self

class Base(DeclarativeBase, AsyncAttrs):
    pass


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(unique=True, nullable=False)
    email: Mapped[str] = mapped_column(unique=True, nullable=False)
    active: Mapped[bool] = mapped_column(default=True)
    password: Mapped[str] = mapped_column(nullable=False)
    operator: Mapped[bool] = mapped_column(default=False)

    def verify_password(self, password: str) -> bool:
        return checkpw(password.encode(), self.password.encode('utf-8'))

class UserRole(str, Enum):
   CONSUMER = "consumer"
   OPERATOR = "operator"


class Text(BaseModel): body: str


class HTML(BaseModel): body: str


class Markdown(BaseModel): body: str


format_map = {
    "text": Text,
    "html": HTML,
    "markdown": Markdown
}
