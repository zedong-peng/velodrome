#!/usr/bin/env python3
"""Check every run and write the static site to site/.

    python3 build.py
    python3 -m http.server -d site 8000

Standard library only. The artwork is copied into site/art/ with a strict
Content-Security-Policy and only shown in sandboxed frames; the files in runs/
stay byte-for-byte what the agents wrote.
"""

import html
import json
from pathlib import Path
import re
import shutil
import sys

from velodrome import load_toml, route_slug

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"
WEB = ROOT / "web"
SITE = ROOT / "site"
REPO = "https://github.com/zedong-peng/velodrome"
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
        entries.append({"model": model, "name": info["name"], "lab": info["lab"], "released": info.get("released"),
                        "lead": best(runs)["slug"], "harnesses": [best(rs)["slug"] for rs in by_harness.values()],
                        "runs": [r["slug"] for r in runs]})
    entries.sort(key=lambda m: (m["released"] or "", m["name"]), reverse=True)
    return entries


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
    payload = {"repo": REPO, "runs": data, "models": models(data, catalog)}

    prompt = (ROOT / "pelican" / "instruction.md").read_text().strip()
    page = (WEB / "index.html").read_text()
    page = page.replace("<!-- PROMPT -->", html.escape(prompt))
    page = page.replace("/* DATA */", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    (SITE / "index.html").write_text(page)
    for name in ("app.js", "style.css", "favicon.svg"):
        shutil.copyfile(WEB / name, SITE / name)
    print(f"Built {len(data)} runs into {SITE.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
