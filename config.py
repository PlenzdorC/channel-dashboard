"""Paths, server address and the app credentials the dashboard reads from short-content/.env or dashboard/.env."""

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
BASE = PACKAGE.parent  # short-content/
STATIC = PACKAGE / "static"
DATA_DIR = PACKAGE / "data"
ENV_FILES = (BASE / ".env", PACKAGE / ".env")  # short-content/.env, then dashboard/.env (wins on conflicts)

HOST = "127.0.0.1"
PORT = int(os.environ.get("DASHBOARD_PORT", "8765"))
BASE_URL = f"http://{HOST}:{PORT}"

AUTO_SYNC_HOURS = 6
ENV_PREFIXES = ("YOUTUBE_", "TIKTOK_", "INSTAGRAM_")


def read_env(paths: tuple[Path, ...] = ENV_FILES) -> dict[str, str]:
    """KEY=VALUE lines of the .env files (later files win), overridden by real environment variables."""
    values: dict[str, str] = {}
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    overrides = {k: v for k, v in os.environ.items() if k.startswith(ENV_PREFIXES)}
    return {**values, **overrides}


def find_niches(base: Path = BASE) -> tuple[str, ...]:
    """Every channel folder with a kit (short-content/<niche>/_kit/kit.json)."""
    return tuple(sorted(p.parent.parent.name for p in base.glob("*/_kit/kit.json")))


@dataclass(frozen=True)
class Settings:
    db_path: Path
    demo: bool = False
    env: dict[str, str] = field(default_factory=dict, repr=False)  # holds client secrets
    base: Path = BASE

    def get(self, key: str, default: str = "") -> str:
        return self.env.get(key, default).strip()

    @property
    def redirect_base(self) -> str:
        return BASE_URL


def load_settings(demo: bool = False) -> Settings:
    DATA_DIR.mkdir(exist_ok=True)
    name = "demo.db" if demo else "dashboard.db"
    return Settings(db_path=DATA_DIR / name, demo=demo, env=read_env())
