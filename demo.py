"""Demo mode: a separate database with invented but plausible numbers for nine accounts, so the
dashboard can be explored before any platform is connected. Uploads are simulated."""

import math
import random
import time
from datetime import date, datetime, timedelta, timezone

from .db import Database
from .pipeline import find_renders
from .platforms.base import AccountInfo, DailyPoint, PlatformError, UploadRequest, UploadResult, VideoSnapshot

NAMES = {"finanzklarheit": "Finanzklarheit", "mindmatrix": "MindMatrix", "neurofakt": "NeuroFakt"}
EXTRA_TITLES = {
    "finanzklarheit": ["Warum dein Gehalt schrumpft, obwohl es steigt", "Die 50-30-20-Regel ist kaputt",
                       "So viel kostet dich dein Auto wirklich", "Dispo: die teuerste Bequemlichkeit"],
    "mindmatrix": ["Der Namens-Trick, den jeder Verkäufer kennt", "Schweig drei Sekunden und gewinne",
                   "Die Benjamin-Franklin-Methode", "Spiegeln: sofort sympathisch"],
    "neurofakt": ["Dein Gehirn sortiert jede Nacht aus", "Warum du dich nicht selbst kitzeln kannst",
                  "Der Ohrwurm-Fakt", "Dein Gehirn blendet deine Nase aus"],
}
PLATFORM_REACH = {"youtube": 1.0, "tiktok": 1.6, "instagram": 0.7}
HISTORY_DAYS = 60


class DemoConnector:
    connect_mode = "demo"

    def __init__(self, platform: str):
        self.platform = platform
        self.label = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram"}[platform]

    def missing_config(self) -> list[str]:
        return []

    def fresh(self, creds):
        return creds

    def sync(self, creds):
        raise PlatformError("Demo-Modus: keine echten Daten")

    def connect_with_token(self, token):
        raise PlatformError("Im Demo-Modus werden keine echten Konten verbunden.")

    def upload(self, creds, request: UploadRequest, progress) -> UploadResult:
        for step in range(1, 11):
            time.sleep(0.4)
            progress(step / 10, f"Demo-Upload zu {self.label}")
        if self.platform == "tiktok":
            return UploadResult(None, None, "Demo: Entwurf läge jetzt in deinen TikTok-Benachrichtigungen.")
        return UploadResult(f"demo{int(time.time())}", None, "Demo: nichts wurde wirklich hochgeladen.")


def growth(age_days: float, final: float, tau: float) -> float:
    return final * (1 - math.exp(-max(age_days, 0) / tau))


def retention_curve(rng: random.Random, hold: float) -> list[list[float]]:
    points = []
    for i in range(1, 101):
        x = i / 100
        drop = 1 - (1 - hold) * min(x / 0.08, 1)  # most people leave in the first seconds
        tail = drop * (1 - 0.45 * x) + rng.uniform(-0.015, 0.015)
        points.append([x, round(max(tail, 0.05), 3)])
    return points


def seed(db: Database, base) -> None:
    rng = random.Random(7)
    today = date.today()
    renders = find_renders(base)
    for niche, name in NAMES.items():
        own = sorted([r for r in renders if r.niche == niche], key=lambda r: r.number)
        clips = [(t, None) for t in EXTRA_TITLES[niche]] + [(r.title, r.slug) for r in own]
        start_age = 52
        published = [start_age - i * (start_age - 2) / max(len(clips) - 1, 1) for i in range(len(clips))]
        niche_scale = {"finanzklarheit": 1.3, "mindmatrix": 1.0, "neurofakt": 0.8}[niche]
        for platform, reach in PLATFORM_REACH.items():
            info = AccountInfo(platform, f"demo-{niche}", name, f"@{niche}", "")
            key = db.save_account(info, {}, niche)
            videos = []
            for index, ((title, slug), age) in enumerate(zip(clips, published)):
                final = rng.lognormvariate(math.log(900 * reach * niche_scale * (1 + index * 0.08)), 0.6)
                videos.append((f"{platform}-{niche}-{index}", title, slug, today - timedelta(days=round(age)), final,
                               rng.uniform(1.5, 4.0), rng.uniform(0.55, 0.85)))
            _seed_history(db, key, info, platform, videos, rng, today)


def _seed_history(db: Database, key: str, info: AccountInfo, platform: str, videos: list, rng, today: date) -> None:
    followers0 = rng.randint(3, 40)
    prev_total = 0.0
    for offset in range(HISTORY_DAYS, -1, -1):
        day = today - timedelta(days=offset)
        snaps, total, subs = [], 0.0, 0
        for vid, title, slug, published, final, tau, hold in videos:
            age = (day - published).days
            if age < 0:
                continue
            views = growth(age + 0.5, final, tau)
            total += views
            pct = hold * 70 + rng.uniform(-2, 2)
            subs += int(views * 0.004)
            snaps.append(VideoSnapshot(
                external_id=vid, title=title, url="", published_at=f"{published.isoformat()}T16:00:00Z",
                duration_s=45.0, views=round(views), likes=round(views * rng.uniform(0.03, 0.06)),
                comments=round(views * 0.004), shares=round(views * rng.uniform(0.004, 0.02)),
                saves=round(views * 0.01) if platform == "instagram" else None,
                reach=round(views * 0.8) if platform == "instagram" else None,
                watch_time_min=None if platform == "tiktok" else round(views * 45 * pct / 100 / 60, 1),
                avg_view_s=None if platform == "tiktok" else round(45 * pct / 100, 1),
                avg_view_pct=round(pct, 1) if platform == "youtube" else None,
                subs_gained=int(views * 0.004) if platform == "youtube" else None,
            ))
        account = AccountInfo(info.platform, info.external_id, info.name, info.handle, "",
                              followers=followers0 + subs, total_views=round(total), video_count=len(snaps))
        daily = ()
        if platform == "youtube" and offset >= 2:  # YouTube Analytics lags about two days
            daily = (DailyPoint(day.isoformat(), views=round(total - prev_total),
                                watch_time_min=round((total - prev_total) * 0.45 * 0.65, 1),
                                subs_gained=max(int((total - prev_total) * 0.004), 0), subs_lost=0),)
        retention = {}
        if platform == "youtube" and offset == 0:
            retention = {s.external_id: retention_curve(rng, v[6]) for s, v in zip(snaps, videos)}
        db.store_sync(key, account, tuple(snaps), daily, retention, day=day.isoformat())
        prev_total = total
    with db.connect() as con:
        con.execute("UPDATE accounts SET last_sync = ? WHERE id = ?",
                    (datetime.now(timezone.utc).replace(microsecond=0).isoformat(), key))
        for vid, _, slug, *_ in videos:
            if slug:
                con.execute("UPDATE videos SET slug = ?, slug_source = 'posted' WHERE id = ?", (slug, f"{platform}:{vid}"))


def reset_demo(db_path, base) -> Database:
    for suffix in ("", "-wal", "-shm"):
        path = db_path.with_name(db_path.name + suffix)
        if path.exists():
            path.unlink()
    db = Database(db_path)
    seed(db, base)
    return db
