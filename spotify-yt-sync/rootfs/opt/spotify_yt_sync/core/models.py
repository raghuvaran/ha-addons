"""Data models for sync operations."""

from dataclasses import dataclass, field
from typing import List


class SyncAbortError(Exception):
    """Raised when sync must abort due to transient API error (e.g., 409)."""
    pass


@dataclass
class Track:
    """A track from Spotify."""
    name: str
    artist: str
    album: str
    spotify_id: str


@dataclass
class PlaylistItem:
    """An item from YouTube playlist."""
    item_id: str
    video_id: str
    title: str
    channel: str
    position: int = 0


@dataclass
class SyncResult:
    """Result of a sync operation."""
    success: bool
    tracks_added: int
    tracks_removed: int
    errors: List[str]
    spotify_count: int
    youtube_count: int
    duration: float
    
    @classmethod
    def failure(cls, error: str) -> "SyncResult":
        """Create a failure result with single error."""
        return cls(
            success=False,
            tracks_added=0,
            tracks_removed=0,
            errors=[error],
            spotify_count=0,
            youtube_count=0,
            duration=0.0
        )
