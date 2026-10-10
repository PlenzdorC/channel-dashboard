"""Fetch every connected account and store today's snapshot; runs on demand, on start and every few hours."""

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import AUTO_SYNC_HOURS, BASE, Settings
from .db import Database
from .pipeline import find_renders, match_slug
from .platforms.base import AuthExpired, Connector, PlatformError

log = logging.getLogger("dashboard.sync")


@dataclass(frozen=True)
class SyncOutcome:
    account_id: str
    ok: bool
    message: str


def sync_account(db: Database, registry: dict[str, Connector], key: str, base: Path = BASE) -> SyncOutcome:
    account = db.account(key)
    if not account:
        return SyncOutcome(key, False, "Konto nicht gefunden")
    connector = registry[account["platform"]]
    try:
        result = connector.sync(db.fresh_credentials(key, connector))
    except AuthExpired as exc:
        db.set_status(key, "reconnect", str(exc))
        return SyncOutcome(key, False, str(exc))
    except PlatformError as exc:
        db.set_status(key, "error", str(exc))
        return SyncOutcome(key, False, str(exc))
    except Exception as exc:  # network errors etc. must not stop the other accounts
        log.exception("sync %s failed", key)
        db.set_status(key, "error", f"Fehler beim Abruf: {exc.__class__.__name__}")
        return SyncOutcome(key, False, str(exc))
    db.store_sync(key, result.account, result.videos, result.daily, result.retention)
    if result.warnings:
        db.set_status(key, "warning", " · ".join(result.warnings)[:500])
    link_slugs(db, base, key)
    return SyncOutcome(key, True, f"{len(result.videos)} Videos")


def link_slugs(db: Database, base: Path, key: str | None = None) -> int:
    """Attach pipeline slugs to videos that have none yet (manual or posted links are never touched)."""
    renders = find_renders(base)
    if not renders:
        return 0
    sql = """SELECT v.id, v.title, a.niche FROM videos v JOIN accounts a ON a.id = v.account_id
             WHERE (v.slug_source IS NULL OR v.slug_source = 'auto')"""
    rows = db.query(sql + (" AND v.account_id = ?" if key else ""), (key,) if key else ())
    linked = 0
    for row in rows:
        slug = match_slug(row["title"] or "", renders, row["niche"])
        if slug:
            db.set_slug(row["id"], slug, "auto")
            linked += 1
    return linked


def sync_all(db: Database, registry: dict[str, Connector], base: Path = BASE) -> list[SyncOutcome]:
    return [sync_account(db, registry, a["id"], base) for a in db.accounts()]


ALL = "*"


class SyncManager:
    """Syncs run one after another in a background worker. Requests arriving meanwhile are queued
    (connecting three accounts in a row syncs all three); the UI polls `state`."""

    def __init__(self, db: Database, registry: dict[str, Connector], settings: Settings):
        self.db, self.registry, self.settings = db, registry, settings
        self._lock = threading.Lock()
        self._pending: list[str] = []
        self.running = False
        self.last_outcomes: list[SyncOutcome] = []
        self.finished_at: str | None = None

    def start(self, key: str | None = None) -> bool:
        """Queue a sync of one account (or all); False only in demo mode."""
        if self.settings.demo:
            return False
        with self._lock:
            item = key or ALL
            if item not in self._pending:
                self._pending.append(item)
            if self.running:
                return True
            self.running = True
            self.last_outcomes = []
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self._pending:
                    self.running = False
                    self.finished_at = datetime.now(timezone.utc).isoformat()
                    return
                item = self._pending.pop(0)
            try:
                if item == ALL:
                    outcomes = sync_all(self.db, self.registry, self.settings.base)
                else:
                    outcomes = [sync_account(self.db, self.registry, item, self.settings.base)]
            except Exception:  # keep the worker alive for the queued requests
                log.exception("sync run failed")
                outcomes = [SyncOutcome(item, False, "Abruf abgebrochen")]
            self.last_outcomes = [*self.last_outcomes, *outcomes]

    def state(self) -> dict:
        return {"running": self.running, "finished_at": self.finished_at,
                "outcomes": [o.__dict__ for o in self.last_outcomes]}

    def is_stale(self) -> bool:
        last = [a["last_sync"] for a in self.db.accounts() if a["last_sync"]]
        if not last:
            return bool(self.db.accounts())
        newest = datetime.fromisoformat(max(last))
        return datetime.now(timezone.utc) - newest > timedelta(hours=AUTO_SYNC_HOURS)

    def run_periodically(self, check_every_s: float = 15 * 60) -> None:
        """Sync now if the data is older than AUTO_SYNC_HOURS, then keep checking while the server runs."""
        def loop() -> None:
            while True:
                if self.is_stale():
                    self.start()
                time.sleep(check_every_s)
        threading.Thread(target=loop, daemon=True).start()
