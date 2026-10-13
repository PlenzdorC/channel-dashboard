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
LOOPBACK_NAMES = (HOST, "localhost")
DEFAULT_PORT = 8765

AUTO_SYNC_HOURS = 6
ENV_PREFIXES = ("YOUTUBE_", "TIKTOK_", "INSTAGRAM_", "DASHBOARD_")


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
    def port(self) -> int:
        return int(self.get("DASHBOARD_PORT") or DEFAULT_PORT)

    @property
    def network_names(self) -> tuple[str, ...]:
        """Addresses in the home network the dashboard is reachable under (DASHBOARD_HOSTS). Empty: this PC only."""
        return tuple(name.strip() for name in self.get("DASHBOARD_HOSTS").split(",") if name.strip())

    @property
    def listen_host(self) -> str:
        return "0.0.0.0" if self.network_names else HOST

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        """Host headers the server answers; anything else is refused, so no DNS rebinding."""
        return (*LOOPBACK_NAMES, *self.network_names)

    @property
    def password(self) -> str:
        return self.get("DASHBOARD_PASSWORD")

    @property
    def url(self) -> str:
        return f"http://{(self.network_names or LOOPBACK_NAMES)[0]}:{self.port}"

    @property
    def redirect_base(self) -> str:
        """The OAuth apps only send logins back to 127.0.0.1, also when the dashboard runs on another machine."""
        return f"http://{HOST}:{self.port}"


def load_settings(demo: bool = False) -> Settings:
    DATA_DIR.mkdir(exist_ok=True)
    name = "demo.db" if demo else "dashboard.db"
    env = read_env()
    content = env.get("DASHBOARD_SHORT_CONTENT", "").strip()  # short-content elsewhere, e.g. a network share
    return Settings(db_path=DATA_DIR / name, demo=demo, env=env, base=Path(content) if content else BASE)
