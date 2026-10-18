import pytest
from datetime import date
from decimal import Decimal
from httpx import AsyncClient, ASGITransport
import secrets
from sqlalchemy import select

from app.main import app
from app.core.database import AsyncSessionLocal
from app.models.organization import Employee, Department, Position
from app.models.auth import Role, UserAccount, UserRoleAssignment
from app.models.payroll import EmployeeCompensation, PayrollPeriod, PayrollLine
from app.schemas.organization import EmployeeCreate, EmployeeUpdate, EmployeeResponse
from app.schemas.payroll import PayrollLineResponse


def test_employee_schemas_gross_salary():
    payload = EmployeeCreate(
        employee_code="TEST_GS_01",
        full_name="Nguyễn Văn A",
        email="nva@test.com",
        department_id=1,
        position_id=1,
        hire_date=date(2026, 1, 1),
        gross_salary=Decimal("20000000.00"),
    )
    assert payload.gross_salary == Decimal("20000000.00")

    update_payload = EmployeeUpdate(gross_salary=Decimal("25000000.00"))
    assert update_payload.gross_salary == Decimal("25000000.00")


def test_payroll_line_response_schema():
    line_data = {
        "payroll_line_id": 1,
        "payroll_period_id": 1,
        "employee_id": 10,
        "employee_code": "EMP010",
        "full_name": "Trần Thị B",
        "department_name": "Phòng Kỹ thuật",
        "currency_code": "VND",
        "base_salary": Decimal("20000000.00"),
        "standard_work_days": Decimal("22.0"),
        "actual_work_days": Decimal("22.0"),
        "allowance_amount": Decimal("0.00"),
        "overtime_amount": Decimal("0.00"),
        "deduction_amount": Decimal("0.00"),
        "gross_salary": Decimal("20000000.00"),
        "insurance_deduction": Decimal("2100000.00"),
        "taxable_income": Decimal("17900000.00"),
        "personal_income_tax": Decimal("690000.00"),
        "other_deductions": Decimal("0.00"),
        "net_salary": Decimal("17210000.00"),
        "calculated_at": "2026-02-28T17:00:00Z",
    }
    resp = PayrollLineResponse.model_validate(line_data)
    assert resp.employee_code == "EMP010"
    assert resp.full_name == "Trần Thị B"
    assert resp.department_name == "Phòng Kỹ thuật"


@pytest.mark.asyncio
async def test_create_and_update_employee_gross_salary_api():
    async with AsyncSessionLocal() as db:
        # Find admin account for authorization
        admin_account = (
            await db.execute(
                select(UserAccount)
                .join(UserRoleAssignment)
                .join(Role)
                .where(Role.role_code == "ADMIN", UserAccount.is_active.is_(True))
            )
        ).scalars().first()
        assert admin_account is not None

        dept = (await db.execute(select(Department))).scalars().first()
        pos = (await db.execute(select(Position))).scalars().first()
        assert dept is not None
        assert pos is not None

    from app.core.security import create_access_token
    token = create_access_token(admin_account.user_account_id)
    headers = {"Authorization": f"Bearer {token}"}

    test_code = f"GS_{secrets.token_hex(4)}"
    test_email = f"{test_code.lower()}@test.com"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Create employee with gross_salary
        create_payload = {
            "employee_code": test_code,
            "full_name": "Lương Gross Test",
            "email": test_email,
            "department_id": dept.department_id,
            "position_id": pos.position_id,
            "hire_date": "2026-01-15",
            "gross_salary": "22000000.00",
            "role_code": "EMPLOYEE",
            "login_password": "Password@123",
        }
        res_create = await ac.post("/api/v1/employees", json=create_payload, headers=headers)
        assert res_create.status_code in {200, 201}, res_create.text
        data_create = res_create.json()
        emp_id = data_create["employee_id"]
        assert Decimal(str(data_create["gross_salary"])) == Decimal("22000000.00")

        # Verify DB compensation record was created
        async with AsyncSessionLocal() as db:
            comp = (
                await db.execute(
                    select(EmployeeCompensation).where(EmployeeCompensation.employee_id == emp_id)
                )
            ).scalars().first()
            assert comp is not None
            assert comp.base_monthly_salary == Decimal("22000000.00")

        # 2. Update employee gross_salary
        update_payload = {
            "gross_salary": "28500000.00",
        }
        res_update = await ac.patch(f"/api/v1/employees/{emp_id}", json=update_payload, headers=headers)
        assert res_update.status_code == 200, res_update.text
        data_update = res_update.json()
        assert Decimal(str(data_update["gross_salary"])) == Decimal("28500000.00")

        # Verify DB compensation record was updated
        async with AsyncSessionLocal() as db:
            comp = (
                await db.execute(
                    select(EmployeeCompensation).where(EmployeeCompensation.employee_id == emp_id)
                )
            ).scalars().first()
            assert comp is not None
            assert comp.base_monthly_salary == Decimal("28500000.00")

        # 3. Clean up
        async with AsyncSessionLocal() as db:
            from sqlalchemy import delete
            await db.execute(delete(EmployeeCompensation).where(EmployeeCompensation.employee_id == emp_id))
            await db.execute(delete(UserRoleAssignment).where(UserRoleAssignment.user_account_id == admin_account.user_account_id, UserRoleAssignment.user_account_id != admin_account.user_account_id))
            emp_obj = (await db.execute(select(Employee).where(Employee.employee_id == emp_id))).scalar_one_or_none()
            if emp_obj:
                acc = (await db.execute(select(UserAccount).where(UserAccount.employee_id == emp_id))).scalar_one_or_none()
                if acc:
                    await db.execute(delete(UserRoleAssignment).where(UserRoleAssignment.user_account_id == acc.user_account_id))
                    await db.execute(delete(UserAccount).where(UserAccount.user_account_id == acc.user_account_id))
                await db.execute(delete(Employee).where(Employee.employee_id == emp_id))
            await db.commit()


@pytest.mark.asyncio
async def test_get_payroll_lines_includes_employee_code_and_full_name():
    async with AsyncSessionLocal() as db:
        admin_account = (
            await db.execute(
                select(UserAccount)
                .join(UserRoleAssignment)
                .join(Role)
                .where(Role.role_code == "ADMIN", UserAccount.is_active.is_(True))
            )
        ).scalars().first()
        assert admin_account is not None

        # Check if any payroll period with lines exists, or create a mock line
        period = (
            await db.execute(
                select(PayrollPeriod).where(PayrollPeriod.lines.any())
            )
        ).scalars().first()

    from app.core.security import create_access_token
    token = create_access_token(admin_account.user_account_id)
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        if period:
            res = await ac.get(f"/api/v1/payroll/periods/{period.payroll_period_id}/lines", headers=headers)
            assert res.status_code == 200
            data = res.json()
            assert len(data) > 0
            for line in data:
                assert "employee_code" in line
                assert "full_name" in line
                assert line["employee_code"] is not None
                assert line["full_name"] is not None
