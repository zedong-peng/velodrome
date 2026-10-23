"""Render pelican.html offline and record what it does.

Runs in the separate verifier container: /app is what the agent left behind,
/logs/verifier is what Harbor keeps. The verdict is deliberately shallow. It
says whether there is a drawing and whether it moves, not whether it is good.
"""

import base64
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

APP = Path("/app")
OUT = Path("/logs/verifier")
WIDTH, HEIGHT = 960, 720
FRAMES = (0.5, 1.0, 2.0, 3.0)  # seconds after load
POSTER_AT = 2.0
MOVING = 0.002  # share of pixels that must change between two frames
MAX_BYTES = 2 * 1024 * 1024

# Pixel difference between two PNG data URLs, computed in the browser so the
# verifier needs nothing beyond the Playwright image.
DIFF_JS = """async ([a, b]) => {
  const load = (src) => new Promise((ok, err) => {
    const img = new Image(); img.onload = () => ok(img); img.onerror = err; img.src = src;
  });
  const [x, y] = await Promise.all([load(a), load(b)]);
  const c = document.createElement('canvas');
  c.width = x.width; c.height = x.height;
  const g = c.getContext('2d');
  g.drawImage(x, 0, 0);
  const p = g.getImageData(0, 0, c.width, c.height).data;
  g.clearRect(0, 0, c.width, c.height);
  g.drawImage(y, 0, 0);
  const q = g.getImageData(0, 0, c.width, c.height).data;
  let changed = 0;
  for (let i = 0; i < p.length; i += 4) {
    if (Math.abs(p[i] - q[i]) + Math.abs(p[i + 1] - q[i + 1]) + Math.abs(p[i + 2] - q[i + 2]) > 24) changed++;
  }
  return changed / (p.length / 4);
}"""

SVG_JS = """() => {
  const svgs = [...document.querySelectorAll('svg')];
  let cover = 0;
  for (const s of svgs) {
    const r = s.getBoundingClientRect();
    const w = Math.max(0, Math.min(r.right, innerWidth) - Math.max(r.left, 0));
    const h = Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0));
    cover = Math.max(cover, (w * h) / (innerWidth * innerHeight));
  }
  return {svgs: svgs.length, shapes: document.querySelectorAll('svg *').length, cover};
}"""


def find_artwork():
    named = APP / "pelican.html"
    if named.is_file():
        return named
    found = [p for p in APP.rglob("*.htm*") if p.is_file() and "node_modules" not in p.parts]
    return found[0] if len(found) == 1 else None


def render(path, report):
    blocked = []
    errors = []

    def gate(route):
        url = route.request.url
        if url.startswith(("file:", "data:", "blob:")):
            route.continue_()
        else:
            blocked.append(url[:200])
            route.abort()

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        page.route("**/*", gate)
        page.on("pageerror", lambda e: errors.append(str(e)[:300]))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text[:300]))
        page.goto(path.as_uri(), wait_until="load", timeout=20_000)

        frames, elapsed = [], 0.0
        for t in FRAMES:
            page.wait_for_timeout((t - elapsed) * 1000)
            elapsed = t
            if t == POSTER_AT:
                page.screenshot(path=str(OUT / "poster.jpg"), type="jpeg", quality=85)
            frames.append(page.screenshot(type="png"))
        report.update(page.evaluate(SVG_JS))

        scratch = browser.new_page()
        urls = ["data:image/png;base64," + base64.b64encode(f).decode() for f in frames]
        report["motion"] = [round(scratch.evaluate(DIFF_JS, [a, b]), 4) for a, b in zip(urls, urls[1:])]
        browser.close()

    report["errors"] = errors[:20]
    report["blocked_requests"] = blocked[:20]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"file": None, "bytes": 0}
    path = find_artwork()
    if path is not None:
        report["file"] = str(path.relative_to(APP))
        report["bytes"] = path.stat().st_size
        if report["bytes"] <= MAX_BYTES:
            try:
                render(path, report)
            except Exception as exc:  # a page that hangs or crashes is a result, not a verifier bug
                report["render_error"] = f"{type(exc).__name__}: {exc}"[:500]

    drawn = report.get("svgs", 0) > 0 and report.get("cover", 0) > 0.05 and "render_error" not in report
    moving = drawn and max(report.get("motion") or [0]) > MOVING
    report["drawn"], report["moving"] = drawn, moving
    (OUT / "check.json").write_text(json.dumps(report, indent=2))
    (OUT / "reward.json").write_text(json.dumps({"reward": float(drawn), "moving": float(moving)}))
    print(json.dumps(report))


if __name__ == "__main__":
    main()
