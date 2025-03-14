import uuid
from typing import Any, Dict, List, Optional

import bcrypt
from bcrypt import checkpw
from pydantic import BaseModel, EmailStr, RootModel, field_validator
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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
    path: str
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