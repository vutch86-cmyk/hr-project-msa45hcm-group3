# Microsoft login on azure-demo

## Behavior

Microsoft appears alongside password and Google when runtime configuration is valid. MSAL Browser v4 uses a popup and authorization code flow with PKCE. The backend validates RS256 signatures, expiry, issuer, audience, tenant and required identity claims before issuing the existing HRMS JWTs. Roles come from HRMS. Users are never automatically created or linked by email claims.

This version supports one Microsoft Entra workforce tenant. Invite external users as guests into that tenant and use their guest Object ID in this tenant. Personal accounts outside that tenant are not accepted. No client secret, Graph permissions, database migration or frontend build variables are needed.

## 1. App Registration

1. Azure Portal → Microsoft Entra ID → App registrations → New registration.
2. Name: `HRMS Azure Demo`; account type: **Accounts in this organizational directory only**.
3. Authentication → Add a platform → **Single-page application (SPA)**.
4. Register the exact redirect URI `https://hrms-azure.vuta.it.com/microsoft-callback.html`. Add the custom domain URI too if used. Local Vite: `http://localhost:5173/microsoft-callback.html`.
5. Copy Application (client) ID and Directory (tenant) ID from Overview. Do not create a client secret or enable implicit grant.
6. If tenant policy requires admin consent or user assignment, obtain those from the tenant administrator.

## 2. Bind existing accounts

Entra ID → Users → select user → copy **Object ID**. Map it to the exact existing `hr_user_accounts.login_email`:

```json
{"33333333-3333-4333-8333-333333333333":"employee@hrgroup3.com"}
```

Only administrators should edit this mapping. Do not populate it from user-submitted email or token claims. Unmapped users and inactive HRMS accounts are denied. Remove a binding to prevent future Microsoft logins; disable the HRMS account to block access through existing HRMS sessions too.

## 3. Azure Container App variables

Application → Containers → Edit and deploy → Environment variables:

| Name | Value |
| --- | --- |
| `MICROSOFT_CLIENT_ID` | Application (client) ID |
| `MICROSOFT_TENANT_ID` | Directory (tenant) ID as UUID; not common/organizations |
| `MICROSOFT_ACCOUNT_BINDINGS` | JSON object from step 2 |

Local development: put the same settings in `backend/.env`. The public configuration endpoint exposes only enabled/client_id/tenant_id. Invalid or empty IDs hide the button; an empty binding map denies every Microsoft user.

## 4. Deploy after review

After approval, commit/push to `azure-demo`. Actions → **Azure Demo - Branch** → Run workflow → select `azure-demo`. Set the new Container App revision image to `ghcr.io/<owner>/hrms-azure-demo:<commit-sha>` and configure the variables above. The current workflow is manual; pushing code alone does not deploy it.

## 5. Verify and troubleshoot

- `/api/v1/auth/microsoft/config`: returns public IDs, no bindings.
- `/login`: Microsoft opens account chooser and linked user reaches the normal dashboard.
- `/api/v1/auth/me`: intended user and existing roles.
- Test unlinked user, disabled HRMS user, cancelled popup, password and Google regressions.
- AADSTS50011: exact redirect URI mismatch.
- AADSTS9002326: callback must be registered as SPA, not Web.
- Popup blocked: allow popups on the application domain.
- HTTP 403: check tenant-specific Object ID, binding and active HRMS account.
- HTTP 503: check configuration and backend HTTPS egress to login.microsoftonline.com.

Logout ends the HRMS session; it does not sign out of other Microsoft applications. The next Microsoft login shows an account chooser.

Rollback: return traffic to the previous Container App revision. Disable only Microsoft by clearing MICROSOFT_CLIENT_ID and deploying a new revision. No database rollback is required.

## References

- https://learn.microsoft.com/en-us/entra/msal/javascript/browser/initialization
- https://learn.microsoft.com/en-us/entra/identity-platform/claims-validation
- https://learn.microsoft.com/en-us/entra/identity-platform/id-token-claims-reference
