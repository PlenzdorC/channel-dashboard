// Posten: pick a finished short, write title and caption once, tick accounts, upload; jobs below.

import { app } from "../app.js";
import { api, esc, fmt, nicheLabel, platformIcon, PLATFORMS, toast } from "../util.js";

let pollTimer = null;
const CAPTION_MAX = 2200; // Instagram's caption limit, also enforced by the server

export async function renderPost(view, params, stale) {
  clearTimeout(pollTimer);
  const [renders, jobs] = await Promise.all([api("/api/renders"), api("/api/posts")]);
  if (stale()) return;
  const accounts = app.meta.accounts;
  if (!renders.length) {
    view.innerHTML = `<div class="card empty"><h2>Keine fertigen Shorts</h2><p>Erst <code>make.py all</code> ausführen, dann erscheinen sie hier.</p></div>`;
    return;
  }
  const chosen = renders.find((r) => r.path === params.get("render")) || renders[0];
  view.innerHTML = `
    <div class="form-grid">
      <section class="card">
        <label class="field"><span>Short</span>
          <select class="input" id="render">${renders.map((r) => `<option value="${esc(r.path)}" ${r === chosen ? "selected" : ""}>
            ${esc(nicheLabel(r.niche))} · ${r.number}. ${esc(r.title)} (${fmt.one(r.size_mb)} MB)</option>`).join("")}</select></label>
        <label class="field"><span>Titel <small class="muted" id="title-count"></small></span>
          <input class="input" id="title" maxlength="100" required></label>
        <label class="field"><span>Beschreibung / Caption <small class="muted" id="caption-count"></small></span>
          <textarea class="input" id="caption" maxlength="2200"></textarea></label>
        <label class="field"><span>Hashtags</span>
          <input class="input" id="hashtags" placeholder="#shorts #psychologie"></label>
        <h2 style="margin-top:20px">Konten</h2>
        <div id="targets">${accounts.length ? targetGroups(accounts) : '<p class="muted">Noch keine Konten verbunden. <a href="#konten">Konten verbinden</a></p>'}</div>
        <div style="display:flex;gap:12px;align-items:center;margin-top:18px">
          <button class="btn primary" id="submit" type="button" ${accounts.length ? "" : "disabled"}>Hochladen</button>
          <span class="muted small" id="submit-info"></span></div>
      </section>
      <section class="card"><h2>Vorschau</h2><video class="preview" id="preview" controls preload="metadata"></video></section>
    </div>
    <div class="section-title"><h2>Letzte Uploads</h2></div>
    <section class="card" id="jobs">${jobList(jobs)}</section>`;

  const el = (id) => view.querySelector(`#${id}`);
  const select = () => {
    const r = renders.find((x) => x.path === el("render").value);
    el("title").value = r.title;
    el("preview").src = `/media/render?path=${encodeURIComponent(r.path)}`;
    view.querySelectorAll("input[data-account]").forEach((box) => {
      const acc = accounts.find((a) => a.id === box.dataset.account);
      box.checked = Boolean(acc.niche && acc.niche === r.niche && acc.status !== "reconnect");
    });
    counters();
  };
  const counters = () => {
    el("title-count").textContent = `${el("title").value.length}/100`;
    el("caption-count").textContent = `${caption().length}/${CAPTION_MAX}`;
  };
  const caption = () => [el("caption").value.trim(), el("hashtags").value.trim()].filter(Boolean).join("\n\n");
  el("render").addEventListener("change", select);
  ["title", "caption", "hashtags"].forEach((id) => el(id).addEventListener("input", counters));
  view.querySelectorAll("[data-schedule-toggle]").forEach((box) => box.addEventListener("change", () => {
    view.querySelector(`#schedule-${CSS.escape(box.dataset.scheduleToggle)}`).classList.toggle("hidden", !box.checked);
  }));
  el("submit").addEventListener("click", () => submit(view, el, caption));
  select();
  watchJobs(view, jobs);
}

function targetGroups(accounts) {
  return Object.keys(PLATFORMS).map((p) => {
    const own = accounts.filter((a) => a.platform === p);
    if (!own.length) return "";
    return `<div class="platform-group"><h3>${platformIcon(p)}${PLATFORMS[p].label}</h3><div class="check-list">
      ${own.map((a) => `<label class="check"><input type="checkbox" data-account="${esc(a.id)}">
        <span><b>${esc(a.name)}</b> <span class="muted small">${esc(a.handle || "")} · ${esc(nicheLabel(a.niche))}</span>
        ${a.status === "reconnect" ? '<span class="muted small"> · Login abgelaufen</span>' : ""}</span></label>
        ${p === "youtube" ? youtubeOptions(a) : ""}`).join("")}</div>${platformNote(p)}</div>`;
  }).join("");
}

function youtubeOptions(a) {
  const id = esc(a.id);
  return `<div class="check-list" style="padding-left:34px">
    <label class="field" style="margin:0"><span>Sichtbarkeit</span>
      <select class="input" data-privacy="${id}"><option value="private">Privat</option><option value="unlisted">Nicht gelistet</option>
        <option value="public">Öffentlich</option></select></label>
    <label class="check"><input type="checkbox" data-schedule-toggle="${id}"> Zeitgesteuert veröffentlichen</label>
    <input class="input hidden" type="datetime-local" id="schedule-${id}" data-schedule="${id}"></div>`;
}

function platformNote(p) {
  if (p === "youtube") {
    return `<p class="note warn">Solange die Google-Prüfung für deine API-App fehlt, sperrt YouTube per API hochgeladene Videos auf „privat“, egal was hier eingestellt ist.</p>`;
  }
  if (p === "tiktok") {
    return `<p class="note">TikTok bekommt das Video als Entwurf: Öffne danach die Benachrichtigung in der TikTok-App, füge den Text ein und poste. Der Text wird beim Hochladen in die Zwischenablage kopiert.</p>`;
  }
  return `<p class="note">Wird direkt als Reel veröffentlicht (nur mit Creator- oder Business-Konto).</p>`;
}

async function submit(view, el, caption) {
  const targets = [...view.querySelectorAll("input[data-account]:checked")].map((box) => {
    const id = box.dataset.account;
    const privacy = view.querySelector(`[data-privacy="${CSS.escape(id)}"]`)?.value || "private";
    const scheduleToggle = view.querySelector(`[data-schedule-toggle="${CSS.escape(id)}"]`);
    const when = scheduleToggle?.checked ? view.querySelector(`[data-schedule="${CSS.escape(id)}"]`).value : "";
    return { account_id: id, privacy, publish_at: when ? new Date(when).toISOString() : null };
  });
  if (!targets.length) return toast("Bitte mindestens ein Konto auswählen.", true);
  if (!el("title").value.trim()) return toast("Bitte einen Titel eingeben.", true);
  const text = caption();
  if (text.length > CAPTION_MAX) return toast(`Text mit Hashtags ist ${text.length} Zeichen lang, erlaubt sind ${CAPTION_MAX}.`, true);
  const tiktok = targets.some((t) => t.account_id.startsWith("tiktok:"));
  if (!confirm(`„${el("title").value.trim()}“ auf ${targets.length} Konto/Konten hochladen?`)) return;
  el("submit").disabled = true;
  try {
    await api("/api/post", { method: "POST", body: { render: el("render").value, title: el("title").value.trim(), caption: text, targets } });
    if (tiktok && text) {
      try { await navigator.clipboard.writeText(text); el("submit-info").textContent = "Text für TikTok ist in der Zwischenablage."; } catch { /* no clipboard permission */ }
    }
    toast("Upload gestartet.");
    watchJobs(view, await api("/api/posts"));
  } catch (err) {
    toast(err.message, true);
  } finally {
    el("submit").disabled = false;
  }
}

function watchJobs(view, jobs) {
  const box = view.querySelector("#jobs");
  if (!box) return;
  box.innerHTML = jobList(jobs);
  const active = jobs.some((j) => j.targets.some((t) => t.status === "queued" || t.status === "running"));
  clearTimeout(pollTimer);
  if (active) {
    pollTimer = setTimeout(async () => {
      if (!document.body.contains(box)) return;
      watchJobs(view, await api("/api/posts"));
    }, 1500);
  }
}

function jobList(jobs) {
  if (!jobs.length) return '<p class="muted">Noch nichts hochgeladen.</p>';
  return jobs.map((job) => `<div style="margin-bottom:14px"><div><b>${esc(job.title)}</b> <span class="muted small">${fmt.date(job.created_at)} ${fmt.ago(job.created_at)}</span></div>
    ${job.targets.map(targetRow).join("")}</div>`).join("");
}

function targetRow(t) {
  const states = { queued: "Wartet", running: "Läuft", done: "Fertig", error: "Fehler" };
  const progress = t.status === "running" || t.status === "queued"
    ? `<div class="progress"><i style="width:${Math.round((t.progress || 0) * 100)}%"></i></div>` : "";
  const link = t.url && /^https?:\/\//.test(t.url) ? ` · <a href="${esc(t.url)}" target="_blank" rel="noopener noreferrer">ansehen</a>` : "";
  const cls = t.status === "error" ? "delta-down" : t.status === "done" ? "delta-up" : "";
  return `<div class="target-row">${platformIcon(t.platform)}<div><div>${esc(t.account_name || t.account_id)}</div>
    <div class="muted small">${esc(t.message || "")}${link}</div>${progress}</div><span class="${cls}">${states[t.status] || esc(t.status)}</span></div>`;
}
