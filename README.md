# Kanal-Dashboard

Zahlen und Posting für beliebig viele YouTube-, TikTok- und Instagram-Konten an einem Ort.
Läuft lokal auf diesem PC (`http://127.0.0.1:8765`) oder auf einem [Server im Heimnetz](#auf-einem-heimserver).
Logins und Zahlen bleiben in `dashboard/data/`.

## Starten

Vom Ordner `short-content` aus:

```bash
.venv\Scripts\python -m dashboard
```

| Befehl | Zweck |
|---|---|
| `python -m dashboard` | Dashboard starten, öffnet den Browser |
| `python -m dashboard demo` | Mit erfundenen Zahlen ausprobieren (eigene Datenbank, nichts wird hochgeladen) |
| `python -m dashboard sync` | Alle Konten einmal abrufen, z. B. per Windows-Aufgabenplanung |

Abhängigkeiten (einmalig): `.venv\Scripts\python -m pip install -r dashboard\requirements.txt`

Solange das Dashboard läuft, holt es die Zahlen alle 6 Stunden selbst. Täglich mindestens einmal abrufen lohnt sich:
TikTok und Instagram liefern nur Gesamtzahlen pro Video, die Tageskurve entsteht aus dem Zuwachs zwischen zwei Abrufen.

## Bereiche

- **Übersicht**: Aufrufe, Wiedergabezeit, Follower, Ø angesehen und Interaktionsrate für 7/28/90/365 Tage, Aufrufe pro Tag
  nach Plattform, pro Konto das neueste Video (wie „Leistung des neuesten Shorts“ in YouTube Studio) mit Rang und üblichem Wert,
  Top-Videos und Nischen-Vergleich. Filter nach Plattform, Nische und Konto.
- **Videos**: alle Videos sortierbar; Klick öffnet Details mit Zuschauerbindungs-Kurve (YouTube), Aufruf-Verlauf und demselben
  Short auf den anderen Plattformen.
- **Shorts**: jedes fertige Pipeline-Video (`<nische>/<N. slug>/renders/<slug>.mp4`) mit seinen Zahlen auf allen drei Plattformen nebeneinander.
  Die Zuordnung läuft automatisch über den Titel, in der Video-Ansicht lässt sie sich von Hand ändern.
- **Posten**: Short wählen, Titel und Text einmal schreiben, Konten ankreuzen (passend zur Nische vorausgewählt), hochladen.
- **Konten**: beliebig viele Konten pro Plattform verbinden, Nische zuordnen, Login-Probleme sehen, trennen.

## Was jede Plattform liefert

| | YouTube | TikTok (auch privates Konto) | Instagram (nur Creator/Business) |
|---|---|---|---|
| Aufrufe, Likes, Kommentare | ✓ | ✓ | ✓ |
| Shares | ✓ | ✓ | ✓ |
| Saves, Reichweite | – | – | ✓ |
| Wiedergabezeit, Ø Dauer | ✓ | – | ✓ |
| Ø % angesehen | ✓ | – | ✓ (berechnet aus Ø Dauer und Videolänge) |
| Zuschauerbindungs-Kurve | ✓ (neueste 10 Videos) | – | – |
| Follower pro Video | ✓ | – | – |
| Posten | Upload; **ohne Google-Prüfung immer privat** | als **Entwurf** in die TikTok-App | direkt als Reel |

## Einrichtung

Zugangsdaten der Apps kommen in `dashboard/.env` (oder `short-content/.env`; bei doppelten Einträgen gilt `dashboard/.env`).
Danach das Dashboard neu starten, es liest die Dateien nur beim Start.

### YouTube

1. [Google Cloud Console](https://console.cloud.google.com/): neues Projekt, unter „APIs & Dienste“ **YouTube Data API v3** und
   **YouTube Analytics API** aktivieren.
2. OAuth-Zustimmungsbildschirm: Typ „Extern“, App-Name beliebig, dich selbst als Testnutzer eintragen.
   Danach **„App veröffentlichen“ / In Produktion** stellen: Im Testmodus laufen die Logins nach 7 Tagen ab. Google zeigt beim Login
   dann „Diese App wurde nicht überprüft“ → „Erweitert“ → „Weiter zu …“. Für eigene Konten ist das in Ordnung.
3. Anmeldedaten → „OAuth-Client-ID erstellen“ → Anwendungstyp **Desktop-App**.
4. In `.env`:
   ```
   YOUTUBE_CLIENT_ID=…apps.googleusercontent.com
   YOUTUBE_CLIENT_SECRET=…
   ```
5. Im Dashboard unter Konten → YouTube → „Konto verbinden“, einmal pro Kanal. Bei mehreren Kanälen unter einem Google-Login
   (Brand-Kanäle) fragt Google, welcher Kanal gemeint ist.

**Öffentlich posten:** Google sperrt Videos, die über eine ungeprüfte API-App hochgeladen werden, auf „privat“. Für öffentliche Uploads
in der Cloud Console einen Antrag auf Prüfung stellen („YouTube API Services – Audit and Quota Extension“). Bis dahin: Uploads landen
privat. Das Dashboard verlinkt sie, und du kannst sie in YouTube Studio prüfen.

### TikTok

1. [developers.tiktok.com](https://developers.tiktok.com/) → „Manage apps“ → App anlegen (als Einzelperson, keine Firma nötig).
2. Produkte hinzufügen: **Login Kit**, **Display API**, **Content Posting API** (Upload als Entwurf, „Direct Post“ nicht nötig).
3. Bei Login Kit die Plattform **Desktop** wählen und als Redirect-URI eintragen: `http://127.0.0.1:8765/oauth/tiktok/callback/`
4. Oben auf **Sandbox** umschalten, eine Sandbox anlegen und unter „Target Users“ deine TikTok-Konten hinzufügen (bis zu 10).
   In der Sandbox ist keine App-Prüfung durch TikTok nötig.
5. Client Key und Client Secret **der Sandbox** in `.env`:
   ```
   TIKTOK_CLIENT_KEY=…
   TIKTOK_CLIENT_SECRET=…
   ```
6. „Konto verbinden“: TikTok zeigt den Login. Für ein zweites Konto vorher auf tiktok.com abmelden oder das Konto dort wechseln.

Sollte TikTok einen Scope in der Sandbox nicht freigeben, lässt er sich über `TIKTOK_SCOPES=user.info.basic,user.info.profile,user.info.stats,video.list`
in `.env` weglassen. Dann geht nur das Posten nicht.

### Instagram

Private Instagram-Konten haben seit Dezember 2024 keine Schnittstelle mehr. Nötig ist ein **Creator-Konto** (kostenlos, ohne Verifizierung):
Instagram-App → Einstellungen → Kontotyp und Tools → Zu professionellem Konto wechseln → Creator.

1. [developers.facebook.com](https://developers.facebook.com/) → „Los geht's“ (einmalig als Entwickler registrieren, braucht ein
   Facebook-Konto) → oben rechts **Meine Apps** → **App erstellen** → Anwendungsfall **„Nachrichten und Inhalte auf Instagram verwalten“**.
2. In der App: **Anwendungsfälle** → beim Instagram-Fall **Anpassen** → links **„API-Einrichtung mit Instagram-Login“**.
3. Abschnitt **„Zugriffsschlüssel generieren“** → **„Konto hinzufügen“** → Weiter → im Popup mit dem Instagram-Konto anmelden →
   Speichern → Verstanden.
4. Neben dem Konto **„Token generieren“** → bestätigen → Token kopieren (wird nur einmal angezeigt).
5. Im Dashboard unter Konten → Instagram einfügen, Nische wählen, Verbinden. Der Token gilt 60 Tage, das Dashboard verlängert ihn wöchentlich.
   Für jedes weitere Konto die Schritte 3–5 wiederholen.

Meldet Meta bei Schritt 3 eine fehlende Rolle: App-Rollen → Rollen → **Instagram-Tester** einladen und in der Instagram-App annehmen
(Einstellungen → Website-Berechtigungen → Apps und Websites → Tester-Einladungen).
Für App-Einstellungen, die nach Datenschutz, Nutzungsbedingungen oder Datenlöschung fragen: die Seiten unter
`https://plenzdorc.github.io/kanal-dashboard/` (Datenlöschung: `datenschutz.html#loeschung`), App-Icon: `dashboard/app-icon-1024.png`.

## Täglicher Abruf ohne geöffnetes Dashboard (optional)

Windows-Aufgabenplanung → Einfache Aufgabe → täglich → Programm `C:\Projekte\Claude\short-content\.venv\Scripts\python.exe`,
Argumente `-m dashboard sync`, Starten in `C:\Projekte\Claude\short-content`.

## Auf einem Heimserver

Das Dashboard kann auch auf einem kleinen Rechner zu Hause laufen (Raspberry Pi, alter Laptop, Python 3.10 oder neuer).
Dann öffnest du es von jedem Gerät im Heimnetz, z. B. unter `http://192.168.178.138:8765`, und es holt die Zahlen rund um die Uhr selbst.
Die Windows-Aufgabenplanung ist dann nicht mehr nötig. Von außerhalb des Heimnetzes ist es nicht erreichbar.

1. **Feste Adresse:** In der FRITZ!Box unter Heimnetz → Netzwerk beim Server auf „Bearbeiten“ →
   „Diesem Netzwerkgerät immer die gleiche IPv4-Adresse zuweisen“.
2. **Installieren** (Beispiel Linux). Der Ordner muss `dashboard` heißen, gestartet wird aus dem Ordner darüber:
   ```bash
   mkdir ~/kanal && cd ~/kanal
   git clone https://github.com/PlenzdorC/channel-dashboard.git dashboard   # oder den Ordner dashboard vom PC kopieren
   python3 -m venv .venv
   .venv/bin/pip install -r dashboard/requirements.txt
   ```
3. **`dashboard/.env`** mit den Zugangsdaten der Apps wie oben, dazu:
   ```
   DASHBOARD_HOSTS=192.168.178.138
   DASHBOARD_PASSWORD=ein-eigenes-passwort
   ```
   `DASHBOARD_HOSTS` sind die Adressen, unter denen du den Server aufrufst (mehrere mit Komma, z. B. `192.168.178.138,heimserver.fritz.box`).
   Ist es gesetzt, ist das Dashboard im ganzen Heimnetz erreichbar statt nur auf dem eigenen Rechner.
   Mit `DASHBOARD_PASSWORD` fragt der Browser einmal nach dem Passwort (Benutzername egal). Ohne Passwort kann jedes Gerät im WLAN,
   auch das von Gästen, Zahlen sehen, posten und Konten trennen. Das Passwort geht unverschlüsselt durchs Heimnetz, also kein wichtiges wiederverwenden.
4. **Starten:** `.venv/bin/python -m dashboard` im Ordner `~/kanal`. Dauerhaft als Dienst, der mit dem Server startet
   (`<benutzer>` ersetzen):
   ```bash
   sudo tee /etc/systemd/system/kanal-dashboard.service >/dev/null <<'EOF'
   [Unit]
   Description=Kanal-Dashboard
   After=network-online.target
   Wants=network-online.target

   [Service]
   User=<benutzer>
   WorkingDirectory=/home/<benutzer>/kanal
   ExecStart=/home/<benutzer>/kanal/.venv/bin/python -m dashboard
   Environment=PYTHONUNBUFFERED=1
   Restart=on-failure

   [Install]
   WantedBy=multi-user.target
   EOF
   sudo systemctl enable --now kanal-dashboard
   ```
   Meldungen: `journalctl -u kanal-dashboard -f`. Update: `cd ~/kanal/dashboard && git pull && sudo systemctl restart kanal-dashboard`.
   Läuft auf dem Server eine Firewall, Port 8765 fürs Heimnetz freigeben (ufw: `sudo ufw allow from 192.168.178.0/24 to any port 8765 proto tcp`).

**Bisherige Konten mitnehmen:** Dashboard auf PC und Server beenden, den Ordner `dashboard/data/` vom PC auf den Server kopieren.
Konten und Verlauf sind dann sofort da. Danach nur noch das Dashboard auf dem Server benutzen: Zwei Kopien mit denselben Logins
kommen sich beim Erneuern der Logins in die Quere.

**Konten verbinden:** YouTube und TikTok schicken dich nach dem Login an `127.0.0.1` zurück, also an das Gerät, an dem du gerade sitzt.
An den Einstellungen der Apps ändert sich deshalb nichts. Unter Konten öffnet „Konto verbinden“ den Login in einem neuen Tab, der am Ende
„Seite nicht erreichbar“ zeigt. Die Adresse aus dessen Adresszeile (`http://127.0.0.1:8765/oauth/…?code=…`) kopieren, im Dashboard-Tab
in das Feld unter der Plattform einfügen und „Login abschließen“ klicken, innerhalb von 10 Minuten. Läuft auf dem Gerät noch das lokale
Dashboard, vorher beenden, sonst fängt es den Login ab. Instagram geht wie gewohnt per Token.

**Shorts und Posten** brauchen die fertigen Videos (`<nische>/<N. slug>/renders/`). Entstehen die auf dem PC, den Ordner `short-content`
auf den Server spiegeln (z. B. mit Syncthing) oder als Netzlaufwerk einbinden und in `.env` angeben:
```
DASHBOARD_SHORT_CONTENT=/home/<benutzer>/short-content
```
Die Nischen liest das Dashboard beim Start aus diesem Ordner. Übersicht, Videos und Konten funktionieren auch ohne ihn.

## Technik

```text
dashboard/
  __main__.py      Start: serve | demo | sync
  config.py        Pfade, Port, .env lesen
  server.py        FastAPI: JSON-API, OAuth-Callbacks, Video-Vorschau
  db.py            SQLite: Konten (inkl. Login), Tages-Snapshots pro Video, Upload-Jobs
  sync.py          Abruf aller Konten, Slug-Zuordnung
  metrics.py       Kennzahlen, Tageskurven, Vergleiche
  publish.py       Uploads in Hintergrund-Threads
  pipeline.py      fertige Shorts finden, Titel → Slug
  platforms/       youtube.py, tiktok.py, instagram.py (je Login, Abruf, Upload)
  demo.py          Demo-Daten
  static/          Oberfläche (HTML/CSS/JS ohne Build-Schritt, Diagramme als eigenes SVG)
  tests/           pytest mit simulierten API-Antworten
```

Tests: `.venv\Scripts\python -m pytest dashboard\tests -q`

Sicherheit: Der Server hört nur auf 127.0.0.1 (mit `DASHBOARD_HOSTS` im Heimnetz), akzeptiert nur die Hosts `127.0.0.1`/`localhost`
und die in `DASHBOARD_HOSTS` und schreibende Anfragen nur als JSON (fremde Webseiten können so nichts auslösen). Mit `DASHBOARD_PASSWORD`
verlangt er für jede Anfrage das Passwort. Die Vorschau und das Posten erlauben nur Dateien aus `renders/`.
Tokens stehen unverschlüsselt in `dashboard/data/dashboard.db`; den Ordner nicht teilen oder hochladen.
