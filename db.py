"""SQLite storage: accounts with their login, daily snapshots of every video, posting jobs.

Snapshots are keyed by calendar day, so a second sync on the same day overwrites that day's
numbers and the history keeps one point per day - enough for growth curves and day deltas.
"""

import json
import sqlite3
import threading
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

from .platforms.base import AccountInfo, DailyPoint, VideoSnapshot

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY, platform TEXT NOT NULL, external_id TEXT NOT NULL,
    name TEXT, handle TEXT, avatar_url TEXT, niche TEXT,
    credentials TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'ok', status_message TEXT,
    connected_at TEXT, last_sync TEXT
);
CREATE TABLE IF NOT EXISTS account_stats (
    account_id TEXT NOT NULL, day TEXT NOT NULL, followers INTEGER, total_views INTEGER, video_count INTEGER,
    PRIMARY KEY (account_id, day)
);
CREATE TABLE IF NOT EXISTS account_daily (
    account_id TEXT NOT NULL, day TEXT NOT NULL, views INTEGER, watch_time_min REAL,
    subs_gained INTEGER, subs_lost INTEGER,
    PRIMARY KEY (account_id, day)
);
CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY, account_id TEXT NOT NULL, platform TEXT NOT NULL, external_id TEXT NOT NULL,
    title TEXT, url TEXT, thumbnail_url TEXT, published_at TEXT, duration_s REAL,
    slug TEXT, slug_source TEXT
);
CREATE INDEX IF NOT EXISTS videos_account ON videos(account_id);
CREATE TABLE IF NOT EXISTS video_stats (
    video_id TEXT NOT NULL, day TEXT NOT NULL,
    views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER, saves INTEGER, reach INTEGER,
    watch_time_min REAL, avg_view_s REAL, avg_view_pct REAL, subs_gained INTEGER,
    PRIMARY KEY (video_id, day)
);
CREATE TABLE IF NOT EXISTS retention (
    video_id TEXT PRIMARY KEY, points TEXT NOT NULL, fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS post_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, render TEXT, slug TEXT, title TEXT, caption TEXT
);
CREATE TABLE IF NOT EXISTS post_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL, account_id TEXT NOT NULL,
    status TEXT NOT NULL, progress REAL DEFAULT 0, message TEXT, external_id TEXT, url TEXT,
    options TEXT, updated_at TEXT
);
"""

STAT_FIELDS = ("views", "likes", "comments", "shares", "saves", "reach", "watch_time_min", "avg_view_s",
               "avg_view_pct", "subs_gained")
# a later sync on the same day where a metric failed (None) keeps the value an earlier sync got
STATS_UPSERT = (
    f"INSERT INTO video_stats (video_id, day, {', '.join(STAT_FIELDS)}) VALUES (?, ?, {', '.join('?' * len(STAT_FIELDS))}) "
    "ON CONFLICT(video_id, day) DO UPDATE SET "
    + ", ".join(f"{f} = COALESCE(excluded.{f}, video_stats.{f})" for f in STAT_FIELDS)
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def account_id(platform: str, external_id: str) -> str:
    return f"{platform}:{external_id}"


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
        with self.connect() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self.connect() as con:
            return [dict(row) for row in con.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | dict = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # --- accounts ----------------------------------------------------------------------------
    def save_account(self, info: AccountInfo, credentials: dict, niche: str | None = None) -> str:
        key = account_id(info.platform, info.external_id)
        with self.connect() as con:
            con.execute(
                """INSERT INTO accounts (id, platform, external_id, name, handle, avatar_url, niche, credentials,
                                         status, status_message, connected_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ok', NULL, ?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name, handle=excluded.handle,
                       avatar_url=excluded.avatar_url, credentials=excluded.credentials, status='ok',
                       status_message=NULL, niche=COALESCE(accounts.niche, excluded.niche)""",
                (key, info.platform, info.external_id, info.name, info.handle, info.avatar_url, niche,
                 json.dumps(credentials), now_iso()),
            )
        return key

    def accounts(self) -> list[dict]:
        rows = self.query("SELECT * FROM accounts ORDER BY platform, name")
        return [{k: v for k, v in row.items() if k != "credentials"} for row in rows]

    def account(self, key: str) -> dict | None:
        return self.one("SELECT * FROM accounts WHERE id = ?", (key,))

    def credentials(self, key: str) -> dict:
        row = self.one("SELECT credentials FROM accounts WHERE id = ?", (key,))
        return json.loads(row["credentials"]) if row else {}

    def set_niche(self, key: str, niche: str | None) -> None:
        with self.connect() as con:
            con.execute("UPDATE accounts SET niche = ? WHERE id = ?", (niche, key))

    def set_status(self, key: str, status: str, message: str | None = None) -> None:
        with self.connect() as con:
            con.execute("UPDATE accounts SET status = ?, status_message = ? WHERE id = ?", (status, message, key))

    def delete_account(self, key: str) -> None:
        with self.connect() as con:
            video_ids = [r[0] for r in con.execute("SELECT id FROM videos WHERE account_id = ?", (key,))]
            for table in ("video_stats", "retention"):
                con.executemany(f"DELETE FROM {table} WHERE video_id = ?", [(v,) for v in video_ids])
            for table in ("videos", "account_stats", "account_daily"):
                con.execute(f"DELETE FROM {table} WHERE account_id = ?", (key,))
            con.execute("DELETE FROM accounts WHERE id = ?", (key,))

    # --- sync results ------------------------------------------------------------------------
    def store_sync(self, key: str, info: AccountInfo, videos: tuple[VideoSnapshot, ...],
                   daily: tuple[DailyPoint, ...], retention: dict, day: str | None = None) -> None:
        """Credentials are not written here: only fresh_credentials() and save_account() touch them."""
        day = day or date.today().isoformat()
        with self.connect() as con:
            con.execute(
                """UPDATE accounts SET name=?, handle=?, avatar_url=?, status='ok', status_message=NULL, last_sync=?
                   WHERE id=?""",
                (info.name, info.handle, info.avatar_url, now_iso(), key),
            )
            con.execute(
                "INSERT OR REPLACE INTO account_stats VALUES (?, ?, ?, ?, ?)",
                (key, day, info.followers, info.total_views, info.video_count),
            )
            for v in videos:
                vid = f"{info.platform}:{v.external_id}"
                con.execute(
                    """INSERT INTO videos (id, account_id, platform, external_id, title, url, thumbnail_url,
                                           published_at, duration_s)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET title=excluded.title, url=excluded.url,
                           thumbnail_url=excluded.thumbnail_url, published_at=excluded.published_at,
                           duration_s=COALESCE(excluded.duration_s, videos.duration_s), account_id=excluded.account_id""",
                    (vid, key, info.platform, v.external_id, v.title, v.url, v.thumbnail_url, v.published_at, v.duration_s),
                )
                con.execute(STATS_UPSERT, (vid, day, *(getattr(v, f) for f in STAT_FIELDS)))
            con.executemany(
                "INSERT OR REPLACE INTO account_daily VALUES (?, ?, ?, ?, ?, ?)",
                [(key, d.day, d.views, d.watch_time_min, d.subs_gained, d.subs_lost) for d in daily],
            )
            con.executemany(
                "INSERT OR REPLACE INTO retention VALUES (?, ?, ?)",
                [(f"{info.platform}:{vid}", json.dumps(points), now_iso()) for vid, points in retention.items()],
            )

    def set_slug(self, video_id: str, slug: str | None, source: str) -> None:
        """source 'manual' also pins an explicit "not assigned" (slug None) against automatic re-linking."""
        with self.connect() as con:
            con.execute("UPDATE videos SET slug = ?, slug_source = ? WHERE id = ?", (slug, source, video_id))

    def fresh_credentials(self, key: str, connector) -> dict:
        """Refresh the login if it is about to expire and store the result at once, one account at a time,
        so sync and uploads never work from (or overwrite with) an outdated refresh token."""
        with self._locks[key]:
            creds = self.credentials(key)
            fresh = connector.fresh(creds)
            if fresh != creds:
                with self.connect() as con:
                    con.execute("UPDATE accounts SET credentials = ? WHERE id = ?", (json.dumps(fresh), key))
            return fresh

    def register_posted_video(self, key: str, platform: str, external_id: str, title: str, url: str | None,
                              slug: str | None) -> None:
        """A video uploaded from the dashboard: known before the next sync, already linked to its slug."""
        with self.connect() as con:
            con.execute(
                """INSERT INTO videos (id, account_id, platform, external_id, title, url, published_at, slug, slug_source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'posted')
                   ON CONFLICT(id) DO UPDATE SET slug=excluded.slug, slug_source='posted'""",
                (f"{platform}:{external_id}", key, platform, external_id, title, url or "", now_iso(), slug),
            )

    # --- posting -----------------------------------------------------------------------------
    def create_job(self, render: str, slug: str | None, title: str, caption: str, targets: list[dict]) -> int:
        with self.connect() as con:
            job = con.execute("INSERT INTO post_jobs (created_at, render, slug, title, caption) VALUES (?, ?, ?, ?, ?)",
                              (now_iso(), render, slug, title, caption)).lastrowid
            con.executemany(
                "INSERT INTO post_targets (job_id, account_id, status, options, updated_at) VALUES (?, ?, 'queued', ?, ?)",
                [(job, t["account_id"], json.dumps(t.get("options", {})), now_iso()) for t in targets],
            )
        return int(job)

    def update_target(self, target_id: int, **fields) -> None:
        allowed = {k: v for k, v in fields.items() if k in {"status", "progress", "message", "external_id", "url"}}
        sets = ", ".join(f"{k} = ?" for k in allowed)
        with self.connect() as con:
            con.execute(f"UPDATE post_targets SET {sets}, updated_at = ? WHERE id = ?",
                        (*allowed.values(), now_iso(), target_id))

    def jobs(self, limit: int = 20) -> list[dict]:
        jobs = self.query("SELECT * FROM post_jobs ORDER BY id DESC LIMIT ?", (limit,))
        for job in jobs:
            job["targets"] = self.query(
                """SELECT t.*, a.platform, a.name AS account_name FROM post_targets t
                   LEFT JOIN accounts a ON a.id = t.account_id WHERE t.job_id = ? ORDER BY t.id""", (job["id"],))
        return jobs

    def fail_unfinished_targets(self) -> None:
        """After a restart nothing is uploading any more."""
        with self.connect() as con:
            con.execute("""UPDATE post_targets SET status='error', message='Abgebrochen (Dashboard wurde beendet).'
                           WHERE status IN ('queued', 'running')""")
