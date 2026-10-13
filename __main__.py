"""Kanal-Dashboard. Run from short-content/ with the shared venv:

    .venv\\Scripts\\python -m dashboard          start the dashboard (opens the browser)
    .venv\\Scripts\\python -m dashboard demo     same with invented demo numbers
    .venv\\Scripts\\python -m dashboard sync     fetch all accounts once (for the Windows task scheduler)

DASHBOARD_HOSTS in .env serves it to the home network instead of this PC only (README: "Auf einem Heimserver").
"""

import logging
import os
import sys
import threading
import webbrowser

from .config import load_settings
from .db import Database
from .platforms import build_registry
from .publish import Publisher
from .sync import SyncManager, sync_all


def serve(demo: bool) -> None:
    import uvicorn

    from .server import create_app

    settings = load_settings(demo=demo)
    if demo:
        from .demo import reset_demo
        db = reset_demo(settings.db_path, settings.base)
    else:
        db = Database(settings.db_path)
    db.fail_unfinished_targets()
    registry = build_registry(settings)
    sync = SyncManager(db, registry, settings)
    app = create_app(settings, db, registry, sync, Publisher(db, registry, settings.base))
    sync.run_periodically()
    on_server = bool(settings.network_names)
    if not on_server and "--no-browser" not in sys.argv and not os.environ.get("DASHBOARD_NO_BROWSER"):
        threading.Timer(1.2, lambda: webbrowser.open(settings.url)).start()
    print(f"Dashboard läuft auf {settings.url}{'  (Demo)' if demo else ''}  -  beenden mit Strg+C")
    if on_server and not settings.password:
        print("Hinweis: Ohne DASHBOARD_PASSWORD in .env kann jedes Gerät im Heimnetz das Dashboard bedienen.")
    uvicorn.run(app, host=settings.listen_host, port=settings.port, log_level="warning")


def sync_once() -> int:
    settings = load_settings()
    db = Database(settings.db_path)
    outcomes = sync_all(db, build_registry(settings), settings.base)
    for o in outcomes:
        print(f"{'ok ' if o.ok else 'FEHLER'} {o.account_id}: {o.message}")
    return 0 if all(o.ok for o in outcomes) else 1


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    sys.stdout.reconfigure(encoding="utf-8")
    command = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if command == "sync":
        return sync_once()
    if command in ("serve", "demo"):
        serve(demo=command == "demo")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
