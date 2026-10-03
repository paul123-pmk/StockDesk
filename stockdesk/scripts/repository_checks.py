"""Limited signature/file-policy checks, NOT comprehensive secret or PII scanning.

Only rule names and file/blob references are printed. Matching values are never
included in output. Full-history mode scans each reachable blob once, including
content deleted from the present checkout. Git must be installed.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

SIGNATURES = {
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
    "github_token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{60,255})\b"),
    "aws_access_key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "stripe_live_key": re.compile(rb"\b(?:sk|rk)_live_[A-Za-z0-9]{16,255}\b"),
}


def scan_bytes(content: bytes, source: str) -> list[dict]:
    """Inspect bytes, including binary blobs, without logging matched content."""
    return [{"source": source, "rule": name} for name, pattern in SIGNATURES.items()
            if pattern.search(content)]


def file_policy(name: str) -> list[dict]:
    path = PurePosixPath(name.replace("\\", "/"))
    base = path.name.lower()
    forbidden = (
        (base.startswith(".env") and base != ".env.example")
        or base == "session.key"
        or base.endswith((".db", ".sqlite", ".sqlite3", ".pyc", ".bundle"))
        or any(part in {"node_modules", ".venv", "venv", "__pycache__", "instance"} for part in path.parts)
        or any(mark in base for mark in (".sqlite-wal", ".sqlite-shm", ".db-wal", ".db-shm"))
    )
    return [{"source": name, "rule": "private_or_generated_file"}] if forbidden else []


def git(repository: Path, *arguments: str) -> bytes:
    return subprocess.run(["git", "-C", str(repository), *arguments], check=True,
                          capture_output=True).stdout


def scan(repository: Path, *, include_history: bool = False) -> dict:
    repository = Path(repository).resolve()
    tracked = [name.decode("utf-8", errors="surrogateescape") for name in
               git(repository, "ls-files", "-z").split(b"\0") if name]
    findings = []
    for name in tracked:
        findings.extend(file_policy(name))
        target = repository / name
        if target.is_symlink():
            findings.append({"source": name, "rule": "symlink_requires_review"})
        elif target.is_file():
            findings.extend(scan_bytes(target.read_bytes(), name))
        else:
            findings.append({"source": name, "rule": "tracked_file_missing"})
    blobs = 0
    if include_history:
        objects = git(repository, "rev-list", "--objects", "--all", "--no-object-names")
        metadata = subprocess.run(
            ["git", "-C", str(repository), "cat-file", "--batch-check=%(objectname) %(objecttype)"],
            input=objects, check=True, capture_output=True).stdout
        blob_ids = [line.split()[0] for line in metadata.splitlines() if line.endswith(b" blob")]
        # A regular file avoids pipe-buffer deadlocks while Git emits larger blobs.
        import tempfile
        with tempfile.TemporaryFile() as batch:
            subprocess.run(["git", "-C", str(repository), "cat-file", "--batch"],
                           input=b"\n".join(blob_ids)+b"\n" if blob_ids else b"",
                           stdout=batch, stderr=subprocess.PIPE, check=True)
            batch.seek(0)
            for expected in blob_ids:
                header = batch.readline().split()
                if len(header) != 3 or header[0] != expected or header[1] != b"blob":
                    raise RuntimeError("Unexpected Git object stream; scan did not complete.")
                size = int(header[2])
                content = batch.read(size)
                if len(content) != size or batch.read(1) != b"\n":
                    raise RuntimeError("Truncated Git object stream; scan did not complete.")
                findings.extend(scan_bytes(content, "git:"+expected.decode("ascii")))
                blobs += 1
        names = git(repository, "log", "--all", "--format=", "--name-only", "-z")
        for raw in set(names.split(b"\0")):
            name = raw.strip(b"\n").decode("utf-8", errors="surrogateescape")
            for finding in file_policy(name):
                finding["source"] = "history-path:"+finding["source"]
                findings.append(finding)
    unique = {(item["source"], item["rule"]): item for item in findings}
    return {"scope": "limited signature and tracked-file policy check",
            "current_files": len(tracked), "history_blobs": blobs,
            "findings": list(unique.values()),
            "limits": "Not Gitleaks; not exhaustive credential, vulnerability, ownership or personal-data verification."}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--history", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        result = scan(arguments.repository, include_history=arguments.history)
        print(json.dumps(result, indent=2))
        return 1 if result["findings"] else 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Repository scan did not complete: {type(error).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
