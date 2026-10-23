#!/usr/bin/env python3
"""Run Velodrome routes through Harbor and import what comes back.

    python3 velodrome.py plan [FILTER...]           # every route and whether it has its runs
    python3 velodrome.py run [FILTER...] [-j 3]     # run what is missing
    python3 velodrome.py run ... --dry-run          # print the Harbor commands instead
    python3 velodrome.py import JOB/TRIAL           # import one finished Harbor trial by hand
    python3 velodrome.py render [SLUG...]           # re-render posters and verdicts, no model calls

A route is harness + provider + model + effort. A FILTER keeps routes where any
of the four equals it (`codex`, `rightapi`, `kimi-k3`). Each run is its own
Harbor job: a fresh container from pelican/, the harness installed with public
network, the agent phase cut down to the provider's host, then a separate
container that renders whatever was left in /app.

Keys come from keys.env (or $VELODROME_KEYS) and reach Harbor only through its
process environment: the command line carries ${VAR} templates, never values.
Python 3.11+, standard library only; needs `harbor` and Docker.
"""

import argparse
import concurrent.futures
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
TASK = ROOT / "pelican"
RUNS = ROOT / "runs"
JOBS = ROOT / "jobs"
CLAUDE_CREDENTIALS = Path.home() / ".claude" / ".credentials.json"

# How each harness takes an effort setting through Harbor; None means it has no knob.
EFFORT_KWARG = {
    "opencode": "variant",
    "pi": "thinking",
    "openclaw": "thinking",
    "cline-cli": "reasoning_effort",
    "hermes": None,
    "kimi-cli": None,
    "qwen-coder": None,
}
SDK_PACKAGE = {
    "anthropic": "@ai-sdk/anthropic",
    "chat": "@ai-sdk/openai-compatible",
    "responses": "@ai-sdk/openai",
    "google": "@ai-sdk/google",
}
PI_API = {"anthropic": "anthropic-messages", "chat": "openai-completions", "responses": "openai-responses"}
# Harbor exceptions that still leave a fair result if a drawing exists.
KEEP_IF_DRAWN = {"AgentTimeoutError", "NonZeroAgentExitCodeError"}

print_lock = threading.Lock()


def say(*parts):
    with print_lock:
        print(*parts, flush=True)


def load_toml(name):
    with open(ROOT / name, "rb") as f:
        return tomllib.load(f)


def part(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def route_slug(route, attempt=1):
    slug = "--".join(part(route[k]) for k in ("model", "harness", "provider", "effort"))
    return slug if attempt == 1 else f"{slug}--{attempt}"


def load_routes(catalog):
    routes = []
    for raw in load_toml("routes.toml")["routes"]:
        route = {"effort": "default", "attempts": 1, **raw}
        for field, table in (("harness", "harnesses"), ("provider", "providers"), ("model", "models")):
            if route[field] not in catalog[table]:
                sys.exit(f"routes.toml: unknown {field} {route[field]!r}")
        allowed = catalog["providers"][route["provider"]].get("harnesses")
        if allowed and route["harness"] not in allowed:
            sys.exit(f"routes.toml: {route['provider']} may only be used from {allowed}")
        routes.append(route)
    return routes


def pick_api(route, catalog):
    """The format this harness should speak to this provider for this model."""
    provider = catalog["providers"][route["provider"]]
    offered = provider.get("apis", {}).get(route["model"]) or provider.get("default_api")
    if offered:
        offered = [offered]
    else:
        offered = [a for a in ("anthropic", "responses", "chat", "google") if provider.get(base_key(a))]
    wanted = [route["api"]] if "api" in route else catalog["harnesses"][route["harness"]]["apis"]
    for api in wanted:
        if api in offered:
            return api
    sys.exit(f"{route_slug(route)}: {route['harness']} speaks {wanted}, {route['provider']} offers {offered}")


def base_key(api):
    return {"chat": "openai", "responses": "openai"}.get(api, api)


def key_var(provider_id, api):
    return f"VELODROME_KEY_{part(provider_id).replace('-', '_').upper()}"


def connect(route, catalog, keys):
    """Harbor model argument, agent kwargs, agent env and allowed hosts for a route."""
    pid, harness, model = route["provider"], route["harness"], route["model"]
    provider = catalog["providers"][pid]
    api = pick_api(route, catalog)
    base = provider[base_key(api)]
    var = key_var(pid, api)
    if f"{var}_{api.upper()}" in keys:
        var = f"{var}_{api.upper()}"
    key = "${" + var + "}"
    kwargs, env = {}, {}

    if provider.get("login") == "claude-oauth":
        env = {"CLAUDE_CODE_OAUTH_TOKEN": key, "CLAUDE_FORCE_OAUTH": "1"}
        arg = f"anthropic/{model}"
    elif harness == "opencode" and pid == "opencode-go":
        env = {"OPENCODE_API_KEY": key}
        arg = f"opencode-go/{model}"
    elif harness == "opencode":
        url = base + "/v1" if api == "anthropic" else base
        kwargs["opencode_config"] = {"provider": {pid: {
            "npm": SDK_PACKAGE[api],
            "name": provider["name"],
            "options": {"baseURL": url, "apiKey": "{env:VELODROME_API_KEY}"},
            "models": {model: {"name": model}},
        }}}
        env = {"VELODROME_API_KEY": key}
        arg = f"{pid}/{model}"
    elif api == "anthropic":
        env = {"ANTHROPIC_API_KEY": key, "ANTHROPIC_BASE_URL": base, "ANTHROPIC_API_BASE": base}
        arg = f"anthropic/{model}"
    elif api == "google":
        env = {"GEMINI_API_KEY": key, "GOOGLE_GEMINI_BASE_URL": base}
        arg = f"google/{model}"
    else:
        env = {"OPENAI_API_KEY": key, "OPENAI_BASE_URL": base, "OPENAI_API_BASE": base}
        arg = f"openai/{model}"

    if harness == "pi" and pid != "claude-max":
        kwargs["model_api"] = PI_API[api]
    if harness == "aider" and api != "anthropic":
        arg = f"openai/{arg}"  # Harbor strips one prefix; litellm needs the other
    if harness == "goose" and api == "chat":
        host = urlsplit(base)
        env |= {"OPENAI_HOST": f"{host.scheme}://{host.netloc}", "OPENAI_BASE_PATH": host.path.strip("/") + "/chat/completions"}
    if harness == "kimi-cli":
        kwargs["base_url"] = base
    if harness == "openhands":
        env |= {"LLM_API_KEY": key, "LLM_BASE_URL": base}
    if harness == "mini-swe-agent":
        env |= {"MSWEA_API_KEY": key}

    effort = route["effort"]
    knob = EFFORT_KWARG.get(harness, "reasoning_effort")
    if effort != "default":
        if knob is None:
            sys.exit(f"{route_slug(route)}: {harness} has no effort setting; use effort = \"default\"")
        kwargs[knob] = effort

    if route.get("version"):
        kwargs["version"] = route["version"]  # the harness version to install; latest when unset

    hosts = sorted({urlsplit(base).hostname})
    return {"api": api, "model": arg, "kwargs": kwargs, "env": env, "hosts": hosts}


def load_keys():
    path = Path(os.environ.get("VELODROME_KEYS") or ROOT / "keys.env")
    keys = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                keys[name.strip()] = value.strip()
    return keys


def claude_token(min_minutes=40):
    """The local Claude Code subscription login, if it stays valid long enough for one run."""
    try:
        oauth = json.loads(CLAUDE_CREDENTIALS.read_text())["claudeAiOauth"]
    except (OSError, KeyError, ValueError):
        return None
    left = (oauth.get("expiresAt", 0) / 1000 - time.time()) / 60
    return oauth.get("accessToken") if left >= min_minutes else None


def harbor_env(keys):
    """A clean environment: Harbor falls back to os.environ for agent settings."""
    keep = ("PATH", "HOME", "USER", "LANG", "TERM", "TMPDIR", "XDG_RUNTIME_DIR", "DOCKER_HOST", "DOCKER_CONFIG")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env |= {k: v for k, v in keys.items() if k.startswith("VELODROME_KEY_")}
    return env


def harbor_command(route, plan, job_name):
    cmd = ["harbor", "run", "-p", str(TASK), "-a", route["harness"], "-m", plan["model"],
           "-o", str(JOBS), "--job-name", job_name, "-n", "1", "-y", "-q"]
    for k, v in plan["kwargs"].items():
        cmd += ["--ak", f"{k}={json.dumps(v) if not isinstance(v, str) else v}"]
    for k, v in plan["env"].items():
        cmd += ["--ae", f"{k}={v}"]
    for host in plan["hosts"]:
        cmd += ["--allow-agent-host", host]
    return cmd


def existing_attempts(route):
    n = 0
    while (RUNS / route_slug(route, n + 1) / "run.json").exists():
        n += 1
    return n


def matches(route, filters):
    return not filters or any(f in (route["harness"], route["provider"], route["model"], route["effort"]) for f in filters)


# ------------------------------------------------------------------ import


def list_cost(tokens, price):
    if not price or tokens.get("input") is None:
        return None
    cache = tokens.get("cache") or 0
    fresh = max((tokens.get("input") or 0) - cache, 0)
    usd = fresh * price["input"] + cache * price.get("cache_read", price["input"]) + (tokens.get("output") or 0) * price["output"]
    return round(usd / 1e6, 4)


def seconds(span):
    if not span or not span.get("started_at") or not span.get("finished_at"):
        return None
    parse = lambda s: datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return round((parse(span["finished_at"]) - parse(span["started_at"])).total_seconds(), 1)


def verdict(check):
    return {
        "file": check.get("file"),
        "bytes": check.get("bytes"),
        "drawn": bool(check.get("drawn")),
        "moving": bool(check.get("moving")),
        "svgs": check.get("svgs"),
        "shapes": check.get("shapes"),
        "motion": check.get("motion"),
        "errors": len(check.get("errors") or []),
        "blocked_requests": len(check.get("blocked_requests") or []),
        "render_error": check.get("render_error"),
    }


def render(slugs):
    """Re-render runs with the verifier image, locally and offline. No model calls."""
    subprocess.run(["docker", "build", "-q", "-t", "velodrome-check", str(TASK / "tests")], check=True, capture_output=True)
    for slug in slugs:
        run_dir = RUNS / slug
        with tempfile.TemporaryDirectory() as tmp:
            app, out = Path(tmp) / "app", Path(tmp) / "out"
            app.mkdir()
            out.mkdir()
            out.chmod(0o777)
            if (run_dir / "pelican.html").exists():
                shutil.copyfile(run_dir / "pelican.html", app / "pelican.html")
            subprocess.run(["docker", "run", "--rm", "--network", "none", "-v", f"{app}:/app:ro", "-v", f"{out}:/logs/verifier",
                            "velodrome-check", "python3", "/tests/check.py"], check=True, capture_output=True)
            check = json.loads((out / "check.json").read_text())
            if (out / "poster.jpg").exists():
                shutil.copyfile(out / "poster.jpg", run_dir / "poster.jpg")
            else:
                (run_dir / "poster.jpg").unlink(missing_ok=True)
        record = json.loads((run_dir / "run.json").read_text())
        record["verdict"] = verdict(check)
        (run_dir / "run.json").write_text(json.dumps(record, indent=2) + "\n")
        v = record["verdict"]
        say(f"{slug}: {'moving' if v['moving'] else 'drawn' if v['drawn'] else 'no drawing'}")


def opencode_reasoning(log):
    """Reasoning tokens in an OpenCode JSON event log, summed over its steps."""
    total = 0
    if log.exists():
        for line in log.read_text(errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("type") == "step_finish":
                total += ((event.get("part") or {}).get("tokens") or {}).get("reasoning") or 0
    return total


def import_trial(trial, route, plan, catalog, attempt):
    """Copy one finished trial into runs/<slug>/. Returns the slug, or None when it is not a result."""
    result = json.loads((trial / "result.json").read_text())
    exc = (result.get("exception_info") or {}).get("exception_type")
    check_path = trial / "verifier" / "check.json"
    check = json.loads(check_path.read_text()) if check_path.exists() else {}
    if exc and not (exc in KEEP_IF_DRAWN and check.get("file")):
        return None
    if not check_path.exists():
        return None

    slug = route_slug(route, attempt)
    out = RUNS / slug
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    if check.get("file"):
        shutil.copyfile(trial / "artifacts" / "app" / check["file"], out / "pelican.html")
    if (trial / "verifier" / "poster.jpg").exists():
        shutil.copyfile(trial / "verifier" / "poster.jpg", out / "poster.jpg")

    agent = result.get("agent_result") or {}
    tokens = {"input": agent.get("n_input_tokens"), "cache": agent.get("n_cache_tokens"), "output": agent.get("n_output_tokens")}
    reasoning = opencode_reasoning(trial / "agent" / "opencode.txt")
    if reasoning:
        tokens["output"] = (tokens["output"] or 0) + reasoning  # Harbor leaves OpenCode's reasoning out; it is billed as output
    steps = None
    trajectory = trial / "agent" / "trajectory.json"
    if trajectory.exists():
        try:
            steps = len(json.loads(trajectory.read_text()).get("steps") or [])
        except ValueError:
            pass
    record = {
        "route": {k: route[k] for k in ("harness", "provider", "model", "effort")} | {"api": plan["api"]},
        "attempt": attempt,
        "harness_version": (result.get("agent_info") or {}).get("version"),
        "harbor_version": harbor_version(),
        "task_checksum": result.get("task_checksum"),
        "started_at": (result.get("agent_execution") or {}).get("started_at"),
        "agent_seconds": seconds(result.get("agent_execution")),
        "steps": steps,
        "tokens": tokens,
        "list_cost_usd": list_cost(tokens, catalog["models"][route["model"]].get("price")),
        "harness_cost_usd": agent.get("cost_usd"),
        "exception": exc,
        "verdict": verdict(check),
    }
    (out / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    return slug


_harbor_version = None


def harbor_version():
    global _harbor_version
    if _harbor_version is None:
        out = subprocess.run(["harbor", "--version"], capture_output=True, text=True).stdout
        _harbor_version = out.strip().split()[-1] if out.strip() else None
    return _harbor_version


def explain_failure(trial):
    try:
        result = json.loads((trial / "result.json").read_text())
    except (OSError, ValueError):
        return "no result.json"
    info = result.get("exception_info") or {}
    message = info.get("exception_message") or ""
    log = trial / "agent"
    for name in ("opencode.txt", "claude-code.txt", "codex.txt"):
        if (log / name).exists():
            tail = (log / name).read_text(errors="replace")[-400:]
            message += " | " + tail.replace("\n", " ")
            break
    return f"{info.get('exception_type') or 'no drawing'}: {message[:600]}"


# --------------------------------------------------------------------- run


def run_one(route, attempt, catalog, keys, dry_run):
    slug = route_slug(route, attempt)
    plan = connect(route, catalog, keys)
    job_name = f"{slug}--{datetime.datetime.now(datetime.UTC):%Y%m%dT%H%M%S}"
    cmd = harbor_command(route, plan, job_name)
    if dry_run:
        say(" ".join(cmd))
        return
    env = harbor_env(keys)
    if catalog["providers"][route["provider"]].get("login") == "claude-oauth":
        token = keys.get("VELODROME_KEY_CLAUDE_MAX") or claude_token()
        if not token:
            say(f"skip  {slug}: no Claude login with 40+ minutes left; open Claude Code to refresh it")
            return
        env["VELODROME_KEY_CLAUDE_MAX"] = token
    missing = [v[2:-1] for v in plan["env"].values() if v.startswith("${") and v[2:-1] not in env]
    if missing:
        say(f"skip  {slug}: missing {', '.join(missing)} in keys.env")
        return
    # Some Harbor adapters read os.environ instead of the agent env; each route has
    # its own Harbor process, so its settings can live there too.
    env |= {k: env[v[2:-1]] if v.startswith("${") else v for k, v in plan["env"].items()}

    JOBS.mkdir(exist_ok=True)
    say(f"start {slug}")
    with open(JOBS / f"{job_name}.log", "w") as log:
        subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
    trials = [p for p in (JOBS / job_name).glob("*/") if (p / "result.json").exists()]
    if not trials:
        say(f"fail  {slug}: Harbor left no trial, see jobs/{job_name}.log")
        return
    imported = import_trial(trials[0], route, plan, catalog, attempt)
    if imported:
        verdict = json.loads((RUNS / imported / "run.json").read_text())["verdict"]
        state = "moving" if verdict["moving"] else "drawn" if verdict["drawn"] else "no drawing"
        say(f"done  {imported}: {state}")
    else:
        say(f"fail  {slug}: {explain_failure(trials[0])}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_plan = sub.add_parser("plan")
    p_plan.add_argument("filters", nargs="*")
    p_run = sub.add_parser("run")
    p_run.add_argument("filters", nargs="*")
    p_run.add_argument("-j", "--jobs", type=int, default=3)
    p_run.add_argument("--dry-run", action="store_true")
    p_imp = sub.add_parser("import")
    p_imp.add_argument("trial", type=Path)
    p_ren = sub.add_parser("render")
    p_ren.add_argument("slugs", nargs="*")
    args = ap.parse_args()

    if args.cmd == "render":
        render(args.slugs or sorted(p.name for p in RUNS.iterdir() if (p / "run.json").exists()))
        return

    catalog = load_toml("catalog.toml")
    routes = load_routes(catalog)
    keys = load_keys()

    if args.cmd == "plan":
        for route in routes:
            if matches(route, args.filters):
                have = existing_attempts(route)
                mark = "ok " if have >= route["attempts"] else "   "
                say(f"{mark}{have}/{route['attempts']}  {route_slug(route)}  [{pick_api(route, catalog)}]")
        return

    if args.cmd == "import":
        # Job dirs are named <slug>--<timestamp>; the slug names the route and attempt.
        slug = args.trial.resolve().parent.name.rsplit("--", 1)[0]
        found = [(r, n) for r in routes for n in range(1, r["attempts"] + 1) if route_slug(r, n) == slug]
        if not found:
            sys.exit(f"no route matches {slug}")
        route, attempt = found[0]
        imported = import_trial(args.trial, route, connect(route, catalog, keys), catalog, attempt)
        say(imported or f"not a result: {explain_failure(args.trial)}")
        return

    todo = []
    for route in routes:
        if matches(route, args.filters):
            have = existing_attempts(route)
            todo += [(route, n) for n in range(have + 1, route["attempts"] + 1)]
    if not todo:
        say("nothing to run")
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=1 if args.dry_run else args.jobs) as pool:
        for future in [pool.submit(run_one, r, n, catalog, keys, args.dry_run) for r, n in todo]:
            future.result()


if __name__ == "__main__":
    main()
