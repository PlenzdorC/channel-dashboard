// Übersicht: KPIs, daily views by platform, one card per account with its newest short, top videos, niches.

import { app } from "../app.js";
import { stackedBars } from "../charts.js";
import { api, avatar, delta, esc, fmt, nicheLabel, platformIcon, platformPill, PLATFORMS, thumb } from "../util.js";
import { openVideo } from "./videos.js";

export async function renderOverview(view, params, stale) {
  if (!app.meta.accounts.length) {
    view.innerHTML = `<div class="card empty">
      <h2>Noch keine Konten verbunden</h2>
      <p>Verbinde deine YouTube-, TikTok- und Instagram-Konten. Danach holt das Dashboard die Zahlen automatisch alle paar Stunden.
         Zum Ausprobieren: <code>.venv\\Scripts\\python -m dashboard demo</code> startet es mit erfundenen Zahlen.</p>
      <a class="btn primary" href="#konten">Konten verbinden</a></div>`;
    return;
  }
  const data = await api(`/api/overview?${app.query()}`);
  if (stale()) return;
  const k = data.kpis;
  view.innerHTML = `
    <section class="grid kpis" aria-label="Kennzahlen">
      ${kpi("Aufrufe", fmt.compact(k.views), delta(k.views, k.views_prev))}
      ${kpi("Wiedergabezeit (Std.)", fmt.hours(k.watch_hours), "YouTube und Instagram")}
      ${kpi("Follower", fmt.compact(k.followers), k.followers_delta != null ? `<span class="${k.followers_delta >= 0 ? "delta-up" : "delta-down"}">${fmt.signed(k.followers_delta)}</span> im Zeitraum` : "")}
      ${kpi("Ø angesehen", fmt.pct(k.avg_view_pct), "Anteil des Videos, gewichtet nach Aufrufen")}
      ${kpi("Interaktionsrate", fmt.pct(k.engagement), "(Likes + Kommentare + Shares + Saves) / Aufrufe")}
    </section>
    <section class="card">
      <h2>Aufrufe pro Tag</h2>
      <div class="legend" id="legend"></div>
      <div id="daily-chart"></div>
      <p class="muted small" style="margin:8px 0 0">YouTube liefert Tageswerte mit etwa zwei Tagen Verzögerung. Bei TikTok und Instagram ergibt sich der Tageswert
        aus dem Zuwachs zwischen zwei Abrufen, deshalb erscheint er erst ab dem zweiten Tag mit Abruf.</p>
    </section>
    <div class="section-title"><h2>Konten</h2><span class="muted">${data.accounts.length} verbunden · letzte ${data.days} Tage</span></div>
    <section class="grid accounts">${data.accounts.map(accountCard).join("")}</section>
    <div class="grid two">
      <section class="card"><h2>Top-Videos im Zeitraum</h2>${topVideos(data.top_videos)}</section>
      <section class="card"><h2>Nischen im Vergleich</h2>${nicheTable(data.niches)}</section>
    </div>`;
  drawDaily(data.series);
  app.onResize = () => drawDaily(data.series);
  view.querySelectorAll("[data-video]").forEach((node) => {
    node.addEventListener("click", () => openVideo(node.dataset.video));
    node.addEventListener("keydown", (e) => { if (e.key === "Enter") openVideo(node.dataset.video); });
  });
}

function kpi(label, value, sub) {
  return `<div class="card kpi"><div class="label">${label}</div><div class="value">${value}</div><div class="sub">${sub || ""}</div></div>`;
}

function drawDaily(series) {
  const order = Object.keys(PLATFORMS).filter((p) => series.platforms[p]);
  const legend = document.getElementById("legend");
  const chart = document.getElementById("daily-chart");
  if (!legend || !chart) return;
  legend.innerHTML = order.map((p) => `<span><i style="background:${PLATFORMS[p].color}"></i>${PLATFORMS[p].label}</span>`).join("");
  if (!order.length) {
    chart.innerHTML = `<p class="muted">Noch keine Tageswerte. Sie erscheinen nach dem nächsten Abruf.</p>`;
    return;
  }
  const values = (p) => series.platforms[p];
  stackedBars(chart, {
    days: series.days,
    series: order.map((p) => ({ label: PLATFORMS[p].label, color: PLATFORMS[p].color, values: values(p) })),
    fmt: fmt.compact,
    dayFmt: fmt.day,
    tipHtml: (i) => {
      const total = order.reduce((s, p) => s + (values(p)[i] || 0), 0);
      return `<div class="head">${fmt.date(series.days[i])}</div>` +
        [...order].reverse().map((p) => `<div class="row"><span>${platformIcon(p).replace("<svg", '<svg width="12" height="12"')} ${PLATFORMS[p].label}</span><b>${fmt.int(values(p)[i])}</b></div>`).join("") +
        `<div class="row"><span>Gesamt</span><b>${fmt.int(total)}</b></div>`;
    },
  });
}

function accountCard(a) {
  const latest = a.latest;
  const followers = a.followers_delta != null
    ? `<small>Follower <span class="${a.followers_delta >= 0 ? "delta-up" : "delta-down"}">${fmt.signed(a.followers_delta)}</span></small>` : "<small>Follower</small>";
  const status = a.status !== "ok" ? `<div class="note ${a.status === "warning" ? "" : "warn"}">${esc(a.status_message || "Problem beim Abruf")}</div>` : "";
  return `<article class="card">
    <div class="acc-head">${avatar(a)}
      <div class="who"><div><b>${esc(a.name)}</b></div><div class="muted small">${esc(a.handle || "")} · ${esc(nicheLabel(a.niche))}</div></div>
      ${platformPill(a.platform)}</div>
    <div class="acc-numbers">
      <div><b>${fmt.compact(a.followers)}</b>${followers}</div>
      <div><b>${fmt.compact(a.views)}</b><small>Aufrufe</small></div>
      <div><b>${a.watch_hours != null ? fmt.hours(a.watch_hours) : fmt.int(a.videos)}</b><small>${a.watch_hours != null ? "Std. Wiedergabe" : "Videos"}</small></div>
    </div>
    ${status}
    ${latest ? latestBlock(latest) : '<p class="muted small">Noch keine Videos gefunden.</p>'}
  </article>`;
}

function latestBlock(v) {
  const typical = v.typical_views ? `<div class="stat-row"><span class="muted">Üblich (Median der letzten Videos)</span><span>${fmt.int(v.typical_views)}</span></div>` : "";
  return `<div class="latest" data-video="${esc(v.id)}" role="button" tabindex="0">
    ${thumb(v)}
    <div>
      <div class="muted small">Neuestes Video · ${esc(fmt.age(v.published_at))}</div>
      <div class="title">${esc(v.title || "(ohne Titel)")}</div>
      <div class="stat-row"><span class="muted">Aufrufe</span><span>${fmt.int(v.views)}</span></div>
      ${typical}
      ${v.avg_view_pct != null ? `<div class="stat-row"><span class="muted">Ø angesehen</span><span>${fmt.pct(v.avg_view_pct)}</span></div>` : ""}
      <div class="stat-row"><span class="muted">Likes</span><span>${fmt.int(v.likes)}</span></div>
      <div class="stat-row"><span class="muted">Rang nach Aufrufen</span><span>${v.rank} von ${v.rank_of}</span></div>
    </div></div>`;
}

function topVideos(videos) {
  if (!videos.length) return '<p class="muted">Keine Videos im Zeitraum.</p>';
  return `<div class="table-wrap"><table><thead><tr><th>Video</th><th class="num">Aufrufe</th><th class="num">Ø angesehen</th><th class="num">Interaktion</th></tr></thead><tbody>
    ${videos.map((v) => `<tr class="clickable" data-video="${esc(v.id)}">
      <td><div class="video-cell">${thumb(v, true)}<div><div class="t">${esc(v.title || "(ohne Titel)")}</div><div class="muted small">${esc(v.account_name)} · ${PLATFORMS[v.platform].label}</div></div></div></td>
      <td class="num">${fmt.int(v.views)}</td><td class="num">${fmt.pct(v.avg_view_pct)}</td><td class="num">${fmt.pct(v.engagement)}</td></tr>`).join("")}
  </tbody></table></div>`;
}

function nicheTable(rows) {
  if (!rows.length) return '<p class="muted">Ordne deine Konten unter „Konten“ einer Nische zu.</p>';
  return `<div class="table-wrap"><table><thead><tr><th>Nische</th><th class="num">Videos</th><th class="num">Median Aufrufe</th><th class="num">Ø angesehen</th><th class="num">Interaktion</th></tr></thead><tbody>
    ${rows.map((r) => `<tr><td>${esc(nicheLabel(r.niche))}</td><td class="num">${fmt.int(r.videos)}</td><td class="num">${fmt.int(r.median_views)}</td>
      <td class="num">${fmt.pct(r.avg_view_pct)}</td><td class="num">${fmt.pct(r.engagement)}</td></tr>`).join("")}
  </tbody></table></div>
  <p class="muted small">Gezählt werden alle Videos der gefilterten Konten. Der Median ist robuster als der Durchschnitt, weil ein einzelner Viral-Hit ihn kaum verschiebt.</p>`;
}
