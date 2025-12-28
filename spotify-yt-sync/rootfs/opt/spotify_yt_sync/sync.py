#!/usr/bin/env python3
"""Spotify to YouTube Sync - Add-on Entry Point"""

import fcntl
import logging
import os
import sys
import time
from pathlib import Path

from clients.spotify import SpotifyClient, SpotifyAuthError, SpotifySchemaError
from clients.youtube import YouTubeClient, YouTubeAuthError, YouTubeQuotaExceededError
from core.cache import VideoCache
from core.sync_engine import SyncEngine
from core.models import SyncResult
from core.status import write_status, write_running_status

SPOTIFY_TOP_50_GLOBAL = "37i9dQZEVXbMDoHDwVN2tF"
DATA_DIR = Path("/config/spotify_yt_sync")
LOCK_FILE = DATA_DIR / ".sync.lock"
LOG_FILE = DATA_DIR / "spotify_yt_sync.log"

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler()
        ]
    )


def acquire_lock() -> int | None:
    try:
        # Check for stale lock (older than 30 min = likely orphaned)
        if LOCK_FILE.exists():
            age = time.time() - LOCK_FILE.stat().st_mtime
            if age > 1800:  # 30 minutes
                logger.warning(f"Removing stale lock file (age: {age:.0f}s)")
                LOCK_FILE.unlink(missing_ok=True)
        
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.write(fd, f"{os.getpid()}\n".encode())
        return fd
    except (OSError, IOError):
        return None


def release_lock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def load_config() -> dict:
    required = ["YOUTUBE_PLAYLIST_ID", "YOUTUBE_REFRESH_TOKEN"]
    config = {}
    missing = []
    
    for var in required:
        value = os.environ.get(var)
        if value:
            config[var] = value
        else:
            missing.append(var)
    
    if missing:
        logger.error(f"Missing config: {', '.join(missing)}")
        sys.exit(1)
    
    return config


def main() -> int:
    setup_logging()
    
    lock_fd = acquire_lock()
    if lock_fd is None:
        logger.warning("Another sync running, exiting")
        return 0
    
    try:
        write_running_status(DATA_DIR / "sync_status.json")
        config = load_config()
        
        logger.info("Initializing Spotify client...")
        try:
            spotify = SpotifyClient(DATA_DIR)
        except SpotifyAuthError as e:
            logger.error(f"Spotify auth failed: {e}")
            write_status(SyncResult.failure(f"Spotify auth failed: {e}"), DATA_DIR / "sync_status.json")
            return 1
        except SpotifySchemaError as e:
            logger.error(f"Spotify schema error: {e}")
            write_status(SyncResult.failure(f"Spotify schema error: {e}"), DATA_DIR / "sync_status.json")
            return 1
        
        logger.info("Initializing YouTube client...")
        try:
            youtube = YouTubeClient(config["YOUTUBE_REFRESH_TOKEN"])
        except YouTubeAuthError as e:
            logger.error(f"YouTube auth failed: {e}")
            write_status(SyncResult.failure(f"YouTube auth failed: {e}"), DATA_DIR / "sync_status.json")
            return 1
        
        cache = VideoCache(DATA_DIR / ".video_cache.json")
        engine = SyncEngine(spotify, youtube, cache)
        
        logger.info("Starting sync...")
        result = engine.sync(SPOTIFY_TOP_50_GLOBAL, config["YOUTUBE_PLAYLIST_ID"])
        
        write_status(result, DATA_DIR / "sync_status.json")
        
        if result.success:
            logger.info(f"Sync completed: +{result.tracks_added} -{result.tracks_removed}")
            return 0
        else:
            logger.warning(f"Sync errors: {result.errors}")
            return 1
            
    except YouTubeQuotaExceededError as e:
        logger.error(f"Quota exceeded: {e}")
        write_status(SyncResult.failure(f"Quota exceeded: {e}"), DATA_DIR / "sync_status.json")
        return 1
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        write_status(SyncResult.failure(f"Unexpected error: {e}"), DATA_DIR / "sync_status.json")
        return 1
    finally:
        release_lock(lock_fd)


if __name__ == "__main__":
    sys.exit(main())
