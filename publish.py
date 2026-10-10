"""Posting a finished short to several accounts at once; each target uploads in a worker thread."""

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .db import Database
from .pipeline import find_renders, resolve_render
from .platforms.base import AuthExpired, Connector, PlatformError, UploadRequest

log = logging.getLogger("dashboard.publish")
PRIVACY = {"private", "unlisted", "public"}


@dataclass(frozen=True)
class Target:
    account_id: str
    privacy: str = "private"
    publish_at: str | None = None


class Publisher:
    def __init__(self, db: Database, registry: dict[str, Connector], base: Path, workers: int = 2):
        self.db, self.registry, self.base = db, registry, base
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="upload")

    def submit(self, render: str, title: str, caption: str, targets: list[Target]) -> int:
        path = resolve_render(self.base, render)  # ValueError for anything that is not a pipeline render
        if not targets:
            raise ValueError("Kein Konto ausgewählt")
        accounts = {}
        for target in targets:
            account = self.db.account(target.account_id)
            if not account:
                raise ValueError(f"Unbekanntes Konto {target.account_id}")
            if target.privacy not in PRIVACY:
                raise ValueError(f"Ungültige Sichtbarkeit {target.privacy}")
            accounts[target.account_id] = account
        slug = next((r.slug for r in find_renders(self.base) if r.path == render), None)
        job = self.db.create_job(render, slug, title, caption, [
            {"account_id": t.account_id, "options": {"privacy": t.privacy, "publish_at": t.publish_at}} for t in targets])
        target_rows = self.db.query("SELECT id FROM post_targets WHERE job_id = ? ORDER BY id", (job,))
        for row, target in zip(target_rows, targets):
            request = UploadRequest(path=path, title=title, caption=caption, privacy=target.privacy,
                                    publish_at=target.publish_at)
            self.pool.submit(self._run, row["id"], accounts[target.account_id], request, slug)
        return job

    def _run(self, target_id: int, account: dict, request: UploadRequest, slug: str | None) -> None:
        connector = self.registry[account["platform"]]

        def progress(fraction: float, message: str) -> None:
            self.db.update_target(target_id, status="running", progress=round(min(max(fraction, 0), 1), 3), message=message)

        progress(0, "Startet")
        try:
            result = connector.upload(self.db.fresh_credentials(account["id"], connector), request, progress)
        except AuthExpired as exc:
            self.db.set_status(account["id"], "reconnect", str(exc))
            self.db.update_target(target_id, status="error", message=str(exc))
            return
        except PlatformError as exc:
            self.db.update_target(target_id, status="error", message=str(exc))
            return
        except Exception as exc:
            log.exception("upload to %s failed", account["id"])
            self.db.update_target(target_id, status="error", message=f"Unerwarteter Fehler: {exc.__class__.__name__}")
            return
        if result.external_id:
            self.db.register_posted_video(account["id"], account["platform"], result.external_id, request.title,
                                          result.url, slug)
        self.db.update_target(target_id, status="done", progress=1, message=result.message,
                              external_id=result.external_id, url=result.url)
