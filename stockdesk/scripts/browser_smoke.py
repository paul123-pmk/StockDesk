"""Optional Chromium smoke test, using disposable synthetic data and credentials.

Requires playwright and a Chromium binary (or Playwright's installed browser).
Run from the repository root: python scripts/browser_smoke.py --output PATH
No production database is opened and no network host outside loopback is used.
"""

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    import httpx
    from playwright.sync_api import sync_playwright
    from stockdesk.db import connect, migrate
    from stockdesk.demo import seed_demo
    from stockdesk.services.auth import bootstrap_user

    with tempfile.TemporaryDirectory(prefix="stockdesk-browser-") as directory:
        database = Path(directory)/"smoke.sqlite"
        password = secrets.token_urlsafe(28)
        with closing(connect(database)) as connection:
            migrate(connection)
            owner = bootstrap_user(connection, "demo-owner", password)
            seed_demo(connection, owner["id"])
        with closing(socket.socket()) as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        environment = {**os.environ, "STOCKDESK_ENV": "local",
                       "STOCKDESK_DATABASE": str(database),
                       "STOCKDESK_SECRET_KEY": secrets.token_urlsafe(48),
                       "STOCKDESK_TRUSTED_HOSTS": "127.0.0.1,localhost"}
        process = subprocess.Popen([sys.executable, "-m", "stockdesk", "serve", "--port", str(port)],
                                   cwd=ROOT, env=environment, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(60):
                if process.poll() is not None:
                    raise RuntimeError("Smoke-test server exited before becoming ready.")
                try:
                    if httpx.get(base+"/healthz", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.2)
            else:
                raise RuntimeError("Smoke-test server did not become ready.")
            with sync_playwright() as playwright:
                executable = shutil.which("chromium") or shutil.which("chromium-browser")
                browser = playwright.chromium.launch(headless=True, executable_path=executable,
                                                     args=["--no-sandbox"] if os.name == "posix" else [])
                try:
                    context = browser.new_context(viewport={"width": 1440, "height": 1080})
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(base+"/login", wait_until="networkidle")
                    page.screenshot(path=str(output/"stockdesk-login.png"), full_page=True)
                    page.locator('input[name="username"]').fill("demo-owner")
                    page.locator('input[name="password"]').fill(password)
                    page.get_by_role("button", name="Open workspace").click()
                    page.wait_for_url(base+"/")
                    assert page.locator("h1").count() == 1
                    page.screenshot(path=str(output/"stockdesk-desktop.png"), full_page=True)
                    checked = []
                    for path in ("/inventory", "/movements", "/products", "/orders", "/counts", "/catalog", "/users", "/audit", "/api-guide"):
                        response = page.goto(base+path, wait_until="networkidle")
                        assert response.status == 200, (path, response.status)
                        assert page.locator("h1").count() == 1, path
                        checked.append(path)
                    page.goto(base+"/products")
                    form = page.locator('form[action="/actions/product-create"]')
                    form.locator('[name="sku"]').fill("BROWSER-001")
                    form.locator('[name="name"]').fill("Synthetic browser-check product")
                    form.locator('[name="reorder_level"]').fill("3")
                    form.locator('[name="unit_cost_minor"]').fill("2500")
                    form.get_by_role("button", name="Create product").click()
                    page.wait_for_url(base+"/products/*")
                    assert "BROWSER-001" in page.locator("main").inner_text()
                    page.goto(base+"/movements")
                    form = page.locator('form[action="/actions/movement"]')
                    products = form.locator('[name="product_id"] option').all_text_contents()
                    selection = next(text for text in products if "BROWSER-001" in text)
                    form.locator('[name="product_id"]').select_option(label=selection)
                    form.locator('[name="quantity"]').fill("7")
                    form.locator('[name="warehouse_id"]').select_option(index=1)
                    form.locator('[name="note"]').fill("Synthetic real-browser smoke check")
                    form.get_by_role("button", name="Post stock movement").click()
                    page.wait_for_url(base+"/movements?**")
                    assert "BROWSER-001" in page.locator("table").inner_text()
                    page.goto(base+"/")
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.screenshot(path=str(output/"stockdesk-mobile.png"), full_page=True)
                    mobile_width = page.evaluate("({scroll:document.documentElement.scrollWidth, viewport:innerWidth})")
                    assert mobile_width["scroll"] <= mobile_width["viewport"], mobile_width
                    page.get_by_role("button", name="Sign out").click()
                    page.wait_for_url(base+"/login")
                    assert not errors, errors
                    report = {"status": "passed", "browser": browser.version,
                              "checked_pages": checked,
                              "workflows": ["HTML login", "HTML product creation", "HTML stock receipt", "HTML sign out"],
                              "desktop_viewport": [1440,1080], "mobile_viewport": [390,844],
                              "mobile_horizontal_overflow": False, "page_errors": errors,
                              "data": "Disposable synthetic data, not real users or commercial activity."}
                    (output/"browser-smoke.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
                    print(json.dumps(report,indent=2))
                    context.close()
                finally:
                    browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
