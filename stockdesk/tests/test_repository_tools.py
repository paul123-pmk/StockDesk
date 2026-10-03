"""Repository checks use synthetic signatures, never functioning credentials."""
import importlib
import subprocess

import pytest


def tools():
    try:
        return importlib.import_module("scripts.repository_checks")
    except ModuleNotFoundError:
        pytest.fail("Repository scanner has not been implemented")


def test_signature_check_reports_rule_without_secret():
    synthetic = ("gh" + "p_" + "x" * 36).encode()
    findings = tools().scan_bytes(b"value = " + synthetic, "synthetic")
    assert len(findings) == 1
    assert findings[0]["rule"] == "github_token"
    assert synthetic.decode() not in str(findings)


def test_private_key_marker_detected():
    marker = b"-----BEGIN " + b"RSA PRIVATE KEY-----"
    assert tools().scan_bytes(marker, "synthetic")[0]["rule"] == "private_key"


def test_prohibited_runtime_files_and_safe_example():
    checker = tools().file_policy
    assert checker("instance/session.key")
    assert checker("private/test.sqlite-wal")
    assert checker("src/node_modules/dependency.js")
    assert not checker(".env.example")
    assert not checker("stockdesk/services/inventory.py")


def test_plain_project_text_has_no_signature_findings():
    assert tools().scan_bytes(b"FastAPI inventory operations", "source") == []


def test_scan_checks_deleted_files_in_history(tmp_path):
    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                              capture_output=True, text=True).stdout
    git("init", "-b", "main")
    git("config", "user.name", "Synthetic scanner test")
    git("config", "user.email", "scanner-test@example.invalid")
    target = tmp_path / "deleted.txt"
    target.write_text("gh" + "p_" + "x" * 36, encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "Synthetic signature in isolated test fixture")
    target.unlink()
    (tmp_path / "README.md").write_text("Safe text\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "Remove isolated synthetic fixture")
    assert tools().scan(tmp_path, include_history=False)["findings"] == []
    result = tools().scan(tmp_path, include_history=True)
    assert result["history_blobs"] >= 2
    assert any(f["rule"] == "github_token" and f["source"].startswith("git:")
               for f in result["findings"])
