"""Fetch a page with Chromium, for the sources that plain HTTP cannot read.

Six sources in the registry are marked `method: browser` and none has ever been written, because
the two things that make a browser usable here were not in place: Playwright is not in the base
image, and Chromium does not trust this environment's TLS interception CA.

Both are solved here.

    from ._browser import browser_get, BrowserUnavailable
    page = browser_get("https://example.com/locations", archive_dir, "locations.html")

The file lands in `archive_dir` exactly as `http_get` leaves it — same shape, same .meta.json
sidecar — so a browser source archives, hashes and re-parses like any other.

TLS
---
Outbound HTTPS goes through an interception proxy whose CA lives at
/root/.ccr/agent-proxy-ca.crt. Python trusts it through the standard CA environment variables;
Chromium does not read those, it reads the NSS store, and this image has no `certutil` to put a
certificate into one. So the CA is pinned by SPKI instead, with
`--ignore-certificate-errors-spki-list`.

That flag is NOT `--ignore-certificate-errors`. Verification stays on and every other chain is
still validated normally — the flag whitelists the specific public keys named, and nothing else.
The keys are read out of the same CA file the rest of the sandbox already trusts, at run time, so
a rotated CA is picked up rather than pinned to a stale fingerprint. If that file is missing the
browser launches with no exception at all and a genuinely bad certificate still fails.
"""
from __future__ import annotations
import base64
import hashlib
import json
import re
import subprocess
from pathlib import Path

CA_FILES = ("/root/.ccr/agent-proxy-ca.crt",)
_CERT = re.compile(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.S)
DEFAULT_TIMEOUT_MS = 45000


class BrowserUnavailable(RuntimeError):
    """Playwright or Chromium is not usable here. Says which, and what to do about it."""


def _spki_pins() -> list[str]:
    """Base64 SHA-256 of each SubjectPublicKeyInfo in the proxy CA file, read fresh every launch."""
    pins: list[str] = []
    for f in CA_FILES:
        p = Path(f)
        if not p.exists():
            continue
        for block in _CERT.findall(p.read_text()):
            try:
                pub = subprocess.run(["openssl", "x509", "-pubkey", "-noout"], input=block,
                                     capture_output=True, text=True, check=True).stdout
                der = base64.b64decode("".join(l for l in pub.splitlines() if "-----" not in l))
                pins.append(base64.b64encode(hashlib.sha256(der).digest()).decode())
            except Exception:
                continue        # a cert we cannot read is simply not pinned
    return pins


def _chromium_path() -> str | None:
    """The image ships Chromium under PLAYWRIGHT_BROWSERS_PATH; do not download another."""
    import glob
    found = sorted(glob.glob("/opt/pw-browsers/**/chrome", recursive=True))
    return found[0] if found else None


def browser_get(url: str, archive_dir: Path, name: str, *, timeout_ms: int = DEFAULT_TIMEOUT_MS,
                wait_until: str = "networkidle", after_load=None) -> Path:
    """Render `url` and write its HTML to archive_dir/name, with a .meta.json sidecar.

    `after_load` is given the Playwright page once it has loaded, for a directory that only yields
    its rows after a search is submitted — several of the certification directories do nothing at
    all until a form is filled in, and returning their landing page would archive an empty roster
    that parses to zero rows without failing.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise BrowserUnavailable(
            "playwright is not installed. `pip install playwright` — do NOT run `playwright "
            "install`, the image already ships Chromium under PLAYWRIGHT_BROWSERS_PATH.") from e

    exe = _chromium_path()
    if exe is None:
        raise BrowserUnavailable(
            "no Chromium under /opt/pw-browsers. PLAYWRIGHT_BROWSERS_PATH is set for this image; "
            "if it is genuinely absent, that is an image problem, not something to fix by "
            "downloading a second browser at run time.")

    pins = _spki_pins()
    args = [f"--ignore-certificate-errors-spki-list={','.join(pins)}"] if pins else []
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / name
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=exe, args=args)
        try:
            page = b.new_page()
            page.goto(url, timeout=timeout_ms, wait_until=wait_until)
            if after_load is not None:
                after_load(page)
            html = page.content()
        finally:
            b.close()
    dest.write_text(html, encoding="utf-8")
    (archive_dir / f"{name}.meta.json").write_text(json.dumps(
        {"url": url, "engine": "chromium", "wait_until": wait_until,
         "interactive": after_load is not None, "bytes": len(html),
         "sha256": hashlib.sha256(html.encode("utf-8")).hexdigest()}, indent=1))
    return dest
