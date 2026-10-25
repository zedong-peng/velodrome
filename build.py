#!/usr/bin/env python3
"""Check every run and write the static site to site/.

    python3 build.py
    python3 -m http.server -d site 8000

Standard library only. The artwork is copied into site/art/ with a strict
Content-Security-Policy and only shown in sandboxed frames; the files in runs/
stay byte-for-byte what the agents wrote.
"""

import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import sys

from velodrome import load_toml, part, route_slug

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"
WEB = ROOT / "web"
SITE = ROOT / "site"
REPO = "https://github.com/zedong-peng/velodrome"
URL = "https://zedongpeng.com/velodrome/"
TITLE = "Velodrome · a pelican riding a bicycle"
DESCRIPTION = "How each new model draws a pelican riding a bicycle, at its highest effort, in a coding agent."
CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data: blob:; font-src data:; media-src data: blob:"
)
EFFORTS = ["none", "minimal", "low", "medium", "high", "xhigh", "max", "default"]
# The page leads with each model at its highest effort; "default" ranks lowest, since it names no level.
EFFORT_RANK = {e: i for i, e in enumerate(["default"] + EFFORTS[:-1])}
# Relays resell access to other labs' models; the page shows only official APIs and subscriptions.
SHOWN_KINDS = ("official", "subscription")


def check_run(path, catalog, problems):
    try:
        run = json.loads((path / "run.json").read_text())
    except (OSError, ValueError) as exc:
        problems.append(f"{path.name}: unreadable run.json ({exc})")
        return None
    route = run.get("route") or {}
    for field, table in (("model", "models"), ("harness", "harnesses"), ("provider", "providers")):
        if route.get(field) not in catalog[table]:
            problems.append(f"{path.name}: {field} {route.get(field)!r} is not in catalog.toml")
            return None
    expected = route_slug(route, run.get("attempt", 1))
    if expected != path.name:
        problems.append(f"{path.name}: directory does not match its route ({expected})")
    verdict = run.get("verdict")
    if verdict is None:
        problems.append(f"{path.name}: no verdict yet; run `python3 velodrome.py render {path.name}`")
    elif verdict.get("file") and not (path / "pelican.html").is_file():
        problems.append(f"{path.name}: verdict names a file but pelican.html is missing")
    elif verdict.get("drawn") and not (path / "poster.jpg").is_file():
        problems.append(f"{path.name}: drawn but poster.jpg is missing")
    return run


def protect(markup):
    """The artwork with a CSP meta first in <head>, so it cannot reach anything."""
    meta = f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
    head = re.search(r"<head[^>]*>", markup, re.IGNORECASE)
    if head:
        return markup[: head.end()] + meta + markup[head.end():]
    return meta + markup


def record(run, slug, catalog):
    route = run["route"]
    rank = {table: list(catalog[table]) for table in ("models", "harnesses", "providers")}
    verdict = run.get("verdict") or {}
    tokens = run.get("tokens") or {}
    provider = catalog["providers"][route["provider"]]
    return {
        "slug": slug,
        "model": route["model"],
        "modelName": catalog["models"][route["model"]]["name"],
        "lab": catalog["models"][route["model"]]["lab"],
        "released": catalog["models"][route["model"]].get("released"),
        "harness": route["harness"],
        "harnessName": catalog["harnesses"][route["harness"]]["name"],
        "harnessVersion": run.get("harness_version"),
        "provider": route["provider"],
        "providerName": provider["name"],
        "providerKind": provider["kind"],
        "effort": route["effort"],
        "attempt": run.get("attempt", 1),
        # One sort key for the page: model, harness, provider, effort, attempt.
        "sort": [
            rank["models"].index(route["model"]),
            rank["harnesses"].index(route["harness"]),
            rank["providers"].index(route["provider"]),
            EFFORTS.index(route["effort"]) if route["effort"] in EFFORTS else len(EFFORTS),
            run.get("attempt", 1),
        ],
        "date": (run.get("started_at") or "")[:10] or None,
        "seconds": run.get("agent_seconds"),
        "steps": run.get("steps"),
        "tokensIn": tokens.get("input"),
        "tokensOut": tokens.get("output"),
        "cost": run.get("list_cost_usd"),
        "drawn": bool(verdict.get("drawn")),
        "moving": bool(verdict.get("moving")),
        "file": verdict.get("file"),
        "bytes": verdict.get("bytes"),
        "errors": verdict.get("errors") or 0,
    }


def models(data, catalog):
    """One entry per model, newest release first, with the run the page leads with and one run per harness.

    Runs are chosen without looking at the picture: the highest effort, then an official API over a
    subscription, then catalog order of harness and provider, then the first attempt. The lead is the best
    run of the model, `harnesses` the best of each harness in catalog order; the provider shows only in a
    run's detail.
    """
    kinds = {k: i for i, k in enumerate(SHOWN_KINDS)}
    best = lambda runs: min(runs, key=lambda r: (-EFFORT_RANK.get(r["effort"], -1), kinds[r["providerKind"]], r["sort"][1:]))
    out = {}
    for r in data:
        out.setdefault(r["model"], []).append(r)
    entries = []
    for model, runs in out.items():
        by_harness = {}
        for r in runs:
            by_harness.setdefault(r["harness"], []).append(r)
        info = catalog["models"][model]
        entries.append({"model": model, "page": part(model), "name": info["name"], "lab": info["lab"], "released": info.get("released"),
                        "lead": best(runs)["slug"], "harnesses": [best(rs)["slug"] for rs in by_harness.values()],
                        "runs": [r["slug"] for r in runs]})
    entries.sort(key=lambda m: (m["released"] or "", m["name"]), reverse=True)
    return entries


def fmt_time(s):
    m, s = divmod(round(s), 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


def fmt_tokens(n):
    return f"{n / 1e6:.2f}M" if n >= 1e6 else f"{n / 1e3:.{0 if n >= 1e4 else 1}f}k" if n >= 1e3 else str(n)


def summary(r):
    """A run in one line, for link previews."""
    if not r["file"]:
        spent = f" after {fmt_tokens(r['tokensOut'])} output tokens" if r["tokensOut"] else ""
        return f"{r['modelName']} in {r['harnessName']} wrote no file{spent}."
    took = [fmt_time(r["seconds"]) if r["seconds"] is not None else None,
            f"{fmt_tokens(r['tokensOut'])} output tokens" if r["tokensOut"] else None]
    took = ", ".join(t for t in took if t)
    return f"A pelican riding a bicycle by {r['modelName']}, in {r['harnessName']}" + (f": {took}." if took else ".")


def dress(page, title, description, url, run=None, depth=0):
    """The page with its own title and link preview. A model's page sits one folder down and opens its lead run."""
    e = lambda v: html.escape(v, quote=True)
    tags = [f"<title>{e(title)}</title>",
            f'<meta name="description" content="{e(description)}">',
            f'<link rel="canonical" href="{e(url)}">',
            '<meta property="og:site_name" content="Velodrome">',
            '<meta property="og:type" content="website">',
            f'<meta property="og:title" content="{e(title)}">',
            f'<meta property="og:description" content="{e(description)}">',
            f'<meta property="og:url" content="{e(url)}">']
    if run and run["drawn"]:
        tags += [f'<meta property="og:image" content="{e(URL)}posters/{e(run["slug"])}.jpg">',
                 '<meta name="twitter:card" content="summary_large_image">']
    page = page.replace("<!-- META -->", "\n".join(tags))
    if depth:
        page = page.replace('<base href="./">', f'<base href="{"../" * depth}">')
        page = page.replace("<body>", f'<body data-run="{e(run["slug"])}">')
    return page


def main():
    catalog = load_toml("catalog.toml")
    problems, runs = [], []
    for path in sorted(p for p in RUNS.iterdir() if p.is_dir()) if RUNS.exists() else []:
        run = check_run(path, catalog, problems)
        if run:
            runs.append((path, run))
    if problems:
        print("\n".join(problems), file=sys.stderr)
        sys.exit(1)

    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "art").mkdir(parents=True)
    (SITE / "posters").mkdir()
    data = []
    for path, run in runs:
        if (path / "pelican.html").is_file():
            markup = (path / "pelican.html").read_text(encoding="utf-8", errors="replace")
            (SITE / "art" / f"{path.name}.html").write_text(protect(markup), encoding="utf-8")
        if (path / "poster.jpg").is_file():
            shutil.copyfile(path / "poster.jpg", SITE / "posters" / f"{path.name}.jpg")
        data.append(record(run, path.name, catalog))
    data.sort(key=lambda r: r["sort"])
    data = [r for r in data if r["providerKind"] in SHOWN_KINDS]
    payload = {"repo": REPO, "title": TITLE, "runs": data, "models": models(data, catalog)}

    page = (WEB / "index.html").read_text()
    prompt = (ROOT / "pelican" / "instruction.md").read_text().strip()
    page = page.replace("<!-- PROMPT -->", html.escape(prompt))
    page = page.replace("/* DATA */", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    # A content hash on each asset, so a browser never pairs a fresh page with a cached script.
    for name, attr in (("app.js", "src"), ("style.css", "href")):
        tag = hashlib.sha256((WEB / name).read_bytes()).hexdigest()[:8]
        page = page.replace(f'{attr}="{name}"', f'{attr}="{name}?v={tag}"')
    # Each model has a page of its own to share: the same gallery, opened on its lead run.
    by_slug = {r["slug"]: r for r in data}
    newest = by_slug[payload["models"][0]["lead"]] if payload["models"] else None
    (SITE / "index.html").write_text(dress(page, TITLE, DESCRIPTION, URL, newest))
    for m in payload["models"]:
        lead = by_slug[m["lead"]]
        (SITE / m["page"]).mkdir()
        (SITE / m["page"] / "index.html").write_text(
            dress(page, f"{m['name']} · Velodrome", summary(lead), f"{URL}{m['page']}/", lead, depth=1))
    for name in ("app.js", "style.css", "favicon.svg"):
        shutil.copyfile(WEB / name, SITE / name)
    (SITE / "fonts").mkdir(exist_ok=True)
    for font in (WEB / "fonts").glob("*.woff2"):
        shutil.copyfile(font, SITE / "fonts" / font.name)
    print(f"Built {len(data)} runs into {SITE.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
