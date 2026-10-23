"use strict";

const DATA = JSON.parse(document.getElementById("data").textContent);
const RUNS = DATA.runs; // already sorted by model, harness, provider, effort, attempt
const BY_SLUG = new Map(RUNS.map((r) => [r.slug, r]));
const ORDER = RUNS.map((r) => r.slug);
const ART_W = 960;

const $ = (sel) => document.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) node.append(kid);
  return node;
};

const fmtTime = (s) => {
  if (s == null) return null;
  const m = Math.floor(s / 60), r = Math.round(s % 60);
  return m ? `${m}m ${String(r).padStart(2, "0")}s` : `${r}s`;
};
const fmtTokens = (n) => (n == null ? null : n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}k` : String(n));
const fmtCost = (c) => (c == null ? null : c < 0.01 ? "<$0.01" : `$${c.toFixed(2)}`);
const fmtBytes = (b) => (b == null ? null : b >= 1024 ? `${(b / 1024).toFixed(1)} KB` : `${b} B`);
const kindChip = (kind) => el("span", { class: `kind ${kind}`, text: kind });

// ------------------------------------------------------------------ cards

const canHover = matchMedia("(hover: hover) and (pointer: fine)").matches;

function liveFrame(slug, box) {
  const frame = el("iframe", { src: `art/${slug}.html`, sandbox: "allow-scripts", tabindex: "-1", title: "Live artwork" });
  const fit = () => (frame.style.transform = `scale(${box.clientWidth / ART_W})`);
  fit();
  frame._ro = new ResizeObserver(fit);
  frame._ro.observe(box);
  return frame;
}

function card(r) {
  const shot = r.drawn
    ? el("div", { class: "shot" },
        el("img", { src: `posters/${r.slug}.jpg`, loading: "lazy", decoding: "async", alt: `Pelican by ${r.modelName}, ${r.harnessName}, ${r.effort}, ${r.providerName}` }),
        r.moving ? null : el("span", { class: "badge", text: "still" }))
    : el("div", { class: "shot empty", text: "No drawing" });
  const node = el("button", { class: "card", type: "button", "data-slug": r.slug },
    shot,
    el("div", { class: "label" },
      el("div", { class: "primary" }, r.harnessName, el("span", { class: "effort", text: r.effort }), r.attempt > 1 ? el("small", { text: `#${r.attempt}` }) : null),
      el("div", { class: "secondary" }, r.providerName, " ", kindChip(r.providerKind))));
  node.addEventListener("click", () => openDetail(r.slug));
  if (r.drawn && canHover) {
    let timer = null, frame = null;
    node.addEventListener("pointerenter", () => {
      timer = setTimeout(() => shot.append((frame = liveFrame(r.slug, shot))), 220);
    });
    node.addEventListener("pointerleave", () => {
      clearTimeout(timer);
      if (frame) {
        frame._ro.disconnect();
        frame.remove();
        frame = null;
      }
    });
  }
  return node;
}

function renderBoard() {
  const board = $("#board");
  let group = null, current = null;
  for (const r of RUNS) {
    if (r.model !== current) {
      current = r.model;
      const count = RUNS.filter((x) => x.model === r.model).length;
      group = el("div", { class: "grid" });
      // Small groups sit side by side; big ones take the full width.
      const wide = count > 4;
      if (!wide) group.style.setProperty("--n", count);
      board.append(el("section", { class: wide ? "group wide" : count === 1 ? "group solo" : "group" },
        el("h2", {}, r.modelName, el("small", { text: `${r.lab} · ${count} ${count === 1 ? "run" : "runs"}` })),
        group));
    }
    group.append(card(r));
  }
}

// ----------------------------------------------------------------- detail

const dialog = $("#detail");
let detailFrame = null;

function row(term, ...value) {
  const parts = value.flat().filter((v) => v !== null && v !== undefined && v !== "");
  return parts.length ? [el("dt", { text: term }), el("dd", {}, parts)] : [];
}

function openDetail(slug) {
  const r = BY_SLUG.get(slug);
  if (!r) return;
  $("#detail-kicker").textContent = r.lab;
  $("#detail-title").textContent = r.modelName;
  $("#detail-route").replaceChildren(
    ...row("Effort", r.effort),
    ...row("Harness", r.harnessName, r.harnessVersion ? [" ", el("small", { text: r.harnessVersion })] : null),
    ...row("Provider", r.providerName, " ", kindChip(r.providerKind)),
    ...row("Attempt", r.attempt > 1 ? `#${r.attempt}` : null),
  );
  $("#detail-receipt").replaceChildren(
    ...row("Date", r.date),
    ...row("Time", fmtTime(r.seconds)),
    ...row("Steps", r.steps != null ? String(r.steps) : null),
    ...row("Input tokens", fmtTokens(r.tokensIn)),
    ...row("Output tokens", fmtTokens(r.tokensOut)),
    ...row("List cost", fmtCost(r.cost)),
    ...row("File", r.file ? `${r.file} · ${fmtBytes(r.bytes)}` : "none"),
    ...row("Moving", r.drawn ? (r.moving ? "yes" : "no") : null),
  );
  const blob = `${DATA.repo}/blob/main/runs/${r.slug}`;
  $("#detail-links").replaceChildren(
    r.file ? el("a", { href: `art/${r.slug}.html`, target: "_blank", rel: "noopener", text: "Full page ↗" }) : null,
    r.file ? el("a", { href: `${blob}/pelican.html`, text: "Source" }) : null,
    el("a", { href: `${blob}/run.json`, text: "Record" }),
  );
  const box = $("#detail-frame");
  if (detailFrame) detailFrame._ro.disconnect();
  box.replaceChildren((detailFrame = r.file ? liveFrame(r.slug, box) : null) || el("div", { class: "empty", text: "No drawing" }));
  if (!dialog.open) dialog.showModal();
  history.replaceState(null, "", `#run=${slug}`);
}

function closeDetail() {
  if (detailFrame) detailFrame._ro.disconnect();
  $("#detail-frame").replaceChildren();
  detailFrame = null;
  if (dialog.open) dialog.close();
  history.replaceState(null, "", location.pathname + location.search);
}

function step(delta) {
  const slug = new URLSearchParams(location.hash.slice(1)).get("run");
  const i = ORDER.indexOf(slug);
  if (i >= 0) openDetail(ORDER[(i + delta + ORDER.length) % ORDER.length]);
}

dialog.addEventListener("click", (e) => {
  if (e.target === dialog || e.target.closest("[data-close]")) closeDetail();
  const b = e.target.closest("[data-step]");
  if (b) step(Number(b.dataset.step));
});
dialog.addEventListener("cancel", (e) => {
  e.preventDefault();
  closeDetail();
});
document.addEventListener("keydown", (e) => {
  if (!dialog.open) return;
  if (e.key === "ArrowRight") step(1);
  if (e.key === "ArrowLeft") step(-1);
});

// ------------------------------------------------------------------- boot

const count = (k) => new Set(RUNS.map((r) => r[k])).size;
$("#tally").textContent = `${RUNS.length} runs · ${count("model")} models · ${count("harness")} harnesses · ${count("provider")} providers`;
for (const id of ["#repo-link", "#repo-link-2"]) $(id).href = DATA.repo;
renderBoard();
const start = new URLSearchParams(location.hash.slice(1)).get("run");
if (start && BY_SLUG.has(start)) openDetail(start);
