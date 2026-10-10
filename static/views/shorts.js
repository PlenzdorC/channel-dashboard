// Shorts: every pipeline short with its numbers per platform side by side, plus a post button.

import { api, esc, fmt, nicheLabel, platformIcon, PLATFORMS } from "../util.js";
import { openVideo } from "./videos.js";

export async function renderShorts(view, params, stale) {
  const rows = await api("/api/shorts");
  if (stale()) return;
  if (!rows.length) {
    view.innerHTML = `<div class="card empty"><h2>Noch keine fertigen Shorts</h2><p>Hier erscheinen alle Videos aus <code>&lt;nische&gt;/&lt;N. slug&gt;/renders/&lt;slug&gt;.mp4</code>.</p></div>`;
    return;
  }
  const platforms = Object.keys(PLATFORMS);
  view.innerHTML = `<section class="card"><div class="table-wrap"><table>
    <thead><tr><th>Short</th>${platforms.map((p) => `<th class="num">${platformIcon(p).replace("<svg", '<svg width="14" height="14"')} ${PLATFORMS[p].label}</th>`).join("")}
      <th class="num">Gesamt</th><th class="num">Ø angesehen</th><th class="num">Interaktion</th><th></th></tr></thead>
    <tbody>${rows.map((r) => row(r, platforms)).join("")}</tbody></table></div>
    <p class="muted small">Videos werden dem Short über den Titel zugeordnet. Stimmt eine Zuordnung nicht, ändere sie in der Video-Ansicht.</p></section>`;
  view.querySelectorAll("[data-video]").forEach((n) => n.addEventListener("click", (e) => { e.preventDefault(); openVideo(n.dataset.video); }));
}

function row(r, platforms) {
  const cells = platforms.map((p) => {
    const v = r.platforms[p];
    if (!v) return `<td class="num muted">–</td>`;
    const pct = v.avg_view_pct != null ? `<div class="muted small">${fmt.pct(v.avg_view_pct)} angesehen</div>` : "";
    return `<td class="num"><a href="#" data-video="${esc(v.id)}">${fmt.int(v.views)}</a>${pct}</td>`;
  }).join("");
  const missing = platforms.filter((p) => !r.platforms[p]).length;
  const post = r.render
    ? `<a class="btn" href="#posten?render=${encodeURIComponent(r.render)}">${missing ? "Posten" : "Erneut posten"}</a>` : "";
  return `<tr><td><div><b>${esc(r.title)}</b></div><div class="muted small">${esc(nicheLabel(r.niche))} · ${esc(r.slug)}</div></td>
    ${cells}<td class="num"><b>${fmt.int(r.views)}</b></td><td class="num">${fmt.pct(r.avg_view_pct)}</td>
    <td class="num">${fmt.pct(r.engagement)}</td><td>${post}</td></tr>`;
}
