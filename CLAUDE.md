# Working on Velodrome

The user's calls (10-23, 10-24), with their reasons:

- One clean site at zedongpeng.com/velodrome/, deployed by `site.yml` on every push to main. No redirects, mentions or links to the old sites: the user wants one site, not a trail of earlier versions.
- A benchmark gallery, not a post: people come to see how a newly released model does, in which harness, from which provider. No behavior scores: the automatic ones all pass, and the ones that would separate models are one judge's opinion on one attempt. The reader judges by looking.
- Main board: one card per model, newest release first (`released` in `catalog.toml`), the newest leading large with its receipt (time, steps, output tokens, list cost). A run with no file says how many output tokens it spent. Every other run (other agents or providers) goes in the harness section below, a separate comparison that doesn't change the main board. Relay runs stay in `runs/` but off the site.
- One attempt per route, at the highest effort available. Providers show only in the poster sheet: restraint is more elegant.
- The prompt is ours, not the bare-model one: a coding agent, offline, writes an SVG animation in an HTML file. Shown in Chinese as given and in English.
- The page takes the theme of zedongpeng.com (user, 11-13): it links the site's stylesheet and loads its header, and sets no fonts or base colors of its own; light only. `web/style.css` holds only the gallery's layout. See Theme in the site repo's `AGENTS.md`.
- Runs cost money: show the user the run table with a cost estimate first. Standing approval (10-24): one run for each new model through a subscription route.
- Harbor scrubs the values of secret-looking agent settings out of trial files, so keep short literal values out of `--ae`.
