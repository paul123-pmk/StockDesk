# Contributing

Start with the README, API guide and operations runbook. The initial version is
AI-generated; preserve that fact when adding your own work. Record AI assistance
for later changes rather than claiming a human-only history.

Create a branch for a real change. Write a behavior test that fails without the
change, implement it, and run the entire suite with coverage. Prefer a small
service with a clear transaction boundary over duplicated HTML/API logic. Use
bound SQL values and strict validation. Do not weaken concurrency, authorization,
stock-count versioning or idempotency tests to make an implementation pass.

Use `python -m pytest --cov=stockdesk --cov-report=term-missing` and the repository
checks before opening a real pull request. Record what you actually ran, on
which operating system, and any unavailable checks. Coverage is not evidence of
absence of defects; include regression tests for the behavior being changed.

Keep commits attributable and current. Do not pad lines, import unrelated code,
synthesize contributor identities, create decorative issue histories, or change
authorship/date metadata to meet a licensing threshold. Never commit runtime
databases, real secrets, customer information or private documents.

No automated merge, publish or licensing workflow is configured. The issue and
pull-request files supplied under `.github/` are templates for future real work,
not evidence that any such work has already happened.
