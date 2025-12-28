"""Video ID Cache with 30-day TTL"""

import json
import logging
import os
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

TTL_DAYS = 30
TTL_SECONDS = TTL_DAYS * 24 * 60 * 60


class VideoCache:
    def __init__(self, cache_file: Path = Path("/config/spotify_yt_sync/.video_cache.json")):
        self._file = cache_file
        self._cache: dict[str, dict] = {}
        self._dirty = False
        self._load()
        self._prune_expired()
    
    def _load(self) -> None:
        if not self._file.exists():
            return
        
        try:
            data = json.loads(self._file.read_text())
            for key, value in data.items():
                if isinstance(value, str):
                    self._cache[key] = {"video_id": value, "cached_at": time.time()}
                    self._dirty = True
                else:
                    self._cache[key] = value
            logger.debug(f"Loaded {len(self._cache)} cached mappings")
        except Exception as e:
            logger.warning(f"Cache load failed: {e}")
            self._cache = {}
    
    def _prune_expired(self) -> None:
        now = time.time()
        expired = [k for k, v in self._cache.items() if now - v.get("cached_at", 0) > TTL_SECONDS]
        if expired:
            for key in expired:
                del self._cache[key]
            self._dirty = True
            logger.info(f"Pruned {len(expired)} expired cache entries")
    
    def save(self) -> None:
        if not self._dirty:
            return
        
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_path = tempfile.mkstemp(dir=self._file.parent, prefix=".cache_", suffix=".tmp")
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(self._cache, f, indent=2)
                os.replace(temp_path, self._file)
                self._dirty = False
            except Exception:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                raise
        except Exception as e:
            logger.error(f"Cache save failed: {e}")
    
    def _make_key(self, track: str, artist: str) -> str:
        return f"{track.lower().strip()}\x00{artist.lower().strip()}"
    
    def get(self, track: str, artist: str) -> str | None:
        key = self._make_key(track, artist)
        entry = self._cache.get(key)
        if not entry:
            return None
        if time.time() - entry.get("cached_at", 0) > TTL_SECONDS:
            del self._cache[key]
            self._dirty = True
            return None
        return entry.get("video_id")
    
    def set(self, track: str, artist: str, video_id: str) -> None:
        key = self._make_key(track, artist)
        current = self._cache.get(key, {}).get("video_id")
        if current != video_id:
            self._cache[key] = {"video_id": video_id, "cached_at": time.time()}
            self._dirty = True
    
    def __len__(self) -> int:
        return len(self._cache)
