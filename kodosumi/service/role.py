import uuid
from typing import Union
import litestar
from litestar import delete, get, post, put
from litestar.exceptions import NotFoundException, HTTPException
from litestar.status_codes import HTTP_409_CONFLICT
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from kodosumi.dtypes import Role, RoleCreate, RoleEdit, RoleResponse
from kodosumi.log import logger
from kodosumi.service.jwt import operator_guard


async def update_role(rid: Union[uuid.UUID, str],
                      data: RoleEdit, 
                      transaction: AsyncSession) -> RoleResponse:
    if isinstance(rid, str):
        rid = uuid.UUID(rid)
    query = select(Role).where(Role.id == rid)
    result = await transaction.execute(query)
    role = result.scalar_one_or_none()
    if not role:
        raise NotFoundException(detail=f"role {rid} not found")
    update = False
    for field in ("name", "email", "password", "active", "operator"):
        new = getattr(data, field)
        current = getattr(role, field)
        if new is not None and new != current:
            setattr(role, field, new)
            update = True
    if update:
        await transaction.flush()
        logger.info(f"updated role {role.name} ({role.id})")
    return RoleResponse.model_validate(role)


class RoleControl(litestar.Controller):

    tags = ["Access Management"]
    guards=[operator_guard]

    @post("/", summary="Add Role", description="Add a new role to the system.", operation_id="20_add_role")
    async def add_role(self, 
                       data: RoleCreate, 
                       transaction: AsyncSession) -> RoleResponse:
        role = Role(**data.model_dump())
        transaction.add(role)
        try:
            await transaction.flush()
        except IntegrityError as exc:
            logger.error(f"error creating role {role.name} ({role.id}): {exc}")
            raise HTTPException(
                status_code=HTTP_409_CONFLICT,
                detail=f"Role {role.name} ({role.id}) already exists"
            ) from exc
        logger.info(f"created role {role.name} ({role.id})")
        return RoleResponse.model_validate(role)    
        
    @get("/", summary="List Roles", description="List all roles in the system.", operation_id="21_list_roles")
    async def list_roles(self, 
                         transaction: AsyncSession) -> list[RoleResponse]:
        query = select(Role)
        result = await transaction.execute(query)
        ret = [RoleResponse.model_validate(d) for d in result.scalars().all()]
        ret.sort(key=lambda x: x.name)
        return ret
    
    @get("/{name:str}", summary="Get Role by Name or ID", 
         description="Get a role by name or ID.", operation_id="22_get_role")
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

    @delete("/{rid:uuid}", summary="Delete Role by ID", 
            description="Delete a role by ID.", operation_id="23_delete_role")
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

    @put("/{rid:uuid}", summary="Update Role by ID", 
         description="Update a role by ID.", operation_id="24_edit_role")
    async def edit_role(self, 
                        rid: uuid.UUID, 
                        data: RoleEdit, 
                        transaction: AsyncSession) -> RoleResponse:
        return await update_role(rid, data, transaction)

