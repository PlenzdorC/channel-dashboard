// Shared helpers: API calls, escaping, number formats, platform icons.

export const PLATFORMS = {
  youtube: { label: "YouTube", color: "var(--youtube)" },
  tiktok: { label: "TikTok", color: "var(--tiktok)" },
  instagram: { label: "Instagram", color: "var(--instagram)" },
};

export async function api(path, { method = "GET", body } = {}) {
  const init = { method, headers: {} };
  if (method !== "GET") {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body ?? {});
  }
  const response = await fetch(path, init);
  const data = response.headers.get("content-type")?.includes("json") ? await response.json() : null;
  if (!response.ok) {
    const detail = data?.detail;
    throw new Error(typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join(", ") : `HTTP ${response.status}`);
  }
  return data;
}

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

/** Only http(s) URLs from platform data may end up in src/href attributes. */
export const safeUrl = (url) => (/^https?:\/\//i.test(url || "") ? esc(url) : "");

const nf = new Intl.NumberFormat("de-DE");
const nf1 = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 });
const compact = new Intl.NumberFormat("de-DE", { notation: "compact", maximumFractionDigits: 1 });

export const fmt = {
  int: (v) => (v == null ? "–" : nf.format(Math.round(v))),
  one: (v) => (v == null ? "–" : nf1.format(v)),
  compact: (v) => (v == null ? "–" : Math.abs(v) >= 10000 ? compact.format(v) : nf.format(Math.round(v))),
  pct: (v) => (v == null ? "–" : `${nf1.format(v)} %`),
  secs: (v) => (v == null ? "–" : v >= 60 ? `${Math.floor(v / 60)}:${String(Math.round(v % 60)).padStart(2, "0")} min` : `${nf1.format(v)} s`),
  hours: (v) => (v == null ? "–" : nf1.format(v)),
  signed: (v) => (v == null ? "" : `${v > 0 ? "+" : v < 0 ? "−" : "±"}${nf.format(Math.abs(Math.round(v)))}`),
  date: (iso) => (iso ? new Date(iso).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" }) : "–"),
  day: (iso) => { const [, m, d] = iso.split("-"); return `${d}.${m}.`; },
  ago: (iso) => {
    if (!iso) return "noch nie";
    const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
    if (minutes < 1) return "gerade eben";
    if (minutes < 60) return `vor ${minutes} Min.`;
    if (minutes < 60 * 24) return `vor ${Math.round(minutes / 60)} Std.`;
    return `vor ${Math.round(minutes / 1440)} Tagen`;
  },
  age: (iso) => {
    if (!iso) return "";
    const hours = (Date.now() - new Date(iso).getTime()) / 3600000;
    if (hours < 48) return `Erste ${Math.max(Math.round(hours), 1)} Std.`;
    return `Erste ${Math.round(hours / 24)} Tage`;
  },
};

export function delta(now, before) {
  if (now == null || !before) return "";
  const change = ((now - before) / before) * 100;
  const cls = change >= 0 ? "delta-up" : "delta-down";
  return `<span class="${cls}">${change >= 0 ? "▲" : "▼"} ${nf1.format(Math.abs(change))} %</span> zum Vorzeitraum`;
}

const ICON_PATHS = {
  youtube: '<rect x="2" y="5" width="20" height="14" rx="4" fill="currentColor"/><path d="M10 9l5 3-5 3z" fill="#181818"/>',
  tiktok: '<path d="M14 3v10.5a3.5 3.5 0 1 1-3.5-3.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/><path d="M14 3c.4 2.6 2 4.2 5 4.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/>',
  instagram: '<rect x="3" y="3" width="18" height="18" rx="5" fill="none" stroke="currentColor" stroke-width="2.2"/><circle cx="12" cy="12" r="4" fill="none" stroke="currentColor" stroke-width="2.2"/><circle cx="17.3" cy="6.7" r="1.3" fill="currentColor"/>',
  overview: '<path d="M4 13h6V4H4zm0 7h6v-5H4zm10 0h6v-9h-6zm0-16v5h6V4z" fill="currentColor"/>',
  videos: '<rect x="3" y="5" width="13" height="14" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M16 10l5-3v10l-5-3z" fill="currentColor"/>',
  shorts: '<path d="M7 3h10v18H7z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M10.5 9.5l4 2.5-4 2.5z" fill="currentColor"/>',
  post: '<path d="M12 16V4m0 0l-5 5m5-5l5 5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
  accounts: '<circle cx="9" cy="8" r="3.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0" fill="none" stroke="currentColor" stroke-width="2"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14.5a6.5 6.5 0 0 1 3.5 5.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
  sync: '<path d="M20 12a8 8 0 0 1-14 5.3M4 12a8 8 0 0 1 14-5.3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M18 2.5v4.2h-4.2M6 21.5v-4.2h4.2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
  close: '<path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
};

export function icon(name, color) {
  const style = color ? ` style="color:${color}"` : "";
  return `<svg viewBox="0 0 24 24" aria-hidden="true"${style}>${ICON_PATHS[name] || ""}</svg>`;
}

export const platformIcon = (p) => icon(p, PLATFORMS[p]?.color);

export function platformPill(p) {
  return `<span class="pill">${platformIcon(p)}${esc(PLATFORMS[p]?.label || p)}</span>`;
}

export function thumb(video, small = false) {
  const cls = small ? "thumb small" : "thumb";
  const src = safeUrl(video.thumbnail_url);
  return src
    ? `<img class="${cls}" src="${src}" alt="" loading="lazy" referrerpolicy="no-referrer">`
    : `<div class="${cls}">${platformIcon(video.platform)}</div>`;
}

export function avatar(account) {
  const src = safeUrl(account.avatar_url);
  return src
    ? `<img class="avatar" src="${src}" alt="" referrerpolicy="no-referrer">`
    : `<div class="avatar">${esc((account.name || "?").slice(0, 1).toUpperCase())}</div>`;
}

export function toast(message, isError = false) {
  const node = document.createElement("div");
  node.className = `toast${isError ? " error" : ""}`;
  node.setAttribute("role", "status");
  node.textContent = message;
  document.body.appendChild(node);
  setTimeout(() => node.remove(), isError ? 7000 : 4000);
}

export const nicheLabel = (n) => ({ finanzklarheit: "Finanzklarheit", mindmatrix: "MindMatrix", neurofakt: "NeuroFakt" }[n] || n || "ohne Nische");
