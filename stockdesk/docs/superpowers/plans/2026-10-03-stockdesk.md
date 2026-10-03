# StockDesk Implementation Plan

> Execute inline in the current session. Read the design before changing scope.

**Goal:** Deliver a working inventory application with measured verification.
**Architecture:** Shared transactional services under HTML and JSON interfaces.
**Tech Stack:** Python 3.11+, FastAPI, SQLite, Jinja2, pytest, Uvicorn.
**Spec:** ../specs/2026-10-03-stockdesk-design.md

## Global constraints
Keep AI origin and actual history visible. Never pad lines or manufacture history.
Whole units only, maximum balance 1,000,000,000. Local SQLite, one organization.
Admin/operator/viewer roles; all writes authorized; stock writes idempotent.

## Review focus
Concurrent withdrawals must not oversell; test using separate connections.
Expired or revoked sessions must not remain authorized; test through HTTP.
A partial invalid PO receipt must not leave any balance or line updated.
A stock count must detect the ABA case via balance versions, not quantities alone.
An exported untrusted string must not become a spreadsheet formula.

## Task 1 — persistence, validation and accounts
Create db.py, errors.py, validation.py, migrations/001_initial.sql and
services/auth.py. Add tests for bounds, strict integer input, transaction
rollback, migration repeatability, password verification, lockout and last admin.
Run failing tests, implement, rerun complete available suite. Record evidence.

## Task 2 — catalog and inventory
Create services/catalog.py and inventory.py. Interfaces accept an open connection,
actor ID and named business values, and return plain dictionaries.
Test receipts, issues, transfers, duplicate keys, changed payloads, ledger
immutability, permission changes and two concurrent withdrawals. Implement and
run complete suite before committing this genuine development milestone.

## Task 3 — purchasing, counts and reporting
Create purchasing.py, counts.py and reports.py; use inventory posting primitives.
Test transitions, over-receipt, duplicate lines, multi-line rollback, stale counts,
reconciliation, CSV quoting and formula injection. Implement and verify.

## Task 4 — web application and CLI
Create app.py, api.py, web.py, security.py, configuration, templates and CSS.
Create CLI commands init, create-user, reset-password, demo, serve, backup, check.
Test login, CSRF, authorization, template escaping, all page renders and complete
HTML/API workflows. Run a real browser smoke test when available.

## Task 5 — documentation, CI and release verification
Write Windows/Linux setup, API guide, operational runbook and limitations.
Add cross-platform CI and maintainable issue/PR templates (not invented history).
Run tests with coverage, compilation, dependency checks that are available,
repository pattern scanning, metrics and bundle verification. Clone the bundle
into a separate directory and rerun the suite. Package only tracked source.
