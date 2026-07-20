"""Fail deployment when the generated frontend bundle is missing or stale."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "frontend" / "index.html"
BUNDLE_PATH = ROOT / "frontend" / "app.bundle.js"
BOOT_PATH = ROOT / "frontend" / "boot.js"
BUILD_SCRIPT_PATH = ROOT / "scripts" / "build_frontend_bundle.js"


def main() -> None:
    for required_path in (HTML_PATH, BUNDLE_PATH, BOOT_PATH, BUILD_SCRIPT_PATH):
        if not required_path.is_file():
            raise SystemExit(f"required frontend artifact is missing: {required_path.relative_to(ROOT)}")

    html = HTML_PATH.read_text(encoding="utf-8")
    if '<script src="/boot.js"></script>' not in html:
        raise SystemExit("frontend/index.html does not load /boot.js")
    if '<script src="/app.bundle.js"' not in html:
        raise SystemExit("frontend/index.html does not load /app.bundle.js")
    if "Babel.transform" in html:
        raise SystemExit("frontend/index.html still compiles JSX at runtime")
    match = re.search(
        r'<script\s+type=["\']text/plain["\']\s+id=["\']app-source["\']>(.*?)</script>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        raise SystemExit("frontend/index.html does not contain #app-source")

    source = match.group(1)
    if source.startswith("\n"):
        source = source[1:]
    expected = hashlib.sha256(source.encode("utf-8")).hexdigest()

    bundle_head = BUNDLE_PATH.read_text(encoding="utf-8")[:512]
    actual_match = re.search(r"app-source-sha256:\s*([0-9a-f]{64})", bundle_head)
    actual = actual_match.group(1) if actual_match else ""
    if actual != expected:
        raise SystemExit(
            "frontend/app.bundle.js is stale; run: node scripts/build_frontend_bundle.js"
        )

    print(f"Frontend bundle OK ({expected[:12]})")


if __name__ == "__main__":
    main()
