# Security policy

Punctual stores employee names, emails and location data. Please report vulnerabilities privately through GitHub's "Report a vulnerability" (Security tab) instead of opening a public issue. Expect an answer within a few days.

Deployment notes that matter for security:
- Serve it over HTTPS only (browsers need it for GPS anyway); keep `PUNCTUAL_COOKIE_SECURE` at its default `true`.
- Remove `PUNCTUAL_BOOTSTRAP_ADMIN_PASSWORD` after the first login and change that password.
- Keep the Litestream replica bucket private; it holds a full copy of the database.
