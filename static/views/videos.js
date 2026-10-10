// Videos: sortable table of every video, and the detail drawer (growth, retention, other platforms).

import { app } from "../app.js";
import { lineChart } from "../charts.js";
import { api, esc, fmt, icon, nicheLabel, platformPill, PLATFORMS, safeUrl, thumb, toast } from "../util.js";

const COLUMNS = [
  { key: "published_at", label: "Veröffentlicht", fmt: (v) => fmt.date(v.published_at) },
  { key: "views", label: "Aufrufe", fmt: (v) => fmt.int(v.views), num: true },
  { key: "avg_view_pct", label: "Ø angesehen", fmt: (v) => pctBar(v.avg_view_pct), num: true },
  { key: "avg_view_s", label: "Ø Dauer", fmt: (v) => fmt.secs(v.avg_view_s), num: true },
  { key: "likes", label: "Likes", fmt: (v) => fmt.int(v.likes), num: true },
  { key: "comments", label: "Kommentare", fmt: (v) => fmt.int(v.comments), num: true },
  { key: "shares", label: "Shares", fmt: (v) => fmt.int(v.shares), num: true },
  { key: "saves", label: "Saves", fmt: (v) => fmt.int(v.saves), num: true },
  { key: "subs_gained", label: "Follower +", fmt: (v) => fmt.int(v.subs_gained), num: true },
  { key: "engagement", label: "Interaktion", fmt: (v) => fmt.pct(v.engagement), num: true },
];
let sort = { key: "published_at", asc: false };

function pctBar(pct) {
  if (pct == null) return "–";
  return `<span class="bar-cell"><span class="bar"><i style="width:${Math.min(pct, 100)}%"></i></span>${fmt.pct(pct)}</span>`;
}

export async function renderVideos(view, params, stale) {
  const videos = await api(`/api/videos?${app.query()}`);
  if (stale()) return;
  if (!videos.length) {
    view.innerHTML = `<div class="card empty"><h2>Keine Videos</h2><p>Für diese Auswahl gibt es noch keine Videos. Verbinde Konten oder ändere die Filter.</p></div>`;
    return;
  }
  const draw = () => {
    const sorted = [...videos].sort((a, b) => compare(a[sort.key], b[sort.key]) * (sort.asc ? 1 : -1));
    view.innerHTML = `<section class="card"><div class="table-wrap"><table>
      <thead><tr><th>Video</th>${COLUMNS.map((c) => `<th class="sortable ${c.num ? "num" : ""} ${sort.key === c.key ? `sorted ${sort.asc ? "asc" : ""}` : ""}" data-sort="${c.key}" tabindex="0">${c.label}</th>`).join("")}</tr></thead>
      <tbody>${sorted.map(row).join("")}</tbody></table></div>
      <p class="muted small">${videos.length} Videos. TikTok liefert über die Schnittstelle keine Wiedergabedauer, Instagram keine Follower pro Video. Diese Felder bleiben dort leer.</p></section>`;
    view.querySelectorAll("th[data-sort]").forEach((th) => {
      const toggle = () => {
        sort = sort.key === th.dataset.sort ? { ...sort, asc: !sort.asc } : { key: th.dataset.sort, asc: false };
        draw();
      };
      th.addEventListener("click", toggle);
      th.addEventListener("keydown", (e) => { if (e.key === "Enter") toggle(); });
    });
    view.querySelectorAll("tr[data-video]").forEach((tr) => tr.addEventListener("click", () => openVideo(tr.dataset.video)));
  };
  draw();
}

function compare(a, b) {
  if (a == null && b == null) return 0;
  if (a == null) return -1;
  if (b == null) return 1;
  return a < b ? -1 : a > b ? 1 : 0;
}

function row(v) {
  return `<tr class="clickable" data-video="${esc(v.id)}">
    <td><div class="video-cell">${thumb(v, true)}<div><div class="t">${esc(v.title || "(ohne Titel)")}</div>
      <div class="muted small">${esc(v.account_name)} · ${PLATFORMS[v.platform].label}${v.slug ? ` · <span title="Pipeline-Short">${esc(v.slug)}</span>` : ""}</div></div></div></td>
    ${COLUMNS.map((c) => `<td class="${c.num ? "num" : ""}">${c.fmt(v)}</td>`).join("")}</tr>`;
}

export async function openVideo(id) {
  const detail = await api(`/api/videos/${encodeURIComponent(id)}`);
  const renders = await api("/api/renders");
  const v = detail.video;
  const backdrop = document.createElement("div");
  backdrop.className = "drawer-backdrop";
  const drawer = document.createElement("aside");
  drawer.className = "drawer";
  drawer.setAttribute("role", "dialog");
  drawer.setAttribute("aria-label", v.title || "Video");
  const link = safeUrl(v.url);
  drawer.innerHTML = `
    <button class="btn close" type="button" aria-label="Schließen">${icon("close")}</button>
    <div class="muted small">${platformPill(v.platform)} ${esc(v.account_name)} · ${fmt.date(v.published_at)}</div>
    <h2 style="margin-top:10px">${esc(v.title || "(ohne Titel)")}</h2>
    ${link ? `<a href="${link}" target="_blank" rel="noopener noreferrer">${icon("external").replace("<svg", '<svg width="14" height="14"')} Auf ${PLATFORMS[v.platform].label} öffnen</a>` : ""}
    <div class="stat-grid">
      ${stat("Aufrufe", fmt.int(v.views))}${stat("Ø angesehen", fmt.pct(v.avg_view_pct))}
      ${stat("Ø Dauer", fmt.secs(v.avg_view_s))}${stat("Interaktion", fmt.pct(v.engagement))}
      ${stat("Likes", fmt.int(v.likes))}${stat("Kommentare", fmt.int(v.comments))}
      ${stat("Shares", fmt.int(v.shares))}${stat(v.platform === "instagram" ? "Saves" : "Follower +", fmt.int(v.platform === "instagram" ? v.saves : v.subs_gained))}
    </div>
    ${detail.retention ? `<section class="card"><h2>Zuschauerbindung</h2><div id="retention-chart"></div>
      <p class="muted small">Anteil der Zuschauer, die an dieser Stelle noch zusehen. Über 100 % heißt: Diese Stelle wurde wiederholt angesehen.</p></section>` : ""}
    <section class="card" style="margin-top:16px"><h2>Aufrufe seit Veröffentlichung</h2><div id="growth-chart"></div></section>
    <section class="card" style="margin-top:16px"><h2>Pipeline-Short</h2>
      <label class="field"><span>Gehört zu</span>
        <select class="input" id="slug-select"><option value="">nicht zugeordnet</option>
          ${renders.map((r) => `<option value="${esc(r.slug)}" ${r.slug === v.slug ? "selected" : ""}>${esc(nicheLabel(r.niche))} · ${esc(r.title)}</option>`).join("")}
        </select></label>
      ${detail.siblings.length ? siblingsTable(detail.siblings) : '<p class="muted small">Auf anderen Plattformen ist dieser Short noch nicht zugeordnet.</p>'}
    </section>`;
  document.body.append(backdrop, drawer);
  const close = () => { backdrop.remove(); drawer.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  backdrop.addEventListener("click", close);
  drawer.querySelector(".close").addEventListener("click", close);
  drawer.querySelector(".close").focus();
  drawer.querySelector("#slug-select").addEventListener("change", async (e) => {
    try {
      await api(`/api/videos/${encodeURIComponent(id)}/slug`, { method: "POST", body: { slug: e.target.value || null } });
      toast("Zuordnung gespeichert.");
    } catch (err) { toast(err.message, true); }
  });
  const color = PLATFORMS[v.platform].color;
  if (detail.retention) {
    const duration = v.duration_s;
    lineChart(drawer.querySelector("#retention-chart"), {
      points: detail.retention.map(([x, y]) => [x, y * 100]), color, yMax: Math.max(100, ...detail.retention.map((p) => p[1] * 100)),
      fmtY: (y) => `${Math.round(y)} %`,
      fmtX: (x) => (duration ? `${Math.round(x * duration)} s` : `${Math.round(x * 100)} %`),
      tipHtml: ([x, y]) => `<div class="head">${duration ? `Sekunde ${Math.round(x * duration)}` : `${Math.round(x * 100)} % des Videos`}</div><div class="row"><span>Sehen noch zu</span><b>${fmt.pct(y)}</b></div>`,
    });
  }
  const growth = detail.history.filter((h) => h.views != null).map((h) => [new Date(h.day).getTime(), h.views]);
  const growthBox = drawer.querySelector("#growth-chart");
  if (growth.length > 1) {
    lineChart(growthBox, {
      points: growth, color, fmtY: fmt.compact, fmtX: (t) => fmt.day(new Date(t).toISOString().slice(0, 10)),
      tipHtml: ([t, y]) => `<div class="head">${fmt.date(new Date(t).toISOString())}</div><div class="row"><span>Aufrufe</span><b>${fmt.int(y)}</b></div>`,
    });
  } else {
    growthBox.innerHTML = '<p class="muted">Der Verlauf erscheint, sobald das Dashboard an mehreren Tagen abgerufen hat.</p>';
  }
}

function stat(label, value) {
  return `<div><b>${value}</b><small>${label}</small></div>`;
}

function siblingsTable(rows) {
  return `<table><thead><tr><th>Plattform</th><th>Konto</th><th class="num">Aufrufe</th><th class="num">Ø angesehen</th><th class="num">Interaktion</th></tr></thead><tbody>
    ${rows.map((s) => `<tr><td>${platformPill(s.platform)}</td><td>${esc(s.account_name)}</td><td class="num">${fmt.int(s.views)}</td>
      <td class="num">${fmt.pct(s.avg_view_pct)}</td><td class="num">${fmt.pct(s.engagement)}</td></tr>`).join("")}</tbody></table>`;
}
