# Operations runbook

## Operating model

Run commands from the repository root. `python` below means the Python inside
your project's virtual environment. Use `.\.venv\Scripts\python.exe` in Windows
PowerShell without environment activation.

Default runtime files are `instance/stockdesk.sqlite`, its WAL/SHM companions,
and `instance/session.key`. These are private runtime state and are not in the
source delivery. Keep the database on a local filesystem, not an SMB/NFS share.
SQLite serializes writers; this release is intended for a modest single-business
workload rather than a high-throughput, horizontally scaled service.

## Initial setup and accounts

```sh
python -m stockdesk init
python -m stockdesk create-user --username owner --role admin
python -m stockdesk create-user --username receiving --role operator
python -m stockdesk create-user --username observer --role viewer
```

Passwords are requested interactively, with confirmation, and must be 12–1024
characters. The first account must be an administrator. Never place passwords
in shell arguments, README examples, screenshots, CI variables printed to logs,
or committed fixtures. Use unique account names for real operators.

An on-host administrator can recover an account:

```sh
python -m stockdesk reset-password --username owner
```

Recovery revokes every older session for that user. Account access changes and
sign-out also revoke that account's existing sessions across browsers. There is
no single-device-only logout in this release.

## Configuration

Settings come from environment variables, NOT an automatically loaded `.env`:

| Variable | Default / requirement |
|---|---|
| STOCKDESK_ENV | `local`; set `production` only with a reviewed HTTPS deployment |
| STOCKDESK_DATABASE | `instance/stockdesk.sqlite` |
| STOCKDESK_SECRET_KEY | Local mode creates a random key beside the database; required externally in production |
| STOCKDESK_TRUSTED_HOSTS | `localhost,127.0.0.1`; production requires explicit comma-separated hosts |
| STOCKDESK_CURRENCY | `KES`; uppercase three-letter display label |

`--database PATH` is a global CLI option and must precede the subcommand:

```sh
python -m stockdesk --database /private/stockdesk/stockdesk.sqlite check
```

Do not commit custom runtime paths inside source folders. The filename
`session.key` is ignored anywhere in the tree, but you remain responsible for
safely locating private state. Database permissions and the containing directory
must deny access to unrelated local users. Review Windows ACLs explicitly.

For production, inject a strong random secret using the host's protected secret
configuration. The host list contains names only (no scheme, path or wildcard).
Production enables Secure cookies and HSTS; an HTTPS reverse proxy is required
but is not installed or configured by the application. The server does not trust
forwarded headers by default. Arrange edge rate limits, monitoring and reliable
backups before considering a public deployment. Never enable debug/reload there.

## Backup

```sh
python -m stockdesk check
python -m stockdesk backup /private/backups/stockdesk-2026-10-03.sqlite
```

Windows example:

```powershell
.\.venv\Scripts\python.exe -m stockdesk backup "D:\PrivateBackups\stockdesk-2026-10-03.sqlite"
```

Choose a NEW destination every time. Backup refuses to overwrite an existing
file, uses SQLite's backup API, and checks schema version, database integrity,
foreign keys and ledger reconciliation before returning success. The backup
contains operational records, account password hashes and audit events. Treat
it as sensitive even though it is not plaintext passwords. Encrypt and restrict
backups using your operating system or approved backup service; the application
does not provide backup encryption. Test restoration regularly.

## Restore

1. Stop every StockDesk server/process using the target database. Preserve the
   current database and its sidecars in a separate recovery location.
2. Run `python -m stockdesk --database PATH_TO_BACKUP check` against the backup.
   Only proceed after it returns an `ok: true` result. Work from a verified copy.
3. With the server stopped, place the verified database copy at the configured
   runtime path. Do not mix it with WAL/SHM files from the previous database;
   move those old sidecars to the recovery location before restarting.
4. Rotate the session secret so sessions from before restoration cannot remain
   valid. In local mode remove the OLD runtime `session.key` after preserving
   required recovery material; startup generates a new one. In production
   replace the injected secret using the deployment's secret mechanism.
5. Run `check` on the restored database, then start StockDesk and verify records
   through the UI. Retain recovery files until restoration is confirmed.

Never copy a live SQLite database with an ordinary file-copy operation as your
backup strategy: its current writes may be in the WAL. Use the backup command.

## Inventory rules operators should know

- Quantities are whole units. A balance must stay between zero and 1,000,000,000.
  Costs are nonnegative integer minor units, at most 1,000,000,000 per product.
- Receipt, issue and transfer each create an immutable operation. A transfer's
  two legs commit together or neither does. Do not correct errors by editing SQL.
  Record an authorized counter-movement with a clear note.
- Products with nonzero stock cannot be archived. An archived product on an
  existing open order must be reactivated before it can be received.
- A purchase order can have at most 500 distinct product lines. Drafts can be
  edited; submitted lines cannot. Receipts cannot exceed remaining quantities.
  An order with partial receipts cannot be cancelled by this release.
- Counts include active products, even those currently at zero, up to 500 lines.
  One draft count per warehouse is allowed. A stock change after count creation
  makes that count stale, even if subsequent movements return to the same quantity.
  Cancel it and start again; do not bypass the version check.
- Application-generated form keys prevent a stock operation from being applied
  twice. JSON clients must keep the same `Idempotency-Key` when retrying the same
  request. A reused key with a different business payload is a conflict.

## Troubleshooting

**Database missing:** run `init` using the same database path as `serve`.
**Host rejected:** use the configured hostname; do not replace the host allowlist
with `*`.
**Login works locally but not on public HTTP:** production cookies require HTTPS.
**Account temporarily blocked:** stop retrying and wait for the five-minute lock
window; investigate repeated failures rather than deleting rate-limit records.
**Database busy:** check for long-running competing writers or backup/admin
processes. A retryable storage problem returns 503; investigate sustained pressure.
**CSRF expired/revoked:** reload the sign-in or form page, authenticate again and
use its new token. Do not disable CSRF protection.
**Check fails:** stop writes, preserve a backup/copy and inspect the reported
issues. Do not delete ledger rows or suppress the reconciliation result.

`/healthz` only reports availability and schema version. It is not a full
integrity scan or a guarantee that all business data is valid. Schedule the
read-only `check` command through a scheduler you administer; no scheduler or
background job was installed by this delivery.
