"""Numbers for the UI, computed from the stored snapshots.

Per-day views come from the platform where it reports them (YouTube Analytics). For TikTok and
Instagram they are the day-to-day growth of the summed video views between two snapshots, so
those curves start once the dashboard has synced on two different days.
"""

import json
from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from .db import Database

PLATFORM_ORDER = "CASE a.platform WHEN 'youtube' THEN 0 WHEN 'tiktok' THEN 1 ELSE 2 END"
LATEST_STATS = """
    SELECT v.*, a.name AS account_name, a.niche, s.day AS stats_day, s.views, s.likes, s.comments, s.shares,
           s.saves, s.reach, s.watch_time_min, s.avg_view_s, s.avg_view_pct, s.subs_gained
    FROM videos v
    JOIN accounts a ON a.id = v.account_id
    LEFT JOIN video_stats s ON s.video_id = v.id
         AND s.day = (SELECT MAX(day) FROM video_stats WHERE video_id = v.id)
"""


def engagement(row: dict) -> float | None:
    views = row.get("views") or 0
    if not views:
        return None
    return sum(row.get(k) or 0 for k in ("likes", "comments", "shares", "saves")) / views * 100


def avg_view_pct(row: dict) -> float | None:
    if row.get("avg_view_pct") is not None:
        return row["avg_view_pct"]
    if row.get("avg_view_s") and row.get("duration_s"):
        return min(row["avg_view_s"] / row["duration_s"] * 100, 999)
    return None


def enrich(row: dict) -> dict:
    return {**row, "engagement": engagement(row), "avg_view_pct": avg_view_pct(row)}


def where_clause(filters: dict) -> tuple[str, list]:
    parts, params = [], []
    for column, key in (("a.platform", "platform"), ("a.id", "account"), ("a.niche", "niche")):
        if filters.get(key):
            parts.append(f"{column} = ?")
            params.append(filters[key])
    return (" WHERE " + " AND ".join(parts)) if parts else "", params


def videos(db: Database, filters: dict) -> list[dict]:
    where, params = where_clause(filters)
    rows = db.query(LATEST_STATS + where + " ORDER BY v.published_at DESC", tuple(params))
    return [enrich(r) for r in rows]


def accounts_filtered(db: Database, filters: dict) -> list[dict]:
    where, params = where_clause(filters)
    return db.query("SELECT a.id, a.platform, a.name, a.handle, a.avatar_url, a.niche, a.status, a.status_message, "
                    "a.last_sync FROM accounts a" + where + " ORDER BY a.niche IS NULL, a.niche, " + PLATFORM_ORDER + ", a.name",
                    tuple(params))


def daily_series(db: Database, account: dict, start: date, end: date) -> dict[str, dict]:
    """day -> {views, watch_time_min} for one account; None where nothing is known."""
    reported = db.query("SELECT day, views, watch_time_min FROM account_daily WHERE account_id = ? AND day BETWEEN ? AND ?",
                        (account["id"], start.isoformat(), end.isoformat()))
    if reported:
        return {r["day"]: {"views": r["views"], "watch_time_min": r["watch_time_min"]} for r in reported}
    rows = db.query(
        """SELECT s.video_id, s.day, s.views, s.watch_time_min, v.published_at FROM video_stats s
           JOIN videos v ON v.id = s.video_id WHERE v.account_id = ? AND s.day BETWEEN ? AND ?""",
        (account["id"], (start - timedelta(days=1)).isoformat(), end.isoformat()))
    by_day: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        by_day[row["day"]][row["video_id"]] = row
    days = sorted(by_day)
    series = {}
    for prev_day, cur_day in zip(days, days[1:]):
        gap = (date.fromisoformat(cur_day) - date.fromisoformat(prev_day)).days
        views = growth(by_day[prev_day], by_day[cur_day], "views", prev_day)
        watch = growth(by_day[prev_day], by_day[cur_day], "watch_time_min", prev_day)
        for i in range(gap):  # spread a multi-day gap evenly over its days
            day = (date.fromisoformat(prev_day) + timedelta(days=i + 1)).isoformat()
            series[day] = {"views": round((views or 0) / gap), "watch_time_min": None if watch is None else watch / gap}
    return series


def growth(prev: dict[str, dict], cur: dict[str, dict], field: str, prev_day: str) -> float | None:
    """Increase of `field` between two snapshots, video by video: a video missing from one snapshot or a
    metric that failed once does not distort the total. Videos published after the first snapshot count fully."""
    total, known = 0.0, False
    for video_id, row in cur.items():
        now = row[field]
        before = prev[video_id][field] if video_id in prev else None
        if now is None:
            continue
        if before is not None:
            total, known = total + max(now - before, 0), True
        elif video_id not in prev and (row["published_at"] or "")[:10] > prev_day:
            total, known = total + now, True
    return total if known else None


def first_data_day(db: Database, account_id: str) -> str | None:
    """First day the dashboard knows numbers for: a platform-reported day or the first sync (even one without videos)."""
    row = db.one("""SELECT MIN(day) AS day FROM (SELECT day FROM account_daily WHERE account_id = ?
                    UNION ALL SELECT day FROM account_stats WHERE account_id = ?)""", (account_id, account_id))
    return row["day"] if row else None


def follower_change(db: Database, account_id: str, start: date) -> tuple[int | None, int | None]:
    """(current followers, change since start) from the daily account snapshots, else YouTube's subs numbers."""
    rows = db.query("SELECT day, followers FROM account_stats WHERE account_id = ? AND followers IS NOT NULL ORDER BY day",
                    (account_id,))
    if not rows:
        return None, None
    current = rows[-1]["followers"]
    before = [r for r in rows if r["day"] <= start.isoformat()]
    if before:
        return current, current - before[-1]["followers"]
    reported = db.one("SELECT SUM(subs_gained) - SUM(subs_lost) AS net FROM account_daily WHERE account_id = ? AND day > ?",
                      (account_id, start.isoformat()))
    return current, reported["net"] if reported and reported["net"] is not None else None


def overview(db: Database, filters: dict, days: int) -> dict:
    end = date.today()
    start = end - timedelta(days=days)
    prev_start = start - timedelta(days=days)
    accounts = accounts_filtered(db, filters)
    all_videos = videos(db, filters)
    day_keys = [(start + timedelta(days=i + 1)).isoformat() for i in range(days)]
    by_platform: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    views_now = views_prev = watch_min = 0.0
    watch_known = False
    prev_covered = True  # the comparison period is only shown when every account has data for all of it
    cards = []
    for account in accounts:
        series = daily_series(db, account, prev_start, end)
        first = first_data_day(db, account["id"])
        if first is None or first > (prev_start + timedelta(days=1)).isoformat():
            prev_covered = False
        cur = {d: v for d, v in series.items() if d > start.isoformat()}
        prev = {d: v for d, v in series.items() if prev_start.isoformat() < d <= start.isoformat()}
        acc_views = sum(v["views"] or 0 for v in cur.values())
        views_now += acc_views
        views_prev += sum(v["views"] or 0 for v in prev.values())
        for day, v in cur.items():
            by_platform[account["platform"]][day] += v["views"] or 0
        acc_watch = [v["watch_time_min"] for v in cur.values() if v["watch_time_min"] is not None]
        if acc_watch:
            watch_known = True
            watch_min += sum(acc_watch)
        followers, delta = follower_change(db, account["id"], start)
        own = [v for v in all_videos if v["account_id"] == account["id"]]
        cards.append({**account, "followers": followers, "followers_delta": delta, "views": acc_views,
                      "watch_hours": sum(acc_watch) / 60 if acc_watch else None, "videos": len(own),
                      "latest": latest_with_rank(own)})
    recent = [v for v in all_videos if (v["published_at"] or "")[:10] > start.isoformat()]
    followers = [c["followers"] for c in cards if c["followers"] is not None]
    deltas = [c["followers_delta"] for c in cards if c["followers_delta"] is not None]
    return {
        "days": days,
        "kpis": {
            "views": views_now,
            "views_prev": views_prev if views_prev and prev_covered else None,
            "watch_hours": watch_min / 60 if watch_known else None,
            "followers": sum(followers) if followers else None,
            "followers_delta": sum(deltas) if deltas else None,
            "avg_view_pct": weighted_pct(recent or all_videos),
            "engagement": weighted_engagement(recent or all_videos),
            "videos_published": len(recent),
        },
        "series": {"days": day_keys,
                   "platforms": {p: [round(by_platform[p].get(d, 0)) for d in day_keys] for p in sorted(by_platform)}},
        "accounts": cards,
        "top_videos": sorted(recent or all_videos, key=lambda v: v["views"] or 0, reverse=True)[:6],
        "niches": niche_table(all_videos),
    }


def latest_with_rank(own: list[dict]) -> dict | None:
    """The newest video of an account plus its rank among the last ten by views (like YouTube Studio)."""
    if not own:
        return None
    ranked = sorted(own, key=lambda v: v["published_at"] or "", reverse=True)[:10]
    newest = ranked[0]
    by_views = sorted(ranked, key=lambda v: v["views"] or 0, reverse=True)
    typical = median([v["views"] or 0 for v in ranked[1:]]) if len(ranked) > 2 else None
    return {**newest, "rank": by_views.index(newest) + 1, "rank_of": len(ranked), "typical_views": typical}


def weighted_pct(rows: list[dict]) -> float | None:
    pairs = [(r["avg_view_pct"], r["views"] or 0) for r in rows if r.get("avg_view_pct") is not None]
    weight = sum(w for _, w in pairs)
    return sum(p * w for p, w in pairs) / weight if weight else None


def weighted_engagement(rows: list[dict]) -> float | None:
    views = sum(r["views"] or 0 for r in rows)
    if not views:
        return None
    return sum(sum(r.get(k) or 0 for k in ("likes", "comments", "shares", "saves")) for r in rows) / views * 100


def niche_table(rows: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[row["niche"] or "ohne Nische"].append(row)
    table = []
    for niche, items in sorted(groups.items()):
        views = [r["views"] or 0 for r in items]
        table.append({"niche": niche, "videos": len(items), "views": sum(views),
                      "median_views": median(views) if views else None,
                      "avg_view_pct": weighted_pct(items), "engagement": weighted_engagement(items),
                      "subs_gained": sum(r["subs_gained"] or 0 for r in items)})
    return table


def video_detail(db: Database, video_id: str) -> dict | None:
    rows = db.query(LATEST_STATS + " WHERE v.id = ?", (video_id,))
    if not rows:
        return None
    video = enrich(rows[0])
    history = db.query("SELECT * FROM video_stats WHERE video_id = ? ORDER BY day", (video_id,))
    retention = db.one("SELECT points FROM retention WHERE video_id = ?", (video_id,))
    siblings = []
    if video["slug"]:
        siblings = [enrich(r) for r in db.query(LATEST_STATS + " WHERE v.slug = ? AND v.id != ?", (video["slug"], video_id))]
    return {"video": video, "history": history, "retention": json.loads(retention["points"]) if retention else None,
            "siblings": siblings}


def by_slug(db: Database, renders: list) -> list[dict]:
    """One row per pipeline short: its numbers on each platform, plus renders not posted anywhere yet."""
    linked = [enrich(r) for r in db.query(LATEST_STATS + " WHERE v.slug IS NOT NULL")]
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in linked:
        groups[row["slug"]].append(row)
    rows = []
    for render in renders:
        items = groups.pop(render.slug, [])
        rows.append(slug_row(render.slug, render.title, render.niche, render.path, items))
    for slug, items in groups.items():  # linked by hand to a slug without a render file
        rows.append(slug_row(slug, items[0]["title"], items[0]["niche"], None, items))
    return rows


def slug_row(slug: str, title: str, niche: str | None, render: str | None, items: list[dict]) -> dict:
    platforms: dict[str, dict] = {}
    for item in items:
        best = platforms.get(item["platform"])
        if best is None or (item["views"] or 0) > (best["views"] or 0):
            platforms[item["platform"]] = item
    return {"slug": slug, "title": title, "niche": niche, "render": render, "platforms": platforms,
            "views": sum(i["views"] or 0 for i in items), "avg_view_pct": weighted_pct(items),
            "engagement": weighted_engagement(items),
            "first_published": min((i["published_at"] for i in items if i["published_at"]), default=None)}
