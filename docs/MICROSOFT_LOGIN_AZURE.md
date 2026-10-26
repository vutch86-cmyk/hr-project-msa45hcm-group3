# Microsoft Entra login and automatic employee provisioning on azure-demo

## Sign-in behavior

Microsoft Entra authenticates the user. HRMS verifies the signed v2 ID token against the configured client ID and tenant, then looks up the durable `(tenant_id, object_id)` identity in `hr_microsoft_identities`. No `MICROSOFT_ACCOUNT_BINDINGS` variable is used.

- Existing linked user: sign in to the same HRMS account, even if the Microsoft username changes. HRMS retains its own personnel/contact values; a username change does not create a second employee or overwrite HR edits.
- New user/email: create exactly one pending employee profile and a Microsoft-only account, with EMPLOYEE role. No local password, salary, department, position or hire date is invented.
- Existing local account with the same case-insensitive login email: show a one-time password confirmation. Only after the existing HRMS password is verified is the Microsoft identity linked. Existing employee records, roles, history and local login remain intact. Later Microsoft logins need no local password.
- Existing employee profile without a login: require ADMIN to create the login through that employee's edit form and confirm the link with its password. Never silently attach an existing personnel record based on mutable email claims.
- A different Microsoft identity claiming an already linked email is rejected. Inactive/terminated HRMS accounts are never reactivated by sign-in.
- Missing/invalid email for a new identity: show a configuration error without creating partial records. Already linked identities do not depend on email claims.

Email claims are contact/matching hints, not proof of ownership of an existing HRMS account. Never auto-merge local accounts solely by email. Tenant ID + Object ID uniquely identify the Microsoft principal. If two first logins race, PostgreSQL transaction advisory locks and unique constraints protect identity/account creation. The profile, account, role and identity are committed in one transaction.

## Pending employee workflow

Microsoft sign-in immediately creates an employee with status `PENDING`. The user can see their sign-in/profile and a pending screen, refresh status, or log out. All protected business APIs deny this pending user. HR cannot issue their QR card and kiosk scanning is blocked while pending. Payroll already selects only eligible ACTIVE/TERMINATED profiles with an actual hire date, so pending profiles are excluded.

HR/ADMIN opens Employees, finds **Chờ hoàn thiện**, edits the employee, supplies department, position and actual hire date, and selects **Đang làm việc**. Salary can be added in the same save. Both API validation and a database constraint prevent activating an incomplete profile. New Microsoft-only accounts cannot receive local passwords through HRMS reset/edit endpoints; their passwords remain managed in Entra.

This creates the HRMS profile on **first successful sign-in**, not when the Entra user is created. Entra groups, department/job attributes and account deactivation are not continuously synchronized. Disabling an Entra user blocks subsequent Microsoft authentication, but already-issued HRMS JWTs keep their current lifetime unless the HRMS account is also deactivated. This change does not implement Graph polling, SCIM or instant Entra session revocation.

## App Registration

1. Azure Portal → Microsoft Entra ID → App registrations → New registration.
2. Name: `HRMS Azure Demo Login`; account type: **Accounts in this organizational directory only**.
3. Authentication → Add platform → **Single-page application (SPA)**.
4. Register `https://hrms-azure.vuta.it.com/microsoft-callback.html`. Local Vite: `http://localhost:5173/microsoft-callback.html`.
5. Record Application (client) ID and Directory (tenant) ID. No client secret, implicit grant or Graph permission is required by this implementation. MSAL Browser uses authorization code with PKCE.
6. For a workforce directory containing guests/service/test users, configure Enterprise application → Properties → **Assignment required? Yes**, then assign intended users. Without assignment restriction, any user/guest who can authenticate in this tenant may create a pending HRMS profile. Assignment/tenant policy approval remains the administrator's responsibility.

This is a single workforce tenant implementation. Outside personal/work accounts must be invited into that tenant and use the identity issued in that tenant.

## Azure variables

| Name | Value |
| --- | --- |
| `MICROSOFT_CLIENT_ID` | Application (client) ID |
| `MICROSOFT_TENANT_ID` | Directory (tenant) ID UUID, not common/organizations |

Remove the old `MICROSOFT_ACCOUNT_BINDINGS` variable from Container App configuration. Do not commit user account mappings or passwords. Keep Container App Authentication set to **Allow unauthenticated access**; HRMS handles access control itself.

## Database migration and rollout

This release needs migration **5f6a7b8c9d04**, whose parent is **4e5f6a7b8c93**. It adds the identity table, permits null local password hashes and null draft personnel fields, and enforces complete ACTIVE employee profiles. It does not change existing roles or payroll records.

1. Take a recoverable backup/snapshot of the target Azure application's database. If GCP uses the same database, plan a coordinated release: older app versions cannot safely process new pending records/null password hashes.
2. Check for legacy email collisions before migration (resolve deliberately; do not auto-merge):

   ```sql
   SELECT lower(email), count(*) FROM hr_employees GROUP BY lower(email) HAVING count(*) > 1;
   SELECT lower(login_email), count(*) FROM hr_user_accounts GROUP BY lower(login_email) HAVING count(*) > 1;
   ```

   Both queries must return no rows. The new unique indexes enforce case-insensitive email uniqueness.

3. Use the reviewed new source/image and the target database configuration; run `alembic current` from `backend`. Confirm the expected revision chain. Do not blindly stamp the database or assume it is at this parent.
4. Run `alembic upgrade head` from `backend` using the new release's migration tooling, or an isolated migration job built from the new image. No migration automatically runs when the web container starts.
5. After approved commit/push, run the manual **Azure Demo - Branch** workflow on `azure-demo`. Deploy the image for that commit and route traffic to the new revision. Remove old application replicas that share this database before enabling first-time sign-ins.
6. Existing users previously linked only through the environment mapping must complete the one-time local password confirmation. The mapping is not imported implicitly, and no users are linked by email alone. If the previous mapping used different Microsoft/HRMS email addresses, arrange administrator-assisted data migration with verified identity ownership before rollout.
7. Verify new user, returning user, existing local user/link confirmation, wrong password, inactive account, pending API restrictions, HR activation and payroll exclusion.

The downgrade refuses to discard persisted Microsoft identities or guess missing personnel values. Do not roll back to an old image after pending/Microsoft-only records have been created. Prefer a forward fix; a full coordinated database restore requires a maintenance window and can lose changes since the snapshot.

## Validation

```bash
cd backend
pip install -r requirements-test.txt
python -m pytest tests/unit/test_microsoft_auth.py tests/unit/test_google_auth.py tests/integration/test_microsoft_jit.py -q
```

Integration tests use an isolated in-memory SQLite database, actual SQLAlchemy models, local password verification and API calls. Microsoft token verification tests use locally signed RSA tokens. PostgreSQL migration/advisory locks and real Entra popup login still need environment verification; SQLite does not validate those PostgreSQL-specific operations.

Frontend: `cd frontend && npm test && npm run build`. Tests and a frontend build do not replace real Entra login and UI review.

## References

- https://learn.microsoft.com/en-us/entra/msal/javascript/browser/initialization
- https://learn.microsoft.com/en-us/entra/identity-platform/claims-validation
- https://learn.microsoft.com/en-us/entra/identity-platform/id-token-claims-reference
- https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/application-properties
