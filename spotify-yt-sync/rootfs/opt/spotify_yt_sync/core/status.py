"""Status file writer for Home Assistant integration"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from core.models import SyncResult


def write_status(result: SyncResult, status_file: Path) -> bool:
    data = {
        "status": "success" if result.success else "failed",
        "last_sync_time": datetime.now(timezone.utc).isoformat(),
        "tracks_added": result.tracks_added,
        "tracks_removed": result.tracks_removed,
        "last_error": result.errors[-1] if result.errors else None,
        "spotify_track_count": result.spotify_count,
        "youtube_track_count": result.youtube_count,
    }
    return _atomic_write(status_file, data)


def write_running_status(status_file: Path) -> bool:
    data = {
        "status": "running",
        "last_sync_time": datetime.now(timezone.utc).isoformat(),
        "tracks_added": 0,
        "tracks_removed": 0,
        "last_error": None,
        "spotify_track_count": 0,
        "youtube_track_count": 0,
    }
    return _atomic_write(status_file, data)


def _atomic_write(path: Path, data: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(dir=path.parent, prefix=".status_", suffix=".tmp")
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            os.replace(temp_path, path)
            return True
        except Exception:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise
    except Exception:
        return False
