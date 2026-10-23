"""Route plumbing and site build. Standard library only: python3 -m unittest discover -s tests"""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build  # noqa: E402
import velodrome  # noqa: E402

CATALOG = velodrome.load_toml("catalog.toml")
QUEUED = velodrome.load_routes(CATALOG)
# Every runnable (harness, provider) pairing the catalog allows, as sample routes.
SAMPLES = [
    {"harness": "claude-code", "provider": "claude-max", "model": "claude-opus-5-5", "effort": "max"},
    {"harness": "claude-code", "provider": "opencode-go", "model": "claude-haiku-5-5", "effort": "default"},
    {"harness": "codex", "provider": "rightapi", "model": "claude-opus-5-5", "effort": "high"},
    {"harness": "opencode", "provider": "opencode-go", "model": "kimi-k3", "effort": "default"},
    {"harness": "opencode", "provider": "rightapi", "model": "claude-opus-5-5", "effort": "default"},
    {"harness": "pi", "provider": "deepseek-api", "model": "deepseek-v4.1-flash", "effort": "high"},
    {"harness": "goose", "provider": "zhinaoapi", "model": "gpt-6-astra", "effort": "default"},
    {"harness": "aider", "provider": "zhinaoapi", "model": "gpt-6-astra", "effort": "default"},
    {"harness": "kimi-cli", "provider": "rightapi", "model": "claude-opus-5-5", "effort": "default"},
    {"harness": "hermes", "provider": "rightapi", "model": "claude-opus-5-5", "effort": "default"},
    {"harness": "openhands", "provider": "rightapi", "model": "claude-opus-5-5", "effort": "default"},
    {"harness": "gemini-cli", "provider": "opencode-go", "model": "gemini-4-argon-contributor", "effort": "default"},
]
ROUTES = QUEUED + SAMPLES


class Routes(unittest.TestCase):
    def test_every_route_resolves_to_a_host(self):
        for route in ROUTES:
            plan = velodrome.connect(route, CATALOG, {})
            self.assertTrue(plan["hosts"], velodrome.route_slug(route))
            self.assertIn(plan["api"], CATALOG["harnesses"][route["harness"]]["apis"])

    def test_keys_only_travel_as_templates(self):
        for route in ROUTES:
            plan = velodrome.connect(route, CATALOG, {"VELODROME_KEY_RIGHTAPI": "sk-secret"})
            values = list(plan["env"].values()) + [json.dumps(plan["kwargs"])]
            self.assertFalse(any("sk-secret" in v for v in values), velodrome.route_slug(route))
            for name, value in plan["env"].items():
                if name.endswith(("_KEY", "_TOKEN")):
                    self.assertRegex(value, r"^\$\{VELODROME_KEY_[A-Z_]+\}$", name)

    def test_slugs_are_unique(self):
        slugs = [velodrome.route_slug(r) for r in QUEUED]
        self.assertEqual(len(slugs), len(set(slugs)))

    def test_subscriptions_stay_in_their_own_harness(self):
        for route in ROUTES:
            allowed = CATALOG["providers"][route["provider"]].get("harnesses")
            if allowed:
                self.assertIn(route["harness"], allowed)

    def test_harbor_gets_a_clean_environment(self):
        env = velodrome.harbor_env({"VELODROME_KEY_X": "1", "OPENAI_API_KEY": "leak"})
        self.assertEqual(env["VELODROME_KEY_X"], "1")
        self.assertNotIn("OPENAI_API_KEY", env)

    def test_list_cost(self):
        price = {"input": 4, "output": 20, "cache_read": 0.2}
        cost = velodrome.list_cost({"input": 1_000_000, "cache": 500_000, "output": 100_000}, price)
        self.assertAlmostEqual(cost, 0.5 * 4 + 0.5 * 0.2 + 0.1 * 20)
        self.assertIsNone(velodrome.list_cost({"input": None}, price))


    def test_harness_version_is_passed_on(self):
        route = {"harness": "opencode", "provider": "opencode-go", "model": "kimi-k3", "effort": "max", "version": "1.19.3"}
        plan = velodrome.connect(route, CATALOG, {})
        self.assertEqual(plan["kwargs"], {"variant": "max", "version": "1.19.3"})
        self.assertIn("version=1.19.3", velodrome.harbor_command(route, plan, "job"))

    def test_opencode_reasoning_counts_every_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "opencode.txt"
            step = lambda n: json.dumps({"type": "step_finish", "part": {"tokens": {"output": 5, "reasoning": n}}})
            log.write_text("\n".join([step(100), "not json", json.dumps({"type": "text"}), step(23)]))
            self.assertEqual(velodrome.opencode_reasoning(log), 123)
            self.assertEqual(velodrome.opencode_reasoning(Path(tmp) / "missing.txt"), 0)


class Build(unittest.TestCase):
    def test_csp_goes_first_in_head(self):
        out = build.protect("<!doctype html><html><head><title>x</title></head><body><svg></svg></body></html>")
        self.assertIn("<head><meta http-equiv=\"Content-Security-Policy\"", out)
        self.assertTrue(build.protect("<svg></svg>").startswith("<meta http-equiv"))

    def test_build_writes_the_site(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for name in ("build.py", "velodrome.py", "catalog.toml", "routes.toml"):
                shutil.copy(ROOT / name, tmp / name)
            shutil.copytree(ROOT / "web", tmp / "web")
            (tmp / "pelican").mkdir()
            shutil.copy(ROOT / "pelican" / "instruction.md", tmp / "pelican" / "instruction.md")
            route = {"model": "kimi-k3", "harness": "opencode", "provider": "opencode-go", "effort": "default"}
            run = tmp / "runs" / velodrome.route_slug(route)
            run.mkdir(parents=True)
            (run / "pelican.html").write_text("<html><head></head><body><svg></svg><script>1</script></body></html>")
            (run / "poster.jpg").write_bytes(b"\xff\xd8\xff")
            record = {"route": route | {"api": "chat"}, "attempt": 1, "started_at": "2026-10-23T00:00:00Z",
                      "verdict": {"file": "pelican.html", "drawn": True, "moving": True}}
            (run / "run.json").write_text(json.dumps(record))
            subprocess.run([sys.executable, "build.py"], cwd=tmp, check=True, capture_output=True)
            page = (tmp / "site" / "index.html").read_text()
            self.assertIn(run.name, page)
            self.assertNotIn("/* DATA */", page)
            self.assertIn("Content-Security-Policy", (tmp / "site" / "art" / f"{run.name}.html").read_text())
            self.assertTrue((tmp / "site" / "posters" / f"{run.name}.jpg").exists())

    def test_build_rejects_a_mismatched_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for name in ("build.py", "velodrome.py", "catalog.toml", "routes.toml"):
                shutil.copy(ROOT / name, tmp / name)
            shutil.copytree(ROOT / "web", tmp / "web")
            run = tmp / "runs" / "wrong-name"
            run.mkdir(parents=True)
            route = {"model": "kimi-k3", "harness": "opencode", "provider": "opencode-go", "effort": "default"}
            (run / "run.json").write_text(json.dumps({"route": route, "attempt": 1, "verdict": {}}))
            done = subprocess.run([sys.executable, "build.py"], cwd=tmp, capture_output=True, text=True)
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("does not match", done.stderr)


if __name__ == "__main__":
    unittest.main()
