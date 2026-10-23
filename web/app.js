"use strict";

const DATA = JSON.parse(document.getElementById("data").textContent);
const RUNS = DATA.runs; // official and subscription runs, sorted by model, harness, provider, effort, attempt
const MODELS = DATA.models; // newest release first, each with the run the page leads with
const BY_SLUG = new Map(RUNS.map((r) => [r.slug, r]));
const EFFORT_RANK = ["default", "none", "minimal", "low", "medium", "high", "xhigh", "max"];
// Arrow keys step through runs in page order: the lead runs, then each model's other runs.
const ORDER = [...MODELS.map((m) => m.lead), ...MODELS.flatMap((m) => m.runs.filter((s) => s !== m.lead))];
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
const fmtDate = (d) => (d ? new Date(`${d}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }) : null);
const route = (r) => `${r.harnessName} · ${r.providerName} · ${r.effort}`;

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

function shot(r) {
  return r.drawn
    ? el("div", { class: "shot" },
        el("img", { src: `posters/${r.slug}.jpg`, loading: "lazy", decoding: "async", alt: `Pelican by ${r.modelName}, ${route(r)}` }),
        r.moving ? null : el("span", { class: "badge", text: "still" }))
    : el("div", { class: "shot empty", text: "No drawing" });
}

// A card is a poster that plays the real file on hover and opens the detail sheet.
function card(r, ...label) {
  const s = shot(r);
  const node = el("button", { class: "card", type: "button", "data-slug": r.slug }, s, el("div", { class: "label" }, label));
  node.addEventListener("click", () => openDetail(r.slug));
  if (r.drawn && canHover) {
    let timer = null, frame = null;
    node.addEventListener("pointerenter", () => {
      timer = setTimeout(() => s.append((frame = liveFrame(r.slug, s))), 220);
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

function renderLatest() {
  const grid = el("div", { class: "grid lead" });
  for (const m of MODELS) {
    const r = BY_SLUG.get(m.lead);
    grid.append(card(r,
      el("div", { class: "primary" }, el("span", { text: m.name }), el("time", { datetime: m.released, text: fmtDate(m.released) })),
      el("div", { class: "secondary", text: route(r) })));
  }
  $("#latest").append(grid);
}

// One table per model with more than one run: harnesses down, providers across, efforts inside a cell.
function renderMatrices() {
  const box = $("#matrices");
  for (const m of MODELS.filter((m) => m.runs.length > 1)) {
    const runs = m.runs.map((s) => BY_SLUG.get(s));
    const harnesses = [...new Set(runs.map((r) => r.harness))];
    const providers = [...new Set(runs.map((r) => r.provider))];
    const name = (k, field) => runs.find((r) => r[field] === k)[`${field}Name`];
    const table = el("table", { class: "matrix" },
      el("thead", {}, el("tr", {}, el("th", {}), providers.map((p) => el("th", { scope: "col", text: name(p, "provider") })))),
      el("tbody", {}, harnesses.map((h) => el("tr", {},
        el("th", { scope: "row", text: name(h, "harness") }),
        providers.map((p) => {
          const here = runs.filter((r) => r.harness === h && r.provider === p)
            .sort((a, b) => EFFORT_RANK.indexOf(b.effort) - EFFORT_RANK.indexOf(a.effort) || a.attempt - b.attempt);
          return el("td", {}, here.length
            ? el("div", { class: "cell" }, here.map((r) => card(r, el("div", { class: "secondary" }, r.effort, r.attempt > 1 ? ` · attempt ${r.attempt}` : ""))))
            : el("span", { class: "none", text: "—" }));
        })))));
    box.append(el("section", { class: "model" }, el("h3", { text: m.name }), el("div", { class: "scroll" }, table)));
  }
}

// ----------------------------------------------------------------- detail

const dialog = $("#detail");
let detailFrame = null;

// Every row is always there, with a dash when the value is unknown, so the
// sheet keeps its layout while stepping through runs.
function row(term, ...value) {
  const parts = value.flat().filter((v) => v !== null && v !== undefined && v !== "");
  return [el("dt", { text: term }), parts.length ? el("dd", {}, parts) : el("dd", { class: "none", text: "—" })];
}

function openDetail(slug) {
  const r = BY_SLUG.get(slug);
  if (!r) return;
  const released = fmtDate(r.released);
  $("#detail-kicker").textContent = [r.lab, released ? `released ${released}` : null, r.attempt > 1 ? `attempt ${r.attempt}` : null].filter(Boolean).join(" · ");
  $("#detail-title").textContent = r.modelName;
  $("#detail-route").replaceChildren(
    ...row("Effort", r.effort),
    ...row("Harness", r.harnessName, r.harnessVersion ? [" ", el("small", { text: r.harnessVersion })] : null),
    ...row("Provider", r.providerName),
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

for (const id of ["#repo-link", "#repo-link-2"]) $(id).href = DATA.repo;
renderLatest();
renderMatrices();
const start = new URLSearchParams(location.hash.slice(1)).get("run");
if (start && BY_SLUG.has(start)) openDetail(start);
