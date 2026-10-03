# Security notes

StockDesk is an initial local-first release, not penetration-tested software.
Do not store production data until the deployment and code have been reviewed.

## Controls implemented and behavior tested

- Passwords use scrypt with N=16384, r=8, p=5, a fresh 16-byte random salt and a
  32-byte derived key. Parameters are recorded in the hash and verified against
  the supported configuration before computation. No default password ships.
  This configuration is one of the OWASP-listed scrypt trade-offs (reference
  below); that is not an endorsement or audit of this application.
- Failed sign-ins have generic error text. Five failures per normalized
  username within five minutes trigger a five-minute block stored in SQLite.
  This is not a substitute for upstream connection/IP rate limiting; an attacker
  can target many usernames or deliberately lock an account.
- Signed session cookies contain an account identifier, session version, issue
  time and CSRF token, not a password. Signed does not mean encrypted. Cookies
  are HttpOnly and SameSite=Lax, with Secure enabled in production mode.
- Sessions have an eight-hour absolute lifetime. Password and access changes
  revoke older sessions. Signing out revokes ALL sessions for that account,
  including a copied earlier cookie, then clears the current browser cookie.
- Every write checks a CSRF token. Browser forms reject duplicate fields. Login
  also needs CSRF protection; its successful completion rotates the token.
- Role and enabled status are reloaded from SQLite for authenticated requests.
  The last enabled administrator cannot be disabled or demoted.
- Strict field, range and size checks precede business updates. HTML is escaped
  by Jinja; SQL values are bound parameters. Table-name interpolation is limited
  to explicit allowlists. CSV string cells with formula-like prefixes are
  neutralized. Inventory exports use a consistent read snapshot.
- Request bodies are capped at 256 KiB, including streamed bodies. Hostnames
  must be explicitly allowed. Default headers deny framing, inline scripts,
  third-party scripts/styles and caching of operational responses.
- Ledger, operation and audit tables reject row update/deletion using triggers.
  This deters accidental application edits, NOT someone who controls the
  database file and can alter its schema. There is no cryptographic audit seal.

## Deployment boundary

The CLI and anyone who can read or write the database are trusted administrators.
Local CLI account recovery intentionally bypasses web login. Do not expose shell
access to untrusted users. Restrict database, backups and session-key permissions
with operating-system controls. POSIX creation modes do not establish Windows
ACL policy; configure and review Windows permissions separately.

The default server binds to 127.0.0.1 with proxy-header trust disabled. Production
mode requires an external session secret and explicit trusted hosts. It sets
Secure cookies and HSTS; it does not provision TLS or a reverse proxy. Do not
turn off these controls merely to make a public HTTP deployment work.

No password-reset emails, MFA, per-IP distributed limiter, antivirus upload
pipeline, SSO or remote incident-reporting service is provided. No file upload
feature is needed for the inventory workflows. No arbitrary external redirects
are accepted by form actions.

## Release checks and their limits

The included repository scanner detects a small set of high-confidence token
signatures and unsafe tracked filenames in current files and reachable history.
It is intentionally labeled limited. It does not recognize every credential,
all personal information, or policy problems in arbitrary text. Do not equate
its passing output with the solicitation's full-history security check.

Before a real deployment or licensing review, run a maintained secret scanner
and review dependencies with access to a current vulnerability database:

```sh
gitleaks git --log-opts="--all" --redact .
gitleaks dir --redact .
python -m pip_audit -r requirements.txt
```

Install those tools separately from their official distributions. Review and
resolve findings; do not merely suppress them. If a real secret ever enters Git,
revoke it first, then coordinate its removal from history. Preserve a truthful
record of security remediation instead of disguising the age of the project.

Report suspected vulnerabilities privately to the actual repository owner.
No fictional security team, reporting email address or response commitment is
provided in this new project.

Reference checked during development:
https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
