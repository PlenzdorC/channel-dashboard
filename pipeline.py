"""The finished shorts of the pipeline (<niche>/<N. slug>/renders/<slug>.mp4) and linking platform
videos back to them by slug, so one short can be compared across YouTube, TikTok and Instagram."""

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

PROJECT_RE = re.compile(r"^(\d+)\.\s*(.+)$")
UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


@dataclass(frozen=True)
class Render:
    niche: str
    number: int
    slug: str
    title: str
    path: str  # relative to short-content, forward slashes
    size_mb: float
    modified: float


def find_renders(base: Path) -> list[Render]:
    renders = []
    for kit in base.glob("*/_kit/kit.json"):
        niche_dir = kit.parent.parent
        for project in niche_dir.iterdir():
            match = PROJECT_RE.match(project.name)
            if not project.is_dir() or not match:
                continue
            slug = match.group(2)
            video = project / "renders" / f"{slug}.mp4"
            if not video.is_file():
                continue
            title = slug.replace("-", " ").capitalize()
            try:
                title = json.loads((project / "kit.json").read_text(encoding="utf-8")).get("title") or title
            except (OSError, ValueError):
                pass
            stat = video.stat()
            renders.append(Render(niche_dir.name, int(match.group(1)), slug, title,
                                  video.relative_to(base).as_posix(), round(stat.st_size / 1e6, 1), stat.st_mtime))
    return sorted(renders, key=lambda r: (-r.modified, r.niche, r.number))


def resolve_render(base: Path, rel_path: str) -> Path:
    """Only files that find_renders lists may be served or uploaded."""
    allowed = {r.path for r in find_renders(base)}
    if rel_path not in allowed:
        raise ValueError("unbekanntes Video")
    return base / rel_path


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "").lower().translate(UMLAUTS)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return " ".join(re.findall(r"[a-z0-9]+", text))


def match_slug(title: str, renders: list[Render], niche: str | None = None) -> str | None:
    """Slug of the render a platform video belongs to, from its title or caption.

    A slug matches when all its words appear in order in the title ("hoer-auf-zu-nicken" in
    "Hör auf zu nicken - Das zerstört ..."), or when the render's own title does. Longest match wins.
    """
    haystack = f" {normalize(title)} "
    best: tuple[int, str] | None = None
    for render in renders:
        if niche and render.niche != niche:
            continue
        for needle in (normalize(render.slug.replace("-", " ")), normalize(render.title)):
            if len(needle.split()) >= 2 and f" {needle} " in haystack:
                if best is None or len(needle) > best[0]:
                    best = (len(needle), render.slug)
    return best[1] if best else None
