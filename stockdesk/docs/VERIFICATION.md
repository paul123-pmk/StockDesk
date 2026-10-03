# Verification record — initial delivery

Executed on 3 October 2026 using Linux and Python 3.13.5.

## Executed checks

- `python -m pytest --cov=stockdesk --cov-report=term-missing:skip-covered`:
  **184 tests passed**. Combined statement-and-branch coverage: **90.31%**;
  configured minimum: 85%. No coverage exclusion was added to inflate this result.
- Real temporary SQLite tests include concurrency, transaction rollback,
  idempotent receipts, over-receipt rejection, stale-count/ABA rejection, and
  snapshot-consistent multi-page export during a concurrent catalog change.
- HTTP tests execute HTML forms and JSON workflows through FastAPI TestClient,
  including password/login policy, CSRF, revoked sessions, read-only permissions,
  request-size bounds, escaping and the last-administrator rule.
- Backup tests verify a restored copy and reject overwriting an existing backup.
  CLI tests execute initialization, account setup/reset, fictional demo loading,
  and integrity checks.
- `python -m compileall -q stockdesk scripts`: passed.
- A wheel built locally with `pip wheel --no-deps --no-build-isolation`. It was
  installed into a separate target directory; imports, SQL migration, login
  template, static CSS and health endpoint passed there.
- Chromium 144.0.7559.96 rendered real server-generated login/dashboard HTML
  offline, with the same CSS inlined and network requests disabled. Desktop
  1440×1080 and mobile 390×844 renders had no document-width overflow. These are
  visual checks, **not a real-browser end-to-end workflow test**.

The final external delivery report additionally records current-history scanning,
Git object/bundle checks, clone verification, line/file metrics and checksums.
Those values are measured after this file is committed rather than invented here.

## Checks not completed in this environment

- Chromium navigation to the loopback application was blocked by an administrator
  policy (`ERR_BLOCKED_BY_ADMINISTRATOR`). The optional browser smoke script is
  supplied for another environment but its full workflow did not pass here.
- Gitleaks could not be installed/downloaded. The included repository check is
  limited to a few token signatures and unsafe filenames; it is not a replacement
  for Gitleaks or manual secret/personal-data review.
- No current dependency-vulnerability database audit was run. Dependencies are
  pinned to versions available for testing, not certified vulnerability-free.
- A fresh dependency download/install was not verified because package-index
  network access was unavailable. The wheel build/install test reused installed
  dependency versions and used `--no-deps`.
- Windows/macOS execution, Python 3.11/3.12 execution, public HTTPS deployment,
  hosted GitHub Actions, independent human security review and production load
  tests were not performed. Configuration is not evidence of a hosted run.

No real customer data, functioning shipped password, generated customer usage,
fabricated PR/issue history, or backdated commit was needed for these checks.
Test success does not satisfy the supplied licensing program's human-authorship,
project-history, ownership or size requirements.
