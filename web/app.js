// Git Séance front end: no framework, no build step. Everything that came out
// of a repository or a model is untrusted, so it goes through esc() first.

const $ = (sel, el = document) => el.querySelector(sel);
const app = $("#app");
const chamber = $("#chamber");

const state = {
  config: null,
  repoId: null,
  yard: null,
  filter: { q: "", kinds: new Set(), sort: "recent", year: null },
  shown: 120,
  ghost: null,
  chat: [],
  busy: false,
};

const KIND_GLYPH = { code: "</>", test: "✓", docs: "¶", config: "⚙", asset: "◆", vendor: "▣" };
const KIND_LABEL = { code: "Code", test: "Tests", docs: "Docs", config: "Config", asset: "Assets", vendor: "Vendored" };
const OPENING = "Introduce yourself to the visitor who summoned you: who you were, what you did, and how you died.";
const QUESTIONS = [
  "What did you do while you lived?",
  "Why were you deleted?",
  "Who shaped you most?",
  "Who replaced you?",
  "Do you have regrets?",
  "Should we bring you back?",
];

// ------------------------------------------------------------------ utils

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
const fmtDate = (ts) => (ts ? new Date(ts * 1000).toISOString().slice(0, 10) : "unknown");
const year = (ts) => (ts ? new Date(ts * 1000).getUTCFullYear() : "?");
const days = (g) => (g.born_at ? Math.max(0, Math.floor((g.died_at - g.born_at) / 86400)) : null);
const plural = (n, w) => `${n.toLocaleString()} ${w}${n === 1 ? "" : "s"}`;

function lifespan(d) {
  if (d == null) return "birth unrecorded";
  if (d < 1) return "lived less than a day";
  if (d < 60) return `lived ${plural(d, "day")}`;
  if (d < 730) return `lived ${Math.round(d / 30.4)} months`;
  return `lived ${(d / 365.25).toFixed(1)} years`;
}

function hashCode(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `request failed (${res.status})`);
  return data;
}

let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
}

async function copy(text, done) {
  try { await navigator.clipboard.writeText(text); toast(done); }
  catch { toast("Could not reach the clipboard"); }
}

function store(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}
function save(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ }
}
const marigoldKey = (repo, id) => `seance:marigold:${repo}:${id}`;
const marigolds = (repo, id) => Math.min(5, store(marigoldKey(repo, id), 0));
const marigoldSvg = (n) => Array.from({ length: n }, () => '<svg aria-hidden="true"><use href="#marigold"/></svg>').join("");

function candle(cls = "") {
  return `<span class="candle ${cls}" aria-hidden="true"><span class="flame"></span><span class="wick"></span><span class="wax"></span></span>`;
}

function prettyModel(m) {
  if (!m) return "no model";
  const g = /gemma-?(\d+)/i.exec(m);
  return g ? `Gemma ${g[1]}` : m;
}

// ------------------------------------------------------------ papel picado

const PICADO_COLORS = ["#e83f8a", "#ff9f1c", "#2ec4b6", "#9d6bff", "#ffd166", "#ff5c5c"];

function cutouts(kind) {
  if (kind === 0) { // calavera
    return `<circle cx="29" cy="25" r="11"/><rect x="22" y="31" width="14" height="9" rx="2"/>
      <circle cx="25" cy="24" r="3" fill="white"/><circle cx="33" cy="24" r="3" fill="white"/>
      <path d="M29 28l-2 3h4z" fill="white"/><rect x="26" y="34" width="1.5" height="5" fill="white"/><rect x="30.5" y="34" width="1.5" height="5" fill="white"/>`;
  }
  if (kind === 1) { // flower
    let p = '<circle cx="29" cy="28" r="4"/>';
    for (let i = 0; i < 8; i++) {
      const a = (i * Math.PI) / 4;
      p += `<ellipse cx="${29 + Math.cos(a) * 9}" cy="${28 + Math.sin(a) * 9}" rx="4" ry="2.4" transform="rotate(${i * 45} ${29 + Math.cos(a) * 9} ${28 + Math.sin(a) * 9})"/>`;
    }
    return p;
  }
  if (kind === 2) { // diamonds
    let p = "";
    for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++) {
      const x = 15 + c * 14, y = 14 + r * 13;
      p += `<path d="M${x} ${y - 5}l5 5-5 5-5-5z"/>`;
    }
    return p;
  }
  let p = '<circle cx="29" cy="28" r="7"/>'; // sun
  for (let i = 0; i < 12; i++) {
    const a = (i * Math.PI) / 6;
    p += `<path d="M${29 + Math.cos(a) * 10} ${28 + Math.sin(a) * 10}L${29 + Math.cos(a + 0.12) * 17} ${28 + Math.sin(a + 0.12) * 17}L${29 + Math.cos(a - 0.12) * 17} ${28 + Math.sin(a - 0.12) * 17}z"/>`;
  }
  return p;
}

function buildPicado() {
  const host = $("#picado");
  const w = window.innerWidth;
  const n = Math.ceil(w / 66) + 1;
  let html = `<svg class="string" viewBox="0 0 ${w} 30" preserveAspectRatio="none"><path d="M0 8 Q ${w / 2} 22 ${w} 8" stroke="rgba(239,230,216,.35)" stroke-width="1.2" fill="none"/></svg>`;
  for (let i = 0; i < n; i++) {
    const color = PICADO_COLORS[i % PICADO_COLORS.length];
    const x = i * 66 - 8;
    const sag = Math.sin((x / w) * Math.PI) * 12;
    let dots = "";
    for (let d = 0; d < 6; d++) dots += `<circle cx="${7 + d * 8.8}" cy="54" r="1.6"/>`;
    html += `<svg class="flag" viewBox="0 0 58 66" data-x="${x}" data-y="${sag}" data-delay="${(i % 7) * -0.6}">
      <defs><mask id="pm${i}"><rect width="58" height="66" fill="white"/><g fill="black">${cutouts(i % 4)}${dots}</g></mask></defs>
      <path d="M0 0H58V58l-4.8 8-4.9-8-4.8 8-4.9-8-4.8 8-4.9-8-4.8 8-4.9-8-4.8 8-4.9-8-4.8 8L0 58z" fill="${color}" mask="url(#pm${i})" opacity=".92"/>
    </svg>`;
  }
  host.innerHTML = html;
  host.querySelectorAll(".flag").forEach((f) => {
    f.style.left = `${f.dataset.x}px`;
    f.style.top = `${6 + Number(f.dataset.y)}px`;
    f.style.animationDelay = `${f.dataset.delay}s`;
  });
}

// ------------------------------------------------------------------ boot

async function boot() {
  buildPicado();
  let t;
  window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(buildPicado, 200); });
  const now = new Date();
  const m = now.getMonth(), d = now.getDate();
  if ((m === 9 && d === 31) || (m === 10 && d <= 2)) $("#veil").hidden = false;

  try {
    state.config = await api("/api/config");
  } catch {
    state.config = { llm_ready: false, model: "", demo_repos: [], local_repos: [], allow_remote: false };
  }
  const badge = $("#model-badge");
  badge.innerHTML = state.config.llm_ready
    ? `<span class="dot"></span>${esc(prettyModel(state.config.model))} · open weights`
    : `<span class="dot"></span>records only · no model`;
  badge.classList.toggle("off", !state.config.llm_ready);
  badge.title = state.config.llm_ready ? `Spirits speak through ${state.config.model}` : "Set SEANCE_LLM_API_KEY to let the dead speak";

  window.addEventListener("hashchange", route);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !chamber.hidden) location.hash = `#/r/${state.repoId}`;
  });
  route();
}

async function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "r" && /^[a-z0-9._-]+$/i.test(parts[1] || "")) {
    if (state.repoId !== parts[1] || !state.yard) await showYard(parts[1]);
    if (parts[2] && /^[0-9a-f]{12}$/.test(parts[2])) openChamber(parts[2]);
    else closeChamber();
  } else {
    closeChamber();
    renderHome();
  }
}

// ------------------------------------------------------------------ home

function renderHome() {
  state.repoId = null;
  state.yard = null;
  document.title = "Git Séance";
  const c = state.config;
  const local = c.local_repos.map((r) => `<a class="chip" href="#/r/${esc(r.id)}">⚰ ${esc(r.label)}</a>`).join("");
  const demos = c.demo_repos.map((r) => `<button class="chip" data-repo="${esc(r)}">${esc(r)}</button>`).join("");
  app.innerHTML = `
    <section class="hero">
      <h1>Git <span class="accent">Séance</span></h1>
      <p class="tagline">Every deleted file is a ghost. Light a candle, walk the graveyard of a repository, and summon one to ask why it died.</p>
      ${c.allow_remote ? `
      <form class="summon-form" id="open-form" autocomplete="off">
        <input id="open-input" name="repo" placeholder="owner/repo or a GitHub URL" aria-label="GitHub repository" maxlength="200">
        <button class="btn btn-fire" type="submit">${candle()} Open the graveyard</button>
      </form>` : ""}
      <div class="err" id="open-err" role="alert"></div>
      ${local ? `<div class="chips"><span class="chip-label">Your repositories</span>${local}</div>` : ""}
      ${demos ? `<div class="chips"><span class="chip-label">Haunted repositories</span>${demos}</div>` : ""}
    </section>
    <section class="rites">
      <div class="rite"><div class="num">I</div><h3>Find the dead</h3><p>Git never forgets. We replay a repository's history, follow every rename, and find each file that was deleted and never came back.</p></div>
      <div class="rite"><div class="num">II</div><h3>Read their stones</h3><p>Each ghost gets a headstone: who made it, how long it lived, which commit killed it, who was buried alongside, and who took its place.</p></div>
      <div class="rite"><div class="num">III</div><h3>Summon one</h3><p>Ask it anything. It answers in its own voice through an open-weight model, from git's records only, citing the commits it remembers.</p></div>
    </section>
    <p class="promise"><strong>Open weights, honest ghosts.</strong> The model runs wherever you point it, including your own machine, so your private history never has to leave it. Ghosts only know what git knows: every cited commit is checked, and secrets found in the dead are redacted before anyone, human or model, sees them.</p>`;
  const form = $("#open-form");
  if (form) form.addEventListener("submit", (e) => { e.preventDefault(); openRepo($("#open-input").value); });
  app.querySelectorAll("[data-repo]").forEach((b) => b.addEventListener("click", () => openRepo(b.dataset.repo)));
}

async function openRepo(text) {
  text = (text || "").trim();
  if (!text) return;
  const err = $("#open-err");
  if (err) err.textContent = "";
  renderRitual("Opening the crypt…", `Cloning ${text} and reading its whole history. Big graveyards take a moment.`);
  try {
    const repo = await api("/api/open", { repo: text });
    location.hash = `#/r/${repo.id}`;
  } catch (e) {
    renderHome();
    $("#open-err").textContent = e.message;
    const input = $("#open-input");
    if (input) input.value = text;
  }
}

function renderRitual(title, sub) {
  app.innerHTML = `<div class="ritual">${candle("big")}${candle("big")}${candle("big")}<p>${esc(title)}</p><small>${esc(sub || "")}</small></div>`;
}

// ------------------------------------------------------------- graveyard

async function showYard(repoId) {
  renderRitual("Counting the departed…", "Replaying every add, rename and deletion in the history.");
  try {
    state.yard = await api(`/api/graveyard?repo=${encodeURIComponent(repoId)}`);
  } catch (e) {
    state.yard = null;
    state.repoId = null;
    app.innerHTML = `<div class="ritual"><p>${esc(e.message)}</p><small><a href="#/">Back to the gates</a></small></div>`;
    return;
  }
  state.repoId = repoId;
  state.filter = { q: "", kinds: new Set(), sort: "recent", year: null };
  state.shown = 120;
  renderYard();
}

function ghostById(id) {
  return state.yard?.ghosts.find((g) => g.id === id);
}

function renderYard() {
  const { repo, stats, ghosts } = state.yard;
  document.title = `${repo.label} · Git Séance`;
  const eldest = ghostById(stats.eldest);
  const ext = stats.extinctions[0];
  const reaper = stats.reapers[0];
  const kinds = Object.entries(stats.kinds).sort((a, b) => b[1] - a[1]);
  app.innerHTML = `
    <section class="yard-head">
      <div>
        <h2>The graveyard of ${esc(repo.label)}</h2>
        <div class="sub">${repo.web_url ? `<a href="${esc(repo.web_url)}" target="_blank" rel="noopener">${esc(repo.web_url.replace("https://", ""))}</a> · ` : ""}HEAD ${esc(repo.head.slice(0, 7))}${stats.scan_ms ? ` · history read in ${(stats.scan_ms / 1000).toFixed(1)}s` : ""}</div>
      </div>
      <a class="btn btn-ghost" href="#/">← Another graveyard</a>
    </section>
    <section class="stats">
      <div class="stat hot"><div class="k">Souls departed</div><div class="v">${stats.souls.toLocaleString()}</div><div class="d">${stats.living.toLocaleString()} files still living</div></div>
      <div class="stat"><div class="k">Eldest ghost</div><div class="v">${eldest ? `${(stats.eldest_days / 365.25).toFixed(1)} yrs` : "—"}</div>
        <div class="d">${eldest ? `<button data-open="${eldest.id}">${esc(eldest.name)}</button>` : "no births recorded"}</div></div>
      <div class="stat blood"><div class="k">Deadliest commit</div><div class="v">${ext ? `${ext.count} souls` : "—"}</div>
        <div class="d" title="${esc(ext?.subject)}">${ext ? `<span class="sha">${esc(ext.sha.slice(0, 7))}</span> ${esc(ext.subject)}` : ""}</div></div>
      <div class="stat"><div class="k">The reaper</div><div class="v">${reaper ? reaper.count.toLocaleString() : "—"}</div>
        <div class="d">${reaper ? `deletions by ${esc(reaper.name)}` : ""}</div></div>
    </section>
    ${mortalityChart(stats.deaths_by_year)}
    <section class="filters">
      <label class="search"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
        <input id="q" placeholder="Search the dead by name or path" aria-label="Search ghosts"></label>
      <select class="sort" id="sort" aria-label="Sort ghosts">
        <option value="recent">Most recently departed</option>
        <option value="ancient">Longest dead</option>
        <option value="longest">Longest lived</option>
        <option value="lives">Died most often</option>
        <option value="mass">Mass graves first</option>
      </select>
      <div class="chips" id="kinds">${kinds.map(([k, n]) => `<button class="chip" data-kind="${esc(k)}" aria-pressed="false">${esc(KIND_LABEL[k] || k)}<span class="count">${n}</span></button>`).join("")}</div>
    </section>
    <div class="showing" id="showing"></div>
    <section class="yard" id="yard" aria-label="Tombstones"></section>
    <div class="more" id="more"></div>`;

  app.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => goGhost(b.dataset.open)));
  $("#q").addEventListener("input", (e) => { state.filter.q = e.target.value.toLowerCase(); state.shown = 120; renderStones(); });
  $("#sort").addEventListener("change", (e) => { state.filter.sort = e.target.value; renderStones(); });
  app.querySelectorAll("[data-kind]").forEach((b) => b.addEventListener("click", () => {
    const k = b.dataset.kind;
    state.filter.kinds.has(k) ? state.filter.kinds.delete(k) : state.filter.kinds.add(k);
    b.setAttribute("aria-pressed", state.filter.kinds.has(k));
    state.shown = 120;
    renderStones();
  }));
  app.querySelectorAll(".mortality .bar").forEach((bar) => bar.addEventListener("click", () => {
    const y = Number(bar.dataset.year);
    state.filter.year = state.filter.year === y ? null : y;
    app.querySelectorAll(".mortality .bar").forEach((b) => b.classList.toggle("active", Number(b.dataset.year) === state.filter.year));
    state.shown = 120;
    renderStones();
  }));
  renderStones();
}

function mortalityChart(byYear) {
  const years = Object.keys(byYear).map(Number);
  if (!years.length) return "";
  const first = Math.min(...years), last = Math.max(...years);
  const all = [];
  for (let y = first; y <= last; y++) all.push([y, byYear[y] || 0]);
  const max = Math.max(...all.map(([, n]) => n));
  const W = 1000, H = 110, pad = 18, bw = W / all.length;
  const bars = all.map(([y, n], i) => {
    const h = n ? Math.max(3, ((H - pad - 6) * n) / max) : 0;
    const label = all.length <= 16 || i % Math.ceil(all.length / 12) === 0 ? `<text x="${i * bw + bw / 2}" y="${H - 2}" text-anchor="middle">${y}</text>` : "";
    return `<rect class="bar" data-year="${y}" x="${i * bw + bw * 0.18}" y="${H - pad - h}" width="${bw * 0.64}" height="${h}" rx="3"><title>${y}: ${plural(n, "death")}</title></rect>${label}`;
  }).join("");
  return `<section class="mortality"><div class="k">Deaths per year · click a year to walk only its graves</div>
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Deleted files per year">
      <defs><linearGradient id="barGrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffc15e"/><stop offset="1" stop-color="#e83f8a"/></linearGradient></defs>
      ${bars}</svg></section>`;
}

function filtered() {
  const { q, kinds, sort, year: y } = state.filter;
  let list = state.yard.ghosts.filter((g) =>
    (!q || g.path.toLowerCase().includes(q)) && (!kinds.size || kinds.has(g.kind)) && (!y || year(g.died_at) === y));
  const by = {
    recent: (a, b) => b.died_at - a.died_at,
    ancient: (a, b) => a.died_at - b.died_at,
    longest: (a, b) => (days(b) ?? -1) - (days(a) ?? -1),
    lives: (a, b) => b.lives - a.lives || b.died_at - a.died_at,
    mass: (a, b) => b.siblings - a.siblings || b.died_at - a.died_at,
  }[sort];
  return list.slice().sort(by);
}

function stone(g) {
  const d = days(g);
  const shape = hashCode(g.id) % 3;
  const h = 196 + Math.min(54, Math.log2((d ?? 0) + 1) * 4.4);
  const moss = Math.min(1, (Date.now() / 1000 - g.died_at) / (86400 * 365 * 8));
  const flowers = marigolds(state.repoId, g.id);
  return `<div class="plot">
    <div class="wisp"></div>
    ${g.lives > 1 ? `<span class="lives" title="This path died ${g.lives} times">×${g.lives}</span>` : ""}
    <button class="stone s${shape}" style="--h:${h.toFixed(0)}px;--moss:${moss.toFixed(2)}" data-ghost="${g.id}" aria-label="${esc(g.path)}, ${esc(lifespan(d))}, deleted ${fmtDate(g.died_at)}">
      ${shape === 1 ? '<span class="crossbar"></span>' : ""}
      <span class="glyph">${esc(KIND_GLYPH[g.kind] || "")}</span>
      <span class="name">${esc(g.name)}</span>
      <span class="years">${g.born_at ? year(g.born_at) : "?"} – ${year(g.died_at)}</span>
      <span class="ep">${esc(g.epitaph || lifespan(d))}</span>
      ${g.dir ? `<span class="dir">${esc(g.dir)}/</span>` : ""}
      <span class="rip">R · I · P</span>
    </button>
    <div class="offering">${marigoldSvg(flowers)}</div>
  </div>`;
}

function renderStones() {
  const list = filtered();
  const slice = list.slice(0, state.shown);
  const yard = $("#yard");
  yard.innerHTML = slice.length ? slice.map(stone).join("") : `<div class="empty">No ghost answers to that name.</div>`;
  $("#showing").textContent = `${list.length.toLocaleString()} of ${state.yard.ghosts.length.toLocaleString()} ghosts`
    + (state.filter.year ? ` · died in ${state.filter.year}` : "");
  yard.querySelectorAll("[data-ghost]").forEach((b) => b.addEventListener("click", () => goGhost(b.dataset.ghost)));
  const more = $("#more");
  more.innerHTML = list.length > state.shown ? `<button class="btn">Walk deeper (${(list.length - state.shown).toLocaleString()} more)</button>` : "";
  more.firstChild?.addEventListener("click", () => { state.shown += 240; renderStones(); });
}

function goGhost(id) {
  location.hash = `#/r/${state.repoId}/${id}`;
}

// --------------------------------------------------------------- chamber

function closeChamber() {
  if (chamber.hidden) return;
  chamber.hidden = true;
  chamber.innerHTML = "";
  document.body.style.overflow = "";
  state.ghost = null;
  if (state.yard) {
    document.title = `${state.yard.repo.label} · Git Séance`;
    renderStones(); // refresh marigolds and epitaphs
  }
}

async function openChamber(id) {
  if (state.ghost?.ghost.id === id && !chamber.hidden) return;
  chamber.hidden = false;
  document.body.style.overflow = "hidden";
  chamber.innerHTML = `<div class="ritual">${candle("big")}<p>Lighting the candles…</p></div>`;
  state.chat = [];
  let d;
  try {
    d = await api(`/api/ghost?repo=${encodeURIComponent(state.repoId)}&id=${id}`);
  } catch (e) {
    chamber.innerHTML = `<div class="ritual"><p>${esc(e.message)}</p><small><a href="#/r/${esc(state.repoId)}">Back to the graveyard</a></small></div>`;
    return;
  }
  state.ghost = d;
  document.title = `${d.ghost.name} · Git Séance`;
  renderChamber();
  chamber.scrollTop = 0;
  if (d.epitaph_pending) fetchEpitaph(d);
}

async function fetchEpitaph(d) {
  const el = $("#epitaph");
  el?.classList.add("pending");
  try {
    const ep = await api("/api/epitaph", { repo: state.repoId, id: d.ghost.id });
    if (state.ghost !== d) return;
    d.epitaph = ep;
    const g = ghostById(d.ghost.id);
    if (g && ep.source === "model") g.epitaph = ep.epitaph;
    renderEpitaph();
  } catch {
    el?.classList.remove("pending");
  }
}

function commitLink(sha, label) {
  const tpl = state.ghost?.commit_url;
  const text = esc(label || sha.slice(0, 7));
  return tpl ? `<a class="sha" href="${esc(tpl.replace("{sha}", sha))}" target="_blank" rel="noopener">${text}</a>` : `<span class="sha">${text}</span>`;
}

function timeline(events) {
  const rows = [];
  const item = (e, i) => {
    const last = i === events.length - 1;
    let what;
    if (e.status === "A") what = i === 0 ? "born" : "brought back";
    else if (e.status === "R") what = `renamed from <span class="pill">${esc(e.old_path)}</span>`;
    else if (e.status === "D") what = last ? "<strong>died</strong>" : "deleted";
    else what = "changed";
    return `<li class="${esc(e.status)}"><span class="when">${fmtDate(e.at)}</span>${commitLink(e.sha)} <span class="who">${esc(e.author)}</span> ${what}: ${esc(e.subject)}</li>`;
  };
  const keep = new Set();
  events.forEach((e, i) => { if (i < 4 || i >= events.length - 6 || e.status !== "M") keep.add(i); });
  let skipped = 0;
  events.forEach((e, i) => {
    if (keep.has(i)) {
      if (skipped) rows.push(`<li class="gap">… ${plural(skipped, "quiet commit")} …</li>`);
      skipped = 0;
      rows.push(item(e, i));
    } else skipped++;
  });
  return `<ol class="timeline">${rows.join("")}</ol>`;
}

function codeBody(text) {
  return esc(text).split("\n").map((l) =>
    `<span class="l">${l.replace(/\[REDACTED [^\]]+\]/g, (m) => `<mark>${m}</mark>`) || " "}</span>`).join("");
}

function renderEpitaph() {
  const d = state.ghost;
  const el = $("#epitaph");
  if (!el) return;
  el.classList.remove("pending");
  el.innerHTML = `“${esc(d.epitaph.epitaph)}”`;
  el.classList.remove("materialize");
  void el.offsetWidth;
  el.classList.add("materialize");
  $("#cause").innerHTML = ghostText(d.epitaph.cause_of_death);
  $("#obit").innerHTML = ghostText(d.epitaph.obituary);
}

function renderChamber() {
  const d = state.ghost;
  const g = d.ghost;
  const death = d.events[d.events.length - 1];
  const born = d.events.find((e) => e.status !== "D") || null;
  const span = days(g);
  const secrets = Object.entries(d.secrets);
  const flowers = marigolds(state.repoId, g.id);
  const ready = state.config.llm_ready;

  chamber.innerHTML = `<div class="chamber-inner">
    <div class="chamber-top">
      <div class="crumbs"><a href="#/r/${esc(state.repoId)}">⚰ ${esc(d.repo_label)}</a> / ${esc(g.dir ? g.dir + "/" : "")}<strong>${esc(g.name)}</strong></div>
      <a class="btn btn-ghost" href="#/r/${esc(state.repoId)}" aria-label="Close">Leave the chamber ✕</a>
    </div>
    <div class="chamber-grid">
      <div>
        <div class="headstone">
          <div class="rip">HERE LIES</div>
          <h1 id="chamber-title">${esc(g.name)}</h1>
          <div class="path">${esc(g.path)}</div>
          <div class="dates">${g.born_at ? fmtDate(g.born_at) : "?"} — ${fmtDate(g.died_at)}</div>
          <div class="span">${esc(lifespan(span))}${g.lives > 1 ? ` · died ${g.lives} times` : ""}</div>
          <div class="epitaph" id="epitaph">“${esc(d.epitaph.epitaph)}”</div>
          <div class="offering" id="offering">${marigoldSvg(flowers)}</div>
        </div>
        ${secrets.length ? `<div class="haunted">⚠ <strong>Haunted.</strong> This ghost carried ${secrets.map(([k, n]) => `${n} ${esc(k)}${n > 1 ? "s" : ""}`).join(", ")} to its grave.
          Deleting a file does not delete it from history: anyone who clones this repository can still read it. Treat it as leaked and rotate it. (Git Séance has redacted it here and from the model.)</div>` : ""}
        <div class="panel facts">
          <h3>What git remembers</h3>
          <div class="fact"><div class="k">Cause of death</div><div class="v" id="cause">${ghostText(d.epitaph.cause_of_death)}</div></div>
          <div class="fact"><div class="k">Obituary</div><div class="v" id="obit">${ghostText(d.epitaph.obituary)}</div></div>
          <div class="fact"><div class="k">Born</div><div class="v">${born ? `${fmtDate(born.at)} by ${esc(born.author)} ${commitLink(born.sha)}${g.born_as ? ` as <span class="pill">${esc(g.born_as)}</span>` : ""}` : "not recorded"}</div></div>
          <div class="fact"><div class="k">Died</div><div class="v">${fmtDate(death.at)} by ${esc(death.author)} ${commitLink(death.sha)}<br><em>“${esc(death.subject)}”</em></div></div>
          ${d.contributors.length ? `<div class="fact"><div class="k">Shaped by</div><div class="v">${d.contributors.map((c) => `${esc(c.name)} <span class="count">(${c.commits})</span>`).join(", ")}</div></div>` : ""}
          ${g.former_names.length ? `<div class="fact"><div class="k">Former names</div><div class="v pills">${g.former_names.map((n) => `<span class="pill">${esc(n)}</span>`).join("")}</div></div>` : ""}
          ${d.buried_with_total ? `<div class="fact"><div class="k">Buried alongside</div><div class="v"><div class="pills">${d.buried_with.slice(0, 12).map((p) => `<span class="pill">${esc(p)}</span>`).join("")}${d.buried_with_total > 12 ? `<span class="pill">+${d.buried_with_total - 12} more</span>` : ""}</div></div></div>` : ""}
          ${d.successors.length ? `<div class="fact"><div class="k">Heir</div><div class="v pills">${d.successors.map((s) => `<span class="pill heir">${esc(s.path)}</span>`).join("")}</div></div>` : ""}
          <div class="fact"><div class="k">Last body</div><div class="v">${d.binary ? "binary" : `${plural(d.line_count, "line")}`} · ${(d.size_bytes / 1024).toFixed(1)} KB · ${esc(d.language)}</div></div>
        </div>
        <div class="panel facts">
          <h3>A life in commits</h3>
          ${timeline(d.events)}
          ${d.last_body ? `<details class="body"><summary>Read its last body${d.truncated ? " (truncated)" : ""}</summary><pre class="code">${codeBody(d.last_body)}</pre></details>` : ""}
          <div class="actions">
            <button class="btn btn-fire" id="resurrect">${candle()} Resurrect</button>
            <button class="btn" id="marigold"><svg width="18" height="18" aria-hidden="true"><use href="#marigold"/></svg> Leave a marigold</button>
            <button class="btn" id="share">Share this ghost</button>
          </div>
          <div class="cmd" id="cmd" hidden></div>
        </div>
      </div>
      <aside class="panel seance" id="seance" aria-label="Séance">
        <h3>The séance</h3>
        <div class="altar">${candle()}${candle("big")}${candle()}</div>
        <div class="transcript" id="transcript" aria-live="polite">
          ${ready ? `<div class="ritual-start" id="ritual-start"><p>The candles are lit. ${esc(g.name)} is listening.</p>
            <button class="btn btn-fire" id="summon">Summon the ghost</button></div>`
            : `<div class="silent">The spirits are silent: no model is configured. Set <code>SEANCE_LLM_API_KEY</code> (see <code>.env.example</code>) to let the dead speak.</div>`}
        </div>
        <div class="prompts" id="prompts" hidden>${QUESTIONS.map((q) => `<button class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <form class="ask" id="ask" hidden autocomplete="off">
          <input id="ask-input" placeholder="Ask the ghost…" maxlength="600" aria-label="Your question for the ghost">
          <button class="btn btn-fire" type="submit">Ask</button>
        </form>
      </aside>
    </div>
  </div>`;

  $("#resurrect").addEventListener("click", () => {
    const cmd = $("#cmd");
    cmd.hidden = false;
    cmd.textContent = d.resurrect_cmd;
    copy(d.resurrect_cmd, "Resurrection spell copied. Run it in your clone.");
  });
  $("#marigold").addEventListener("click", () => {
    const key = marigoldKey(state.repoId, g.id);
    const n = Math.min(5, store(key, 0) + 1);
    save(key, n);
    $("#offering").innerHTML = marigoldSvg(n);
    toast(n >= 5 ? "Its altar is full of marigolds." : "A marigold to guide it home.");
  });
  $("#share").addEventListener("click", () => copy(location.href, "Link to this ghost copied"));
  $("#summon")?.addEventListener("click", () => { $("#ritual-start").remove(); speak(""); });
  $("#prompts").querySelectorAll("[data-q]").forEach((b) => b.addEventListener("click", () => speak(b.dataset.q)));
  $("#ask").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = $("#ask-input");
    const q = input.value.trim();
    if (q) { input.value = ""; speak(q); }
  });
}

// ---------------------------------------------------------------- seance

function ghostText(text, citations) {
  const ok = new Set(citations?.verified || []);
  const bad = new Set(citations?.invented || []);
  return esc(text).replace(/\[([0-9a-f]{7,40})\]/g, (m, h) => {
    if (bad.has(h)) return `<span class="cite bad" title="No such commit in this ghost's records">${h.slice(0, 7)}</span>`;
    const full = state.ghost?.known_shas.find((s) => s.startsWith(h));
    if (full && state.ghost.commit_url && (!citations || ok.has(h))) {
      return `<a class="cite" href="${esc(state.ghost.commit_url.replace("{sha}", full))}" target="_blank" rel="noopener" title="Verified: this commit is in the records">${h.slice(0, 7)}</a>`;
    }
    return `<span class="cite" title="${full ? "Verified: this commit is in the records" : "Checking…"}">${h.slice(0, 7)}</span>`;
  }).replace(/\n/g, "<br>");
}

function addMsg(cls, html) {
  const t = $("#transcript");
  const el = document.createElement("div");
  el.className = `msg ${cls}`;
  el.innerHTML = html;
  t.appendChild(el);
  t.scrollTop = t.scrollHeight;
  return el;
}

function metaLine(usage, cites) {
  const bits = [];
  if (usage?.model) bits.push(esc(prettyModel(usage.model)));
  if (usage?.first_token_ms != null) bits.push(`first word ${(usage.first_token_ms / 1000).toFixed(1)}s`);
  if (usage?.latency_ms) bits.push(`${(usage.latency_ms / 1000).toFixed(1)}s`);
  if (usage?.completion_tokens) bits.push(`${usage.completion_tokens} tokens`);
  if (cites) {
    if (cites.verified.length) bits.push(`<span class="ok">✓ ${plural(cites.verified.length, "citation")} verified against git</span>`);
    if (cites.invented.length) bits.push(`<span class="warn">✗ ${cites.invented.length} unverified</span>`);
    if (!cites.verified.length && !cites.invented.length) bits.push("no commits cited");
  }
  return `<div class="meta">${bits.join(" · ")}</div>`;
}

async function speak(question) {
  if (state.busy || !state.ghost) return;
  const d = state.ghost;
  state.busy = true;
  const seance = $("#seance");
  seance.classList.add("speaking");
  $("#prompts").hidden = true;
  $("#ask").hidden = true;
  if (question) addMsg("you", esc(question));
  const el = addMsg("ghost", '<span class="cursor"></span>');
  const t = $("#transcript");
  let text = "";
  let done = null;
  let failure = null;

  try {
    const res = await fetch("/api/summon", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo: state.repoId, id: d.ghost.id, history: state.chat, question }),
    });
    if (!res.ok || !res.body) {
      const j = await res.json().catch(() => ({}));
      throw new Error(j.error || `the séance failed (${res.status})`);
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done: end } = await reader.read();
      if (end) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const event = /^event: (.*)$/m.exec(block)?.[1];
        const data = JSON.parse(/^data: (.*)$/m.exec(block)?.[1] || "{}");
        if (event === "token") {
          text += data.t;
          el.innerHTML = ghostText(text) + '<span class="cursor"></span>';
          t.scrollTop = t.scrollHeight;
        } else if (event === "done") done = data;
        else if (event === "error") failure = data.error;
      }
    }
  } catch (e) {
    failure = e.message;
  }

  if (state.ghost !== d) return; // the visitor left mid-sentence
  if (failure && !text) {
    el.classList.add("error");
    el.innerHTML = `The connection fades… ${esc(failure)}`;
  } else {
    el.innerHTML = ghostText(text, done?.citations) + metaLine(done?.usage, done?.citations);
    state.chat.push({ role: "user", content: question || OPENING }, { role: "assistant", content: text });
  }
  t.scrollTop = t.scrollHeight;
  seance.classList.remove("speaking");
  $("#prompts").hidden = false;
  $("#ask").hidden = false;
  state.busy = false;
  $("#ask-input")?.focus({ preventScroll: true });
}

boot();
