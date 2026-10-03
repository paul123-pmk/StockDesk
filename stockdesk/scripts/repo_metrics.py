"""Describe tracked files honestly; these are NOT licensing-eligible LOC counts."""

import argparse
import json
import subprocess
from pathlib import Path


def output(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout


def metrics(root: Path) -> dict:
    groups = {}
    names = [name for name in output(root, "ls-files", "-z").split("\0") if name]
    for name in names:
        path = root/name
        content = path.read_bytes()
        try:
            lines = content.decode("utf-8").splitlines()
        except UnicodeDecodeError:
            category = "binary"
            lines = []
        else:
            category = ("application" if name.startswith("stockdesk/") else
                        "tests" if name.startswith("tests/") else
                        "tooling" if name.startswith("scripts/") else
                        "documentation" if path.suffix == ".md" else "configuration")
        record = groups.setdefault(category, {"files": 0, "physical_lines": 0,
                                               "nonblank_lines": 0, "bytes": 0})
        record["files"] += 1
        record["physical_lines"] += len(lines)
        record["nonblank_lines"] += sum(bool(line.strip()) for line in lines)
        record["bytes"] += len(content)
    dates = output(root, "log", "--all", "--format=%aI").splitlines()
    return {"tracked_files": len(names), "groups": groups,
            "total_physical_lines": sum(group["physical_lines"] for group in groups.values()),
            "total_nonblank_lines": sum(group["nonblank_lines"] for group in groups.values()),
            "commit_count": int(output(root, "rev-list", "--all", "--count").strip()),
            "earliest_author_timestamp": min(dates) if dates else None,
            "latest_author_timestamp": max(dates) if dates else None,
            "head": output(root, "rev-parse", "HEAD").strip(),
            "method": "UTF-8 tracked-file physical lines including comments; no claim these are eligible own-code LOC.",
            "authorship": "Initial implementation approximately 100% AI-generated; see AI_ASSISTANCE.md.",
            "licensing_solicitation_status": "Does not meet supplied human-authorship, historical-project or size requirements."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    print(json.dumps(metrics(args.repository.resolve()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
