from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import get_current_user
from ..models.user import Membership, ClientCompany, User
from ..schemas.auth import AuthTokenResponse, RefreshTokenRequest, UserLoginRequest, UserRegisterRequest, UserResponse
from ..services.auth_service import AuthService

router = APIRouter()


@router.post("/register", response_model=UserResponse)
async def register(request: UserRegisterRequest, db: AsyncSession = Depends(get_db)):
    service = AuthService(db)
    try:
        user = await service.register_user(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return UserResponse(id=user.id, email=user.email, display_name=user.display_name)


@router.post("/login", response_model=AuthTokenResponse)
async def login(request: UserLoginRequest, db: AsyncSession = Depends(get_db)):
    service = AuthService(db)
    try:
        return await service.login_user(request)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/refresh", response_model=AuthTokenResponse)
async def refresh_token(request: RefreshTokenRequest, db: AsyncSession = Depends(get_db)):
    service = AuthService(db)
    try:
        return await service.refresh_access_token(request.refresh_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/session")
async def get_session_context(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    memberships = (
        await db.execute(
            select(Membership).where(Membership.user_id == current_user.id).where(Membership.client_company_id.is_not(None))
        )
    ).scalars().all()
    company_ids = [m.client_company_id for m in memberships if m.client_company_id is not None]
    if company_ids:
        companies = (
            await db.execute(select(ClientCompany).where(ClientCompany.id.in_(company_ids)).order_by(ClientCompany.id.asc()))
        ).scalars().all()
    else:
        companies = (
            await db.execute(select(ClientCompany).where(ClientCompany.is_active.is_(True)).order_by(ClientCompany.id.asc()))
        ).scalars().all()

    return {
        "user": {
            "id": current_user.id,
            "email": current_user.email,
            "display_name": current_user.display_name,
        },
        "companies": [
            {
                "id": company.id,
                "accounting_firm_id": company.accounting_firm_id,
                "code": company.code,
                "name": company.name,
            }
            for company in companies
        ],
    }
