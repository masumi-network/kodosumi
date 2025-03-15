import uuid
from typing import Any, Dict, List, Optional, Literal

import bcrypt
from bcrypt import checkpw
from pydantic import BaseModel, EmailStr, RootModel, field_validator
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from typing import Generic, TypeVar


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

    @field_validator('password', mode="before")
    def hash_password(cls, password: str) -> str:
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(password.encode(), salt).decode()


class RoleEdit(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    active: Optional[bool] = None
    password: Optional[str] = None

    @field_validator('password', mode="before")
    def hash_password(cls, password: Optional[str] = None) -> str | None:
        if password:
            salt = bcrypt.gensalt()
            return bcrypt.hashpw(password.encode(), salt).decode()
        return password
    

class RoleLogin(BaseModel):
    name: str
    password: str

class RoleResponse(BaseModel):
    id: uuid.UUID
    name: str
    email: EmailStr
    active: bool

    class Config:
        from_attributes = True


class RegisterFlow(BaseModel):
    url: str


class Base(DeclarativeBase, AsyncAttrs):
    pass


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(unique=True, nullable=False)
    email: Mapped[str] = mapped_column(unique=True, nullable=False)
    active: Mapped[bool] = mapped_column(default=True)
    password: Mapped[str] = mapped_column(nullable=False)

    def verify_password(self, password: str) -> bool:
        return checkpw(password.encode(), self.password.encode('utf-8'))


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
    # created_at: float
    # updated_at: float

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


T = TypeVar('T')

class Pagination(BaseModel, Generic[T]):
    items: List[T]
    total: int
    p: Optional[int] = 0
    pp: Optional[int] = 10

    @field_validator('p')
    def validate_page(cls, v, values):
        total = values.data.get('total', len(values.data.get('items', [])))
        pp = values.data.get('pp', 10)
        if pp <= 0:
            raise ValueError("pp must be greater than 0")
        max_pages = (total + pp - 1) // pp
        if v < 0:
            raise ValueError("p must be greater than or equal to 0")
        if v > max_pages:
            raise ValueError(f"p must be less than or equal to {max_pages}")
        return v
