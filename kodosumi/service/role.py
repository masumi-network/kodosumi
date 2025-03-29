import uuid

import litestar
from litestar import delete, get, post, put
from litestar.exceptions import NotFoundException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kodosumi.dtypes import Role, RoleCreate, RoleEdit, RoleResponse
from kodosumi.log import logger
from kodosumi.service.jwt import operator_guard


class RoleControl(litestar.Controller):

    tags = ["Access Management"]
    guards=[operator_guard]

    @post("/")
    async def add_role(self, 
                       data: RoleCreate, 
                       transaction: AsyncSession) -> RoleResponse:
        role = Role(**data.model_dump())
        transaction.add(role)
        await transaction.flush()
        logger.info(f"created role {role.name} ({role.id})")
        return RoleResponse.model_validate(role)    
        
    @get("/")
    async def list_roles(self, 
                         transaction: AsyncSession) -> list[RoleResponse]:
        query = select(Role)
        result = await transaction.execute(query)
        ret = [RoleResponse.model_validate(d) for d in result.scalars().all()]
        ret.sort(key=lambda x: x.name)
        return ret
    
    @get("/{name:str}")
    async def get_role(self, 
                       name: str, 
                       transaction: AsyncSession) -> RoleResponse:
        query = select(Role).where(Role.name == name)
        result = await transaction.execute(query)
        role = result.scalar_one_or_none()
        if not role:
            try:
                uid = uuid.UUID(name)
            except:
                raise NotFoundException(detail=f"role {name} not found")
            query = select(Role).where(Role.id == uid)
            result = await transaction.execute(query)
            role = result.scalar_one_or_none()
        if role:
            return RoleResponse.model_validate(role)
        raise NotFoundException(detail=f"role {name} not found")

    @delete("/{rid:uuid}")
    async def delete_role(self, 
                          rid: uuid.UUID, 
                          transaction: AsyncSession) -> None:
        query = select(Role).where(Role.id == rid)
        result = await transaction.execute(query)
        role = result.scalar_one_or_none()
        if role:
            await transaction.delete(role)
            logger.info(f"deleted role {role.name} ({role.id})")
            return None
        raise NotFoundException(detail=f"role {rid} not found")

    @put("/{rid:uuid}")
    async def edit_role(self, 
                        rid: uuid.UUID, 
                        data: RoleEdit, 
                        transaction: AsyncSession) -> RoleResponse:
        query = select(Role).where(Role.id == rid)
        result = await transaction.execute(query)
        role = result.scalar_one_or_none()
        if not role:
            raise NotFoundException(detail=f"role {rid} not found")
        if data.name:
            role.name = data.name
        if data.email:
            role.email = data.email
        if data.password:
            role.password = data.password
        if data.active is not None:
            role.active = data.active
        if data.operator is not None:
            role.operator = data.operator
        await transaction.flush()
        logger.info(f"updated role {role.name} ({role.id})")
        return RoleResponse.model_validate(role)
