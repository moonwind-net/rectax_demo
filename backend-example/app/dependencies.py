"""FastAPI dependency helpers — authentication guard."""

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .database import get_db
from .models.user import ClientCompany, Membership, User

_bearer = HTTPBearer(auto_error=True)


@dataclass
class CurrentCompanyContext:
    company_id: int
    accounting_firm_id: int
    company_name: str


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Validate JWT bearer token and return the authenticated User row."""
    token = credentials.credentials
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        token_use = payload.get("token_use")
        if token_use == "refresh":
            raise credentials_exception
        user_id_str: str | None = payload.get("sub")
        if user_id_str is None:
            raise credentials_exception
        user_id = int(user_id_str)
    except (JWTError, ValueError):
        raise credentials_exception

    user = await db.get(User, user_id)
    if user is None:
        raise credentials_exception
    return user


async def get_current_company(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    header_company_id: str | None = Header(default=None, alias="X-Client-Company-Id"),
) -> CurrentCompanyContext:
    memberships = (
        await db.execute(
            select(Membership).where(Membership.user_id == current_user.id).where(Membership.client_company_id.is_not(None))
        )
    ).scalars().all()
    allowed_company_ids = [m.client_company_id for m in memberships if m.client_company_id is not None]

    return await _resolve_current_company(db, allowed_company_ids, header_company_id)


async def _resolve_current_company(
    db: AsyncSession,
    allowed_company_ids: list[int],
    header_company_id: str | None,
) -> CurrentCompanyContext:
    selected_company_id: int | None = None
    if header_company_id:
        try:
            selected_company_id = int(header_company_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid X-Client-Company-Id header") from exc

    if allowed_company_ids:
        if selected_company_id is None:
            selected_company_id = allowed_company_ids[0]
        if selected_company_id not in allowed_company_ids:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Selected company is not assigned to current user")
    else:
        selected_company_id = selected_company_id or settings.default_company_id

    company = await db.get(ClientCompany, selected_company_id)
    if not company or not company.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client company not found or inactive")

    return CurrentCompanyContext(
        company_id=company.id,
        accounting_firm_id=company.accounting_firm_id,
        company_name=company.name,
    )
