"""Persistence/API tests on isolated SQLite; no production DB or Microsoft calls.

Install aiosqlite as a test dependency. PostgreSQL advisory-lock and migration
behavior are separately documented for deployment verification.
"""
from datetime import date
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import BigInteger, select, func, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.ext.compiler import compiles

from app.core.database import Base, get_db
from app.core.security import get_password_hash
from app.models.auth import MicrosoftIdentity, UserAccount, Role, UserRoleAssignment
from app.models.organization import Employee, Department, Position
from app.models.payroll import EmployeeCompensation
from app.services.microsoft_provisioning import provision_microsoft_user
from app.api.v1.endpoints import auth, employees
from app.api.deps import get_current_user
from app.schemas.organization import EmployeeUpdate

TENANT = '22222222-2222-4222-8222-222222222222'
OID = '33333333-3333-4333-8333-333333333333'
CLAIMS = dict(tid=TENANT, oid=OID, preferred_username='new@example.com', name='New Employee')

@compiles(BigInteger, 'sqlite')
def sqlite_bigint(element, compiler, **kw):
    return 'INTEGER'

@pytest_asyncio.fixture
async def db():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    @event.listens_for(engine.sync_engine, 'connect')
    def enforce_fks(connection, record):
        connection.execute('PRAGMA foreign_keys=ON')
    tables = [Department.__table__, Position.__table__, Employee.__table__, Role.__table__,
              UserAccount.__table__, UserRoleAssignment.__table__, MicrosoftIdentity.__table__, EmployeeCompensation.__table__]
    async with engine.begin() as conn:
        await conn.run_sync(lambda bind: Base.metadata.create_all(bind, tables=tables))
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        session.add_all([Role(role_code='EMPLOYEE', role_name='Employee'), Role(role_code='ADMIN', role_name='Admin')])
        await session.commit()
        yield session
    await engine.dispose()

async def local_account(db, email='new@example.com', active=True):
    dept = Department(department_code='IT', department_name='IT')
    pos = Position(position_code='DEV', position_name='Developer')
    db.add_all([dept, pos])
    await db.flush()
    emp = Employee(employee_code='EMP001', email=email, full_name='Existing Employee',
                   department_id=dept.department_id, position_id=pos.position_id,
                   hire_date=date(2026, 1, 1), employment_status='ACTIVE')
    db.add(emp)
    await db.flush()
    user = UserAccount(employee_id=emp.employee_id, employee=emp, login_email=email,
                       password_hash=get_password_hash('CorrectPassword123'), is_active=active)
    db.add(user)
    await db.commit()
    return user

async def count(db, model):
    return await db.scalar(select(func.count()).select_from(model))

async def test_first_login_creates_one_pending_employee_and_identity(db):
    user = await provision_microsoft_user(db, CLAIMS)
    await db.commit()
    emp = await db.get(Employee, user.employee_id)
    assert emp.employment_status == 'PENDING'
    assert (emp.hire_date, emp.department_id, emp.position_id) == (None, None, None)
    assert user.password_hash is None and user.is_active
    assert await count(db, EmployeeCompensation) == 0
    roles = (await db.execute(select(Role.role_code).join(UserRoleAssignment).where(
        UserRoleAssignment.user_account_id == user.user_account_id))).scalars().all()
    assert roles == ['EMPLOYEE']
    # Durable ID lookup works even after username is changed/missing.
    again = await provision_microsoft_user(db, dict(CLAIMS, preferred_username='changed@example.com'))
    assert again.user_account_id == user.user_account_id
    again = await provision_microsoft_user(db, {'tid': TENANT, 'oid': OID})
    assert again.user_account_id == user.user_account_id
    assert await count(db, Employee) == await count(db, UserAccount) == await count(db, MicrosoftIdentity) == 1

async def test_existing_email_requires_password_then_links_without_duplicate(db):
    existing = await local_account(db, email='NEW@example.com')
    for password, expected in [(None, 409), ('wrong', 401)]:
        with pytest.raises(HTTPException) as error:
            await provision_microsoft_user(db, CLAIMS, password)
        assert error.value.status_code == expected
        assert await count(db, MicrosoftIdentity) == 0
    linked = await provision_microsoft_user(db, CLAIMS, 'CorrectPassword123')
    await db.commit()
    assert linked.user_account_id == existing.user_account_id
    assert await count(db, Employee) == 1
    assert (await provision_microsoft_user(db, CLAIMS)).user_account_id == existing.user_account_id

async def test_same_email_different_object_cannot_rebind(db):
    await local_account(db)
    await provision_microsoft_user(db, CLAIMS, 'CorrectPassword123')
    await db.commit()
    with pytest.raises(HTTPException) as error:
        await provision_microsoft_user(db, dict(CLAIMS, oid=str(uuid4())), 'CorrectPassword123')
    assert error.value.status_code == 409
    assert await count(db, MicrosoftIdentity) == 1

async def test_disabled_existing_account_cannot_be_reactivated_by_jit(db):
    await local_account(db, active=False)
    with pytest.raises(HTTPException) as error:
        await provision_microsoft_user(db, CLAIMS, 'CorrectPassword123')
    assert error.value.status_code == 403
    assert await count(db, MicrosoftIdentity) == 0

async def test_existing_profile_without_login_is_not_attached_by_email(db):
    user = await local_account(db)
    await db.delete(user)
    await db.commit()
    with pytest.raises(HTTPException) as error:
        await provision_microsoft_user(db, CLAIMS)
    assert error.value.status_code == 409
    assert await count(db, Employee) == 1
    assert await count(db, UserAccount) == 0

async def test_new_user_invalid_email_does_not_create_partial_data(db):
    with pytest.raises(HTTPException) as error:
        await provision_microsoft_user(db, dict(CLAIMS, preferred_username='not-an-email'))
    assert error.value.status_code == 400
    assert await count(db, Employee) == 0

async def test_pending_api_profile_and_business_guard(db, monkeypatch):
    monkeypatch.setattr(auth, 'verify_microsoft_token', lambda token: CLAIMS)
    app = FastAPI()
    app.include_router(auth.router, prefix='/auth')
    app.include_router(employees.router, prefix='/employees')
    app.dependency_overrides[get_db] = lambda: db
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/auth/login/microsoft', json={'id_token': 'provider-verified'})
        assert response.status_code == 200
        headers = {'Authorization': 'Bearer ' + response.json()['access_token']}
        profile = await client.get('/auth/me', headers=headers)
        assert profile.status_code == 200 and profile.json()['onboarding_required']
        assert profile.json()['roles'] == ['EMPLOYEE']
        assert (await client.get('/employees', headers=headers)).status_code == 403
        # A local password cannot sign in to a Microsoft-only account.
        assert (await client.post('/auth/login', json={
            'login_email': CLAIMS['preferred_username'], 'password': 'any-password',
        })).status_code == 401

async def test_activate_requires_real_profile_and_enables_business_access(db):
    user = await provision_microsoft_user(db, CLAIMS)
    await db.commit()
    # Separate admin actor, not a privilege derived from Microsoft name/claims.
    from types import SimpleNamespace as NS
    admin = NS(user_account_id=user.user_account_id, role_assignments=[NS(role=NS(role_code='ADMIN'))])
    with pytest.raises(HTTPException) as error:
        await employees.update_employee(user.employee_id, EmployeeUpdate(employment_status='ACTIVE'), db, admin)
    assert error.value.status_code == 400
    dept = Department(department_code='IT', department_name='IT')
    pos = Position(position_code='DEV', position_name='Developer')
    db.add_all([dept, pos]); await db.flush()
    emp = await employees.update_employee(user.employee_id, EmployeeUpdate(
        employment_status='ACTIVE', department_id=dept.department_id, position_id=pos.position_id,
        hire_date=date(2026, 10, 1), gross_salary=10000000,
    ), db, admin)
    assert emp.employment_status == 'ACTIVE' and emp.microsoft_only
    assert (await get_current_user(user)).user_account_id == user.user_account_id
    comp = (await db.execute(select(EmployeeCompensation))).scalar_one()
    assert comp.effective_from == date(2026, 10, 1)
    # Entra-only accounts must not gain a local password through admin edits.
    with pytest.raises(HTTPException) as error:
        await employees.update_employee(user.employee_id, EmployeeUpdate(new_password='NewPassword123'), db, admin)
    assert error.value.status_code == 400

async def test_database_constraint_prevents_active_incomplete_profile(db):
    user = await provision_microsoft_user(db, CLAIMS)
    await db.commit()
    emp = await db.get(Employee, user.employee_id)
    emp.employment_status = 'ACTIVE'
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()

async def test_confirmed_link_preserves_existing_admin_role(db):
    user = await local_account(db)
    role = (await db.execute(select(Role).where(Role.role_code == 'ADMIN'))).scalar_one()
    db.add(UserRoleAssignment(user_account_id=user.user_account_id, role_id=role.role_id))
    await db.commit()
    await provision_microsoft_user(db, CLAIMS, 'CorrectPassword123')
    codes = (await db.execute(select(Role.role_code).join(UserRoleAssignment))).scalars().all()
    assert codes == ['ADMIN']

async def test_existing_identity_cannot_bypass_account_deactivation(db):
    user = await provision_microsoft_user(db, CLAIMS)
    await db.commit()
    user.is_active = False
    await db.commit()
    with pytest.raises(HTTPException) as error:
        await provision_microsoft_user(db, CLAIMS)
    assert error.value.status_code == 403
    assert await count(db, UserAccount) == 1

async def test_missing_role_failure_leaves_no_employee(db):
    from sqlalchemy import delete
    await db.execute(delete(Role).where(Role.role_code == 'EMPLOYEE'))
    await db.commit()
    with pytest.raises(HTTPException) as error:
        await provision_microsoft_user(db, CLAIMS)
    assert error.value.status_code == 503
    assert await count(db, Employee) == 0

async def test_pending_employee_cannot_receive_qr_card(db):
    from app.services.attendance_service import AttendanceService
    user = await provision_microsoft_user(db, CLAIMS)
    await db.commit()
    with pytest.raises(HTTPException) as error:
        await AttendanceService.issue_qr_card(db, user.employee_id, user.user_account_id)
    assert error.value.status_code == 403

async def test_email_case_changes_cannot_create_duplicate_employee(db):
    from sqlalchemy import insert
    await provision_microsoft_user(db, CLAIMS)
    await db.commit()
    with pytest.raises(IntegrityError):
        await db.execute(insert(Employee).values(
            employee_code='OTHER', full_name='Other', email='NEW@EXAMPLE.COM', employment_status='PENDING'))
    await db.rollback()
