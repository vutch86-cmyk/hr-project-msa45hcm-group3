"""Persist Entra identities and support pending Microsoft employee profiles."""
from alembic import op
import sqlalchemy as sa

revision = "5f6a7b8c9d04"
down_revision = "4e5f6a7b8c93"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("uq_hr_user_login_email_ci", "hr_user_accounts", [sa.text("lower(login_email)")], unique=True)
    op.create_index("uq_hr_employee_email_ci", "hr_employees", [sa.text("lower(email)")], unique=True)
    op.alter_column("hr_user_accounts", "password_hash", existing_type=sa.String(255), nullable=True)
    for column, type_ in (("department_id", sa.BigInteger()), ("position_id", sa.BigInteger()), ("hire_date", sa.Date())):
        op.alter_column("hr_employees", column, existing_type=type_, nullable=True)
    op.create_check_constraint("ck_hr_employee_active_profile", "hr_employees",
        "employment_status <> 'ACTIVE' OR (department_id IS NOT NULL AND position_id IS NOT NULL AND hire_date IS NOT NULL)")
    op.create_table("hr_microsoft_identities",
        sa.Column("microsoft_identity_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("object_id", sa.String(36), nullable=False),
        sa.Column("user_account_id", sa.BigInteger(), sa.ForeignKey("hr_user_accounts.user_account_id"), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "object_id", name="uq_hr_microsoft_identity"),
    )


def downgrade():
    # Do not delete employee identities or invent missing personnel data on rollback.
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM hr_user_accounts WHERE password_hash IS NULL LIMIT 1")).first() or bind.execute(sa.text(
        "SELECT 1 FROM hr_employees WHERE department_id IS NULL OR position_id IS NULL OR hire_date IS NULL LIMIT 1")).first():
        raise RuntimeError("Complete pending profiles and resolve Microsoft-only accounts before downgrading.")
    if bind.execute(sa.text("SELECT 1 FROM hr_microsoft_identities LIMIT 1")).first():
        raise RuntimeError("Export and resolve Microsoft identity links before downgrading.")
    op.drop_table("hr_microsoft_identities")
    op.drop_index("uq_hr_user_login_email_ci", table_name="hr_user_accounts")
    op.drop_index("uq_hr_employee_email_ci", table_name="hr_employees")
    op.drop_constraint("ck_hr_employee_active_profile", "hr_employees", type_="check")
    for column, type_ in (("department_id", sa.BigInteger()), ("position_id", sa.BigInteger()), ("hire_date", sa.Date())):
        op.alter_column("hr_employees", column, existing_type=type_, nullable=False)
    op.alter_column("hr_user_accounts", "password_hash", existing_type=sa.String(255), nullable=False)
