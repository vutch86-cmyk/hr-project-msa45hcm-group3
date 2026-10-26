from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from google.oauth2 import id_token
from google.auth.transport import requests
from google.auth.exceptions import GoogleAuthError
from app.core.database import get_db
from app.core.security import verify_password, create_access_token, create_refresh_token
from app.core.config import settings
from app.models.auth import UserAccount, UserRoleAssignment
from app.schemas.auth import LoginRequest, Token, UserResponse, GoogleLoginRequest, MicrosoftLoginRequest
from app.api.deps import get_session_user

from starlette.concurrency import run_in_threadpool
from app.core.microsoft_auth import microsoft_configuration, verify_microsoft_token
from app.services.microsoft_provisioning import provision_microsoft_user
from sqlalchemy.exc import IntegrityError

router = APIRouter()


@router.post("/login", response_model=Token)
async def login(login_data: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Authenticate user and return access & refresh JWT tokens"""
    stmt = (
        select(UserAccount)
        .where(UserAccount.login_email == login_data.login_email)
        .options(
            selectinload(UserAccount.employee),
            selectinload(UserAccount.role_assignments).selectinload(UserRoleAssignment.role)
        )
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user or not user.password_hash or not verify_password(login_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive"
        )

    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()

    access_token = create_access_token(subject=user.user_account_id)
    refresh_token = create_refresh_token(subject=user.user_account_id)

    return Token(access_token=access_token, refresh_token=refresh_token)


@router.post("/login/google", response_model=Token)
async def login_with_google(login_data: GoogleLoginRequest, db: AsyncSession = Depends(get_db)):
    """Authenticate user with Google id_token"""
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server chưa được cấu hình GOOGLE_CLIENT_ID"
        )

    try:
        idinfo = id_token.verify_oauth2_token(
            login_data.id_token, requests.Request(), settings.GOOGLE_CLIENT_ID, clock_skew_in_seconds=10
        )
        if not idinfo.get("email_verified"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email Google chưa được xác thực."
            )
        email = idinfo.get("email")
    except (ValueError, GoogleAuthError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    ALLOWED_DOMAIN = "fsb.edu.vn"
    if not email or not email.endswith(f"@{ALLOWED_DOMAIN}"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Chỉ tài khoản Google có đuôi @{ALLOWED_DOMAIN} mới được phép đăng nhập."
        )

    stmt = (
        select(UserAccount)
        .where(UserAccount.login_email == email)
        .options(
            selectinload(UserAccount.employee),
            selectinload(UserAccount.role_assignments).selectinload(UserRoleAssignment.role)
        )
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tài khoản email này chưa được cấp quyền truy cập hệ thống."
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tài khoản hiện đang bị khóa."
        )
    if user.password_hash is None:
        raise HTTPException(status_code=403, detail="Tài khoản này sử dụng đăng nhập Microsoft.")

    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()

    access_token = create_access_token(subject=user.user_account_id)
    refresh_token = create_refresh_token(subject=user.user_account_id)

    return Token(access_token=access_token, refresh_token=refresh_token)


@router.get("/microsoft/config")
async def get_microsoft_config():
    """Only public SPA metadata; identity links are stored privately in the database."""
    config = microsoft_configuration()
    if config is None:
        return {"enabled": False}
    client_id, tenant_id = config
    return {"enabled": True, "client_id": client_id, "tenant_id": tenant_id}


@router.post("/login/microsoft", response_model=Token)
async def login_with_microsoft(login_data: MicrosoftLoginRequest, db: AsyncSession = Depends(get_db)):
    # JWKS network access and signature verification must not block the event loop.
    claims = await run_in_threadpool(verify_microsoft_token, login_data.id_token)
    try:
        user = await provision_microsoft_user(db, claims, login_data.local_password)
        user.last_login_at = datetime.now(timezone.utc)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Tài khoản vừa được cập nhật. Vui lòng đăng nhập lại.") from None
    return Token(
        access_token=create_access_token(subject=user.user_account_id),
        refresh_token=create_refresh_token(subject=user.user_account_id),
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(current_user: UserAccount = Depends(get_session_user)):
    """Return profile and active roles of current authenticated user"""
    roles = [ra.role.role_code for ra in current_user.role_assignments]
    return UserResponse(
        user_account_id=current_user.user_account_id,
        employee_id=current_user.employee_id,
        login_email=current_user.login_email,
        is_active=current_user.is_active,
        roles=roles,
        onboarding_required=current_user.employee.employment_status == "PENDING",
    )
