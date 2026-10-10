// Shell: navigation by hash, the filter row shared by the views, sync button.

import { api, esc, fmt, icon, nicheLabel, PLATFORMS, toast } from "./util.js";
import { renderOverview } from "./views/overview.js";
import { renderVideos } from "./views/videos.js";
import { renderShorts } from "./views/shorts.js";
import { renderPost } from "./views/post.js";
import { renderAccounts } from "./views/accounts.js";

const ROUTES = {
  uebersicht: { title: "Kanal-Dashboard", nav: "Übersicht", icon: "overview", render: renderOverview, filters: true, days: true },
  videos: { title: "Videos", nav: "Videos", icon: "videos", render: renderVideos, filters: true },
  shorts: { title: "Shorts im Vergleich", nav: "Shorts", icon: "shorts", render: renderShorts },
  posten: { title: "Posten", nav: "Posten", icon: "post", render: renderPost },
  konten: { title: "Konten", nav: "Konten", icon: "accounts", render: renderAccounts },
};

const FILTER_KEY = "dashboard.filters";
export const app = {
  meta: null,
  filters: loadFilters(),
  query() {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(this.filters)) if (v) params.set(k, v);
    return params.toString();
  },
  async refreshMeta() {
    this.meta = await api("/api/state");
    renderSyncState();
    return this.meta;
  },
  go(route, params = "") { location.hash = `#${route}${params ? `?${params}` : ""}`; },
  rerender: () => route(),
};

function loadFilters() {
  try {
    return { days: 28, platform: "", niche: "", account: "", ...JSON.parse(localStorage.getItem(FILTER_KEY) || "{}") };
  } catch {
    return { days: 28, platform: "", niche: "", account: "" };
  }
}

function saveFilters() {
  try { localStorage.setItem(FILTER_KEY, JSON.stringify(app.filters)); } catch { /* private mode */ }
}

function parseHash() {
  const [name, query] = location.hash.replace(/^#/, "").split("?");
  return { name: ROUTES[name] ? name : "uebersicht", params: new URLSearchParams(query || "") };
}

function renderNav(active) {
  document.getElementById("nav").innerHTML = Object.entries(ROUTES).map(([key, r]) =>
    `<a href="#${key}" class="${key === active ? "active" : ""}" ${key === active ? 'aria-current="page"' : ""}>${icon(r.icon)}${r.nav}</a>`).join("");
}

function renderFilters(routeDef) {
  const box = document.getElementById("filters");
  if (!routeDef.filters || !app.meta?.accounts.length) { box.innerHTML = ""; return; }
  const f = app.filters;
  const accounts = app.meta.accounts.filter((a) => (!f.platform || a.platform === f.platform) && (!f.niche || a.niche === f.niche));
  const option = (value, label, current) => `<option value="${esc(value)}" ${value === current ? "selected" : ""}>${esc(label)}</option>`;
  box.innerHTML = `
    ${routeDef.days ? `<select class="control" data-filter="days" aria-label="Zeitraum">
      ${[7, 28, 90, 365].map((d) => option(String(d), d === 365 ? "Letztes Jahr" : `Letzte ${d} Tage`, String(f.days))).join("")}</select>` : ""}
    <select class="control" data-filter="platform" aria-label="Plattform">
      ${option("", "Alle Plattformen", f.platform)}${Object.entries(PLATFORMS).map(([k, p]) => option(k, p.label, f.platform)).join("")}</select>
    <select class="control" data-filter="niche" aria-label="Nische">
      ${option("", "Alle Nischen", f.niche)}${app.meta.niches.map((n) => option(n, nicheLabel(n), f.niche)).join("")}</select>
    <select class="control" data-filter="account" aria-label="Konto">
      ${option("", "Alle Konten", f.account)}${accounts.map((a) => option(a.id, `${a.name} · ${PLATFORMS[a.platform].label}`, f.account)).join("")}</select>`;
  box.querySelectorAll("select").forEach((sel) => sel.addEventListener("change", () => {
    app.filters = { ...app.filters, [sel.dataset.filter]: sel.value };
    if (sel.dataset.filter !== "account") app.filters.account = "";
    saveFilters();
    route();
  }));
}

function renderSyncState() {
  const meta = app.meta;
  const btn = document.getElementById("sync-btn");
  const info = document.getElementById("sync-info");
  document.getElementById("demo-badge").classList.toggle("hidden", !meta.demo);
  const last = meta.accounts.map((a) => a.last_sync).filter(Boolean).sort().pop();
  const running = meta.sync.running;
  btn.innerHTML = `${icon("sync").replace("<svg", `<svg class="${running ? "spin" : ""}"`)}${running ? "Aktualisiert …" : "Aktualisieren"}`;
  btn.disabled = running || meta.demo || !meta.accounts.length;
  info.textContent = meta.accounts.length ? `Stand: ${fmt.ago(last)}` : "";
}

async function startSync() {
  try {
    await api("/api/sync", { method: "POST", body: {} });
    await app.refreshMeta();
    pollSync();
  } catch (err) {
    toast(err.message, true);
  }
}

let syncTimer = null;
function pollSync() {
  clearTimeout(syncTimer);
  syncTimer = setTimeout(async () => {
    const wasRunning = app.meta.sync.running;
    await app.refreshMeta();
    if (app.meta.sync.running) return pollSync();
    if (wasRunning) {
      const failed = app.meta.sync.outcomes.filter((o) => !o.ok);
      if (failed.length) toast(`${failed.length} Konto/Konten mit Fehler, Details unter „Konten“.`, true);
      else toast("Zahlen aktualisiert.");
      route();
    }
  }, 2000);
}

let routeSeq = 0;

async function route() {
  const token = ++routeSeq;
  const stale = () => token !== routeSeq; // a newer navigation started while this one was loading
  const { name, params } = parseHash();
  const def = ROUTES[name];
  renderNav(name);
  document.getElementById("page-title").textContent = def.title;
  document.title = `${def.title} · Kanal-Dashboard`;
  try {
    await app.refreshMeta();
    if (stale()) return;
    if (app.meta.sync.running) pollSync();
    renderFilters(def);
    app.onResize = null;
    const view = document.getElementById("view");
    await def.render(view, params, stale);
  } catch (err) {
    if (stale()) return;
    document.getElementById("view").innerHTML = `<div class="card empty"><h2>Das hat nicht geklappt</h2><p>${esc(err.message)}</p></div>`;
  }
}

document.getElementById("sync-btn").addEventListener("click", startSync);
window.addEventListener("hashchange", () => { window.scrollTo(0, 0); route(); });
let resizeTimer = null;
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => app.onResize?.(), 150); });
route();
