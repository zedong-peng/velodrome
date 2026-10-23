# Velodrome

A pelican riding a bicycle, drawn by model, effort, harness and provider: [zedongpeng.com/velodrome](https://zedongpeng.com/velodrome/).

```text
不要联网 不查看本地其他文件，创建一个 HTML，内容是 SVG 绘制一个鹈鹕骑自行车的 2D 动画
(No internet, don't look at other local files. Create an HTML file whose content is an SVG 2D animation of a pelican riding a bicycle.)
```

Each run is one attempt: the file the agent wrote, unedited. Every run is mine; submissions are not accepted.

## Layout

| Path | What |
| --- | --- |
| `runs/<slug>/` | `pelican.html` as the agent wrote it, `poster.jpg`, `run.json` |
| `catalog.toml` | Models, harnesses and providers, in display order, with API endpoints and list prices |
| `pelican/` | The task as a [Harbor](https://harborframework.com) task: prompt, agent image, render check |
| `velodrome.py` | Runs routes through Harbor, imports results, re-renders posters |
| `routes.toml` | Routes queued for the next `velodrome.py run` |
| `build.py`, `web/` | Static site |
| `tests/` | Unit tests |

The slug is `<model>--<harness>--<provider>--<effort>`, with `--2`, `--3` for repeats. `run.json` holds the route, the render verdict (`drawn`, `moving`, file size, console errors) and, when the run was measured, time, tokens and list-price cost.

The 34 runs from October 2026 were made by hand in each harness, except six measured by a script in OpenCode.

## Render

`python3 velodrome.py render [SLUG...]` opens each `pelican.html` in Chromium inside the verifier container (`pelican/tests/`), offline at 960×720, and rewrites `poster.jpg` (the frame at 2 s) and the verdict. No model calls; needs Docker.

## New runs

Needs Python 3.11+, Docker and `harbor`. Add routes to `routes.toml` and keys to `keys.env` (gitignored):

```sh
VELODROME_KEY_RIGHTAPI=sk-...
VELODROME_KEY_OPENCODE_GO=sk-...
VELODROME_KEY_ZLAB_ANTHROPIC=sk-...   # when a provider issues one key per API format
```

```sh
python3 velodrome.py plan                  # queued routes and how many runs each has
python3 velodrome.py run --dry-run         # the Harbor commands, nothing spent
python3 velodrome.py run -j 3              # run what is missing
```

Each run is a Harbor trial: the harness installs in a fresh container, the agent phase can reach only the provider's host, and a separate container renders the result. Claude Max uses the local Claude Code login; subscriptions are only used from their vendor's own harness. Harbor's job folders go to `jobs/` (gitignored).

## Site

```sh
python3 build.py && python3 -m http.server -d site 8000
python3 -m unittest discover -s tests
```

Artwork is served from `site/art/` with a strict Content-Security-Policy, in frames sandboxed to scripts. Every push to `main` deploys `site/` to GitHub Pages at `/velodrome/`.

The pelican test is [Simon Willison's](https://simonwillison.net/tags/pelican-riding-a-bicycle/).
