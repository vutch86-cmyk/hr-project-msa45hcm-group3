"""Create pending employee profiles and persist Entra identities on first sign-in."""
from uuid import uuid4

from fastapi import HTTPException
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select, func, text
from sqlalchemy.orm import selectinload
from starlette.concurrency import run_in_threadpool

from app.core.security import verify_password
from app.models.auth import MicrosoftIdentity, UserAccount, Role, UserRoleAssignment
from app.models.organization import Employee


def identity_email(claims: dict) -> str:
    # Display/contact values only: never authorize access to an existing account by these claims.
    for field in ("preferred_username", "email"):
        try:
            return str(TypeAdapter(EmailStr).validate_python(claims.get(field))).lower()
        except ValidationError:
            pass
    raise HTTPException(status_code=400, detail="Microsoft chưa cung cấp địa chỉ email hợp lệ. Vui lòng liên hệ quản trị viên Entra.")


async def provision_microsoft_user(db, claims: dict, local_password: str | None = None) -> UserAccount:
    tenant, oid = claims["tid"], claims["oid"]
    # Serialize repeat first logins in PostgreSQL. DB unique constraints remain authoritative.
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
                         {"identity": f"microsoft:{tenant}:{oid}"})
    identity = (await db.execute(select(MicrosoftIdentity).where(
        MicrosoftIdentity.tenant_id == tenant, MicrosoftIdentity.object_id == oid,
    ))).scalar_one_or_none()
    if identity:
        user = (await db.execute(select(UserAccount).where(
            UserAccount.user_account_id == identity.user_account_id,
        ).options(selectinload(UserAccount.employee)).with_for_update())).scalar_one()
        _check_active(user)
        return user

    email = identity_email(claims)
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
                         {"identity": f"microsoft-email:{email}"})
    matches = (await db.execute(select(UserAccount).where(
        func.lower(UserAccount.login_email) == email,
    ).options(selectinload(UserAccount.employee)).with_for_update())).scalars().all()
    if len(matches) > 1:
        raise HTTPException(status_code=409, detail="Có nhiều tài khoản trùng email. Vui lòng liên hệ quản trị viên.")
    if matches:
        user = matches[0]
        _check_active(user)
        bound = (await db.execute(select(MicrosoftIdentity).where(
            MicrosoftIdentity.user_account_id == user.user_account_id,
        ))).scalar_one_or_none()
        if bound or not user.password_hash:
            raise HTTPException(status_code=409, detail="Tài khoản đã liên kết danh tính khác. Vui lòng liên hệ quản trị viên.")
        if not local_password:
            raise HTTPException(status_code=409, detail={
                "code": "MICROSOFT_LINK_REQUIRED",
                "message": "Email này đã có tài khoản HRMS. Nhập mật khẩu HRMS một lần để xác nhận liên kết Microsoft.",
            })
        if not await run_in_threadpool(verify_password, local_password, user.password_hash):
            raise HTTPException(status_code=401, detail="Mật khẩu HRMS không đúng. Chưa liên kết tài khoản.")
    else:
        employee = (await db.execute(select(Employee).where(func.lower(Employee.email) == email))).scalars().first()
        if employee:
            # Avoid attaching an existing personnel record on mutable email evidence alone.
            raise HTTPException(status_code=409, detail="Email đã có hồ sơ nhân viên. Nhờ ADMIN tạo tài khoản local cho hồ sơ đó rồi xác nhận liên kết bằng mật khẩu.")
        role = (await db.execute(select(Role).where(Role.role_code == "EMPLOYEE"))).scalar_one_or_none()
        if not role:
            raise HTTPException(status_code=503, detail="Hệ thống chưa khởi tạo vai trò EMPLOYEE.")
        employee = Employee(
            employee_code="MS-" + uuid4().hex[:24],
            full_name=str(claims.get("name") or email)[:200], email=email,
            employment_status="PENDING", department_id=None, position_id=None, hire_date=None,
        )
        db.add(employee)
        await db.flush()
        user = UserAccount(employee_id=employee.employee_id, login_email=email,
                           password_hash=None, is_active=True)
        db.add(user)
        await db.flush()
        db.add(UserRoleAssignment(user_account_id=user.user_account_id, role_id=role.role_id))
    db.add(MicrosoftIdentity(tenant_id=tenant, object_id=oid, user_account_id=user.user_account_id))
    await db.flush()
    return user


def _check_active(user):
    if not user.is_active or user.employee.employment_status not in {"ACTIVE", "PENDING"}:
        raise HTTPException(status_code=403, detail="Tài khoản HRMS đang bị khóa hoặc nhân viên đã nghỉ việc.")
