from datetime import date, datetime
from typing import Optional, List
from decimal import Decimal
from sqlalchemy import BigInteger, Integer, String, Boolean, Date, DateTime, ForeignKey, CheckConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from app.core.database import Base


class Department(Base):
    __tablename__ = "hr_departments"

    department_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    department_code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    department_name: Mapped[str] = mapped_column(String(150), nullable=False)
    manager_employee_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("hr_employees.employee_id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    employees: Mapped[List["Employee"]] = relationship(
        "Employee",
        back_populates="department",
        foreign_keys="[Employee.department_id]"
    )


class Position(Base):
    __tablename__ = "hr_positions"

    position_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    position_code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    position_name: Mapped[str] = mapped_column(String(150), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    employees: Mapped[List["Employee"]] = relationship("Employee", back_populates="position")


class Employee(Base):
    __tablename__ = "hr_employees"

    employee_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False, index=True)
    phone_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    department_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("hr_departments.department_id"), nullable=True)
    position_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("hr_positions.position_id"), nullable=True)
    manager_employee_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("hr_employees.employee_id"), nullable=True)
    employment_status: Mapped[str] = mapped_column(String(20), default="ACTIVE", nullable=False)  # ACTIVE, INACTIVE, TERMINATED
    hire_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    termination_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # [FIX #7] Number of tax dependents for PIT deduction (4,400,000 VND per dependent).
    # Requires DB migration: alembic revision --autogenerate -m "add_dependent_count"
    dependent_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (CheckConstraint(
        "employment_status <> 'ACTIVE' OR (department_id IS NOT NULL AND position_id IS NOT NULL AND hire_date IS NOT NULL)",
        name="ck_hr_employee_active_profile",
    ), Index("uq_hr_employee_email_ci", func.lower(email), unique=True))

    # Relationships
    department: Mapped["Department"] = relationship("Department", back_populates="employees", foreign_keys=[department_id])
    position: Mapped["Position"] = relationship("Position", back_populates="employees")
    manager: Mapped[Optional["Employee"]] = relationship("Employee", remote_side=[employee_id], foreign_keys=[manager_employee_id])
    user_account: Mapped[Optional["UserAccount"]] = relationship("UserAccount", back_populates="employee", uselist=False)
    qr_cards: Mapped[List["QRCard"]] = relationship("QRCard", back_populates="employee", foreign_keys="[QRCard.employee_id]")
    attendance_events: Mapped[List["AttendanceEvent"]] = relationship("AttendanceEvent", back_populates="employee")
    attendance_days: Mapped[List["AttendanceDay"]] = relationship("AttendanceDay", back_populates="employee")
    attendance_fixes: Mapped[List["AttendanceFix"]] = relationship("AttendanceFix", back_populates="employee", foreign_keys="[AttendanceFix.employee_id]")
    leave_balances: Mapped[List["EmployeeLeaveBalance"]] = relationship("EmployeeLeaveBalance", back_populates="employee")
    leave_requests: Mapped[List["LeaveRequest"]] = relationship("LeaveRequest", back_populates="employee", foreign_keys="[LeaveRequest.employee_id]")
    compensations: Mapped[List["EmployeeCompensation"]] = relationship("EmployeeCompensation", back_populates="employee")
    payroll_lines: Mapped[List["PayrollLine"]] = relationship("PayrollLine", back_populates="employee")

    @property
    def microsoft_only(self) -> bool:
        from sqlalchemy import inspect
        return "user_account" in inspect(self).dict and self.user_account is not None and self.user_account.password_hash is None

    @property
    def roles(self) -> List[str]:
        """Return list of role codes assigned to this employee's user account without triggering lazy load."""
        from sqlalchemy import inspect
        state = inspect(self)
        if "user_account" in state.dict and self.user_account:
            ua_state = inspect(self.user_account)
            if "role_assignments" in ua_state.dict and self.user_account.role_assignments:
                return [
                    ra.role.role_code
                    for ra in self.user_account.role_assignments
                    if "role" in inspect(ra).dict and ra.role and getattr(ra.role, "role_code", None)
                ]
        return []

    @property
    def gross_salary(self) -> Optional[Decimal]:
        """Return current base monthly gross salary without triggering lazy load."""
        from sqlalchemy import inspect
        state = inspect(self)
        if "compensations" in state.dict and self.compensations:
            active = sorted(self.compensations, key=lambda c: c.effective_from, reverse=True)
            return active[0].base_monthly_salary
        return None
