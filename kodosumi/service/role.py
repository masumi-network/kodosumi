import uuid

import litestar
from litestar import Request, Response, delete, get, post, put, route
from litestar.exceptions import NotAuthorizedException, NotFoundException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kodosumi.dtypes import Role, RoleCreate, RoleEdit, RoleLogin, RoleResponse
from kodosumi.log import logger
from kodosumi.service.jwt import HEADER_KEY, TOKEN_KEY, encode_jwt_token


class RoleControl(litestar.Controller):

    @post("/role")
    async def add_role(self, 
                       data: RoleCreate, 
                       transaction: AsyncSession) -> RoleResponse:
        role = Role(**data.model_dump())
        transaction.add(role)
        await transaction.flush()
        logger.info(f"created role {role.name} ({role.id})")
        return RoleResponse.model_validate(role)    
        
    @get("/role")
    async def list_roles(self, 
                         transaction: AsyncSession) -> list[RoleResponse]:
        query = select(Role)
        result = await transaction.execute(query)
        ret = [RoleResponse.model_validate(d) for d in result.scalars().all()]
        ret.sort(key=lambda x: x.name)
        return ret
    
    @get("/role/{name:str}")
    async def get_role(self, 
                       name: str, 
                       transaction: AsyncSession) -> RoleResponse:
        query = select(Role).where(Role.name == name)
        result = await transaction.execute(query)
        role = result.scalar_one_or_none()
        if role:
            return RoleResponse.model_validate(role)
        raise NotFoundException(detail=f"role {name} not found")

    @delete("/role/{rid:uuid}")
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

    async def _get_role(self, 
                        transaction: AsyncSession,
                        name: str, 
                        password: str) -> Response:
        query = select(Role).where(Role.name == name)
        result = await transaction.execute(query)
        role = result.scalar_one_or_none()
        if role:
            if role.verify_password(password):
                if role.active:
                    logger.info(f"role {role.name} ({role.id}) logged in")
                    token = encode_jwt_token(role_id=str(role.id))
                    response = Response(content={
                        "name": role.name, "id": role.id, HEADER_KEY: token})
                    response.set_cookie(key=TOKEN_KEY, value=token)
                    return response
        raise NotAuthorizedException(detail="Invalid name or password")

    @post("/login", status_code=200, opt={"no_auth": True})
    async def login_role(self, 
                         data: RoleLogin, 
                         transaction: AsyncSession) -> Response:
        return await self._get_role(transaction, data.name, data.password)

    @get("/login", status_code=200, opt={"no_auth": True})
    async def login_role_get(self, 
                             name: str, 
                             password: str, 
                             transaction: AsyncSession) -> Response:
        return await self._get_role(transaction, name, password)

    @route("/logout", status_code=200, http_method=["GET", "POST"])
    async def get_logout(self, request: Request) -> Response:
        if request.user:
            response = Response(content="")
            response.delete_cookie(key=TOKEN_KEY)
            return response
        raise NotAuthorizedException(detail="Invalid name or password")

    @put("/role/{rid:uuid}")
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
        await transaction.flush()
        logger.info(f"updated role {role.name} ({role.id})")
        return RoleResponse.model_validate(role)
