"""The records every platform connector returns, so sync and the UI never see platform JSON."""

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

Progress = Callable[[float, str], None]
Credentials = dict  # opaque per platform; stored as JSON on the account


class PlatformError(Exception):
    """A failure worth showing to the user as is (German message)."""


class AuthExpired(PlatformError):
    """The stored login no longer works - the account has to be connected again."""


@dataclass(frozen=True)
class AccountInfo:
    platform: str
    external_id: str
    name: str
    handle: str = ""
    avatar_url: str = ""
    followers: int | None = None
    total_views: int | None = None
    video_count: int | None = None


@dataclass(frozen=True)
class VideoSnapshot:
    external_id: str
    title: str
    url: str
    published_at: str  # ISO 8601, UTC
    thumbnail_url: str = ""
    duration_s: float | None = None
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    saves: int | None = None
    reach: int | None = None
    watch_time_min: float | None = None
    avg_view_s: float | None = None
    avg_view_pct: float | None = None
    subs_gained: int | None = None


@dataclass(frozen=True)
class DailyPoint:
    """Per-day channel numbers the platform reports itself (YouTube Analytics)."""

    day: str
    views: int | None = None
    watch_time_min: float | None = None
    subs_gained: int | None = None
    subs_lost: int | None = None


@dataclass(frozen=True)
class SyncResult:
    account: AccountInfo
    credentials: Credentials
    videos: tuple[VideoSnapshot, ...] = ()
    daily: tuple[DailyPoint, ...] = ()
    retention: dict[str, list[list[float]]] = field(default_factory=dict)  # video id -> [[ratio, watch], ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class UploadRequest:
    path: Path
    title: str
    caption: str
    privacy: str = "private"  # YouTube only: private | unlisted | public
    publish_at: str | None = None  # YouTube only: ISO time, needs privacy=private


@dataclass(frozen=True)
class UploadResult:
    external_id: str | None
    url: str | None
    message: str


class Connector(Protocol):
    platform: str
    label: str
    connect_mode: str  # "oauth" (redirect login) | "token" (paste an access token)

    def missing_config(self) -> list[str]: ...
    def fresh(self, creds: Credentials) -> Credentials: ...
    def sync(self, creds: Credentials) -> SyncResult: ...
    def upload(self, creds: Credentials, request: UploadRequest, progress: Progress) -> UploadResult: ...


def new_verifier() -> str:
    """PKCE code verifier: 64 characters from the unreserved set."""
    return secrets.token_urlsafe(48)[:64]


def challenge_b64(verifier: str) -> str:
    """RFC 7636 S256 challenge (Google)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def challenge_hex(verifier: str) -> str:
    """TikTok's desktop flow wants the SHA-256 of the verifier hex-encoded instead."""
    return hashlib.sha256(verifier.encode("ascii")).hexdigest()


def expires_at(seconds: float | int | None) -> float:
    return time.time() + float(seconds or 3600)


def is_expiring(creds: Credentials, margin_s: float = 120) -> bool:
    return float(creds.get("expires_at", 0)) - margin_s < time.time()


def as_int(value) -> int | None:
    try:
        return None if value is None or value == "" else int(float(value))
    except (TypeError, ValueError):
        return None


def as_float(value) -> float | None:
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None


def read_chunks(path: Path, sizes: list[int]):
    """Yield (offset, bytes) for consecutive chunks of the given sizes."""
    offset = 0
    with path.open("rb") as fh:
        for size in sizes:
            data = fh.read(size)
            yield offset, data
            offset += len(data)
