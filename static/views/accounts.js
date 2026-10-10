// Konten: connect any number of accounts per platform, set the niche, see login problems, disconnect.

import { app } from "../app.js";
import { api, avatar, esc, fmt, nicheLabel, platformIcon, PLATFORMS, toast } from "../util.js";

const SETUP = {
  youtube: (p) => `<ol>
    <li>In der <a href="https://console.cloud.google.com/" target="_blank" rel="noopener noreferrer">Google Cloud Console</a> ein Projekt anlegen und
      „YouTube Data API v3“ sowie „YouTube Analytics API“ aktivieren.</li>
    <li>OAuth-Zustimmungsbildschirm: Typ „Extern“, dich als Testnutzer eintragen, danach auf „In Produktion“ stellen
      (sonst läuft der Login nach 7 Tagen ab).</li>
    <li>Anmeldedaten → OAuth-Client-ID → Typ <b>Desktop-App</b>. Client-ID und Secret in <code>dashboard/.env</code> eintragen:
      <code>YOUTUBE_CLIENT_ID=…</code> und <code>YOUTUBE_CLIENT_SECRET=…</code>, dann das Dashboard neu starten.</li>
    <li>Für jeden Kanal einmal „Konto verbinden“ klicken. Bei Brand-Kanälen wählst du im Google-Login den Kanal aus.</li></ol>
    <p class="muted small">Weiterleitungs-URL (wird automatisch genutzt): <code>${esc(p.redirect_uri)}</code></p>`,
  tiktok: (p) => `<ol>
    <li>Auf <a href="https://developers.tiktok.com/" target="_blank" rel="noopener noreferrer">developers.tiktok.com</a> eine App anlegen
      (als Einzelperson) und die Produkte <b>Login Kit</b>, <b>Display API</b> und <b>Content Posting API</b> hinzufügen.</li>
    <li>Plattform „Desktop“ wählen, als Redirect-URI eintragen: <code>${esc(p.redirect_uri)}</code></li>
    <li>Eine <b>Sandbox</b> anlegen und dort deine TikTok-Konten (bis zu 10) als Target User hinzufügen. Dafür ist keine Prüfung durch TikTok nötig.</li>
    <li>Client Key und Secret der Sandbox in <code>dashboard/.env</code> eintragen: <code>TIKTOK_CLIENT_KEY=…</code> und <code>TIKTOK_CLIENT_SECRET=…</code>,
      dann das Dashboard neu starten.</li></ol>`,
  instagram: () => `<ol>
    <li>Das Instagram-Konto in der App auf <b>Creator</b> umstellen (Einstellungen → Kontotyp und Tools). Das ist kostenlos und braucht keine Verifizierung.</li>
    <li>Auf <a href="https://developers.facebook.com/" target="_blank" rel="noopener noreferrer">developers.facebook.com</a> („Los geht's“, falls noch kein
      Entwicklerkonto) → Meine Apps → App erstellen mit dem Anwendungsfall „Nachrichten und Inhalte auf Instagram verwalten“.</li>
    <li>Anwendungsfälle → Anpassen → <b>API-Einrichtung mit Instagram-Login</b> → „Zugriffsschlüssel generieren“ → „Konto hinzufügen“,
      im Popup mit dem Instagram-Konto anmelden.</li>
    <li>Neben dem Konto „Token generieren“, Token kopieren (wird nur einmal angezeigt) und hier einfügen.</li>
    <li>Meldet Meta eine fehlende Rolle: App-Rollen → Rollen → Instagram-Tester einladen und in der Instagram-App unter
      Einstellungen → Website-Berechtigungen → Apps und Websites → Tester-Einladungen annehmen.</li></ol>`,
};

export async function renderAccounts(view, params) {
  if (params.get("verbunden")) toast(`Verbunden: ${params.get("verbunden")}. Die Zahlen werden jetzt geholt.`);
  if (params.get("fehler")) toast(params.get("fehler"), true);
  if (params.has("verbunden") || params.has("fehler")) history.replaceState(null, "", "#konten");
  const meta = app.meta;
  view.innerHTML = meta.platforms.map((p) => platformCard(p, meta)).join("") +
    `<p class="muted small" style="margin-top:16px">Die Zugangsdaten bleiben auf diesem PC (<code>dashboard/data/dashboard.db</code>). Ausführliche Anleitung: <code>dashboard/README.md</code>.</p>`;
  bind(view);
}

function platformCard(p, meta) {
  const accounts = meta.accounts.filter((a) => a.platform === p.id);
  const ready = !p.missing.length;
  const connect = meta.demo ? '<span class="muted small">Im Demo-Modus nicht verfügbar</span>'
    : p.connect_mode === "oauth"
      ? `<a class="btn primary ${ready ? "" : "hidden"}" href="/connect/${p.id}">+ Konto verbinden</a>`
      : "";
  const setupOpen = !accounts.length || !ready;
  return `<section class="card platform-card">
    <header>${platformIcon(p.id)}<h2>${PLATFORMS[p.id].label}</h2><span class="muted">${accounts.length} verbunden</span>${connect}</header>
    ${!ready ? `<p class="note warn">Es fehlen noch Einträge in <code>.env</code>: ${p.missing.map(esc).join(", ")}</p>` : ""}
    <details class="setup" ${setupOpen ? "open" : ""}><summary><b>Einrichtung</b></summary>${SETUP[p.id](p)}</details>
    ${p.connect_mode === "token" && !meta.demo ? `<div class="token-form">
      <input class="input" type="password" id="ig-token" placeholder="Instagram-Zugriffsschlüssel einfügen" autocomplete="off">
      <select class="input" id="ig-niche" style="max-width:180px"><option value="">Nische …</option>
        ${meta.niches.map((n) => `<option value="${esc(n)}">${esc(nicheLabel(n))}</option>`).join("")}</select>
      <button class="btn primary" type="button" id="ig-connect">Verbinden</button></div>` : ""}
    ${accounts.map((a) => accountRow(a, meta)).join("")}
  </section>`;
}

function accountRow(a, meta) {
  const status = { ok: "Aktuell", warning: "Teilweise", error: "Fehler", reconnect: "Neu verbinden" }[a.status] || a.status;
  const message = a.status !== "ok" && a.status_message ? `<div class="small ${a.status === "warning" ? "muted" : "delta-down"}">${esc(a.status_message)}</div>` : "";
  const reconnect = a.status === "reconnect" && a.platform !== "instagram" ? `<a class="btn" href="/connect/${esc(a.platform)}">Neu verbinden</a>` : "";
  return `<div class="account-row">${avatar(a)}
    <div><div><b>${esc(a.name)}</b> <span class="muted small">${esc(a.handle || "")}</span></div>
      <div class="muted small"><span class="status-dot status-${esc(a.status)}"></span> ${esc(status)} · abgerufen ${esc(fmt.ago(a.last_sync))}</div>${message}</div>
    <select class="input" data-niche="${esc(a.id)}" aria-label="Nische">
      <option value="">ohne Nische</option>${meta.niches.map((n) => `<option value="${esc(n)}" ${n === a.niche ? "selected" : ""}>${esc(nicheLabel(n))}</option>`).join("")}</select>
    <div class="actions">${reconnect}
      <button class="btn" type="button" data-sync="${esc(a.id)}" ${meta.demo ? "disabled" : ""}>Abrufen</button>
      <button class="btn danger" type="button" data-remove="${esc(a.id)}" data-name="${esc(a.name)}">Trennen</button></div>
  </div>`;
}

function bind(view) {
  view.querySelectorAll("[data-niche]").forEach((sel) => sel.addEventListener("change", async () => {
    try {
      await api(`/api/accounts/${encodeURIComponent(sel.dataset.niche)}`, { method: "POST", body: { niche: sel.value || null } });
      toast("Nische gespeichert.");
    } catch (err) { toast(err.message, true); }
  }));
  view.querySelectorAll("[data-sync]").forEach((btn) => btn.addEventListener("click", async () => {
    try {
      const res = await api("/api/sync", { method: "POST", body: { account_id: btn.dataset.sync } });
      toast(res.started ? "Abruf gestartet." : "Es läuft bereits ein Abruf.");
      app.rerender();
    } catch (err) { toast(err.message, true); }
  }));
  view.querySelectorAll("[data-remove]").forEach((btn) => btn.addEventListener("click", async () => {
    if (!confirm(`„${btn.dataset.name}“ trennen? Gespeicherte Zahlen dieses Kontos werden gelöscht.`)) return;
    try {
      await api(`/api/accounts/${encodeURIComponent(btn.dataset.remove)}`, { method: "DELETE" });
      toast("Konto getrennt.");
      app.rerender();
    } catch (err) { toast(err.message, true); }
  }));
  view.querySelector("#ig-connect")?.addEventListener("click", async () => {
    const token = view.querySelector("#ig-token").value.trim();
    if (!token) return toast("Bitte den Zugriffsschlüssel einfügen.", true);
    try {
      const res = await api("/api/connect/instagram", { method: "POST", body: { token, niche: view.querySelector("#ig-niche").value || null } });
      toast(`Verbunden: ${res.name}. Die Zahlen werden jetzt geholt.`);
      app.rerender();
    } catch (err) { toast(err.message, true); }
  });
}
