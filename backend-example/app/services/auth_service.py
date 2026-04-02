from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models.user import User
from ..schemas.auth import AuthTokenResponse, UserLoginRequest, UserRegisterRequest

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def _verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


def _encode_token(user_id: int, email: str, expire_seconds: int, token_use: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(seconds=expire_seconds)
    payload = {
        "sub": str(user_id),
        "email": email,
        "token_use": token_use,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: int, email: str) -> tuple[str, int]:
    """Return (jwt_token, expires_in_seconds)."""
    expire_seconds = settings.jwt_access_token_expire_minutes * 60
    token = _encode_token(user_id, email, expire_seconds, token_use="access")
    return token, expire_seconds


def create_refresh_token(user_id: int, email: str) -> tuple[str, int]:
    """Return (refresh_jwt_token, expires_in_seconds)."""
    expire_seconds = settings.jwt_refresh_token_expire_days * 24 * 60 * 60
    token = _encode_token(user_id, email, expire_seconds, token_use="refresh")
    return token, expire_seconds


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def register_user(self, request: UserRegisterRequest) -> User:
        stmt = select(User).where(User.email == request.email)
        existing = (await self.db.execute(stmt)).scalars().first()
        if existing:
            raise ValueError("Email already registered")

        user = User(
            email=request.email,
            display_name=request.display_name,
            password_hash=_hash_password(request.password),
        )
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def login_user(self, request: UserLoginRequest) -> AuthTokenResponse:
        stmt = select(User).where(User.email == request.email)
        user = (await self.db.execute(stmt)).scalars().first()
        # constant-time: always verify even when user not found to prevent timing attacks
        dummy_hash = "$2b$12$KIXFwz9AAAABBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
        supplied_hash = user.password_hash if user else dummy_hash
        if not _verify_password(request.password, supplied_hash) or not user:
            raise ValueError("Invalid credentials")

        access_token, expires_in = create_access_token(user.id, user.email)
        refresh_token, refresh_expires_in = create_refresh_token(user.id, user.email)
        return AuthTokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in=expires_in,
            refresh_expires_in=refresh_expires_in,
        )

    async def refresh_access_token(self, refresh_token: str) -> AuthTokenResponse:
        try:
            payload = jwt.decode(refresh_token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
            token_use = payload.get("token_use")
            if token_use != "refresh":
                raise ValueError("Invalid refresh token type")
            user_id_str: str | None = payload.get("sub")
            email: str | None = payload.get("email")
            if user_id_str is None or email is None:
                raise ValueError("Invalid refresh token payload")
            user_id = int(user_id_str)
        except (JWTError, ValueError) as exc:
            raise ValueError("Invalid refresh token") from exc

        user = await self.db.get(User, user_id)
        if user is None or not user.is_active:
            raise ValueError("User not found or inactive")

        access_token, expires_in = create_access_token(user.id, user.email)
        rotated_refresh_token, refresh_expires_in = create_refresh_token(user.id, user.email)
        return AuthTokenResponse(
            access_token=access_token,
            refresh_token=rotated_refresh_token,
            token_type="bearer",
            expires_in=expires_in,
            refresh_expires_in=refresh_expires_in,
        )
