"""
Sync Engine

Orchestrates synchronization between Spotify and YouTube playlists.
Uses LIS (Longest Increasing Subsequence) algorithm to minimize
playlist modifications and conserve YouTube API quota.

Algorithm: Insert-First Strategy
--------------------------------
Key insight: YouTube deletes use item_id (stable), inserts use position (dynamic).

1. Find LIS of items already in correct relative order (these stay)
2. Execute INSERTS first in ascending position order
   - Each insert shifts existing items right automatically
   - YouTube handles position management
3. Execute DELETES last using item_id (position-independent)
   - item_id doesn't change when other items move
4. Abort immediately on 409 errors (state becomes uncertain)

Quota costs:
- search.list: 100 units (cached to avoid)
- playlistItems.list: 1 unit
- playlistItems.insert: 50 units
- playlistItems.delete: 50 units
"""

import logging
import time
from dataclasses import dataclass
from typing import Protocol

from core.models import Track, PlaylistItem, SyncResult, SyncAbortError
from core.cache import VideoCache

logger = logging.getLogger(__name__)


class SpotifyClientProtocol(Protocol):
    def get_playlist_tracks(self, playlist_id: str) -> list[Track] | None: ...


class YouTubeClientProtocol(Protocol):
    def get_playlist_items(self, playlist_id: str) -> list[PlaylistItem] | None: ...
    def search_video(self, track: str, artist: str) -> str | None: ...
    def add_to_playlist(self, playlist_id: str, video_id: str, title: str, position: int | None) -> bool: ...
    def remove_from_playlist(self, item_id: str, title: str) -> bool: ...


@dataclass
class SyncOp:
    """A single sync operation."""
    action: str  # "insert" or "delete"
    position: int
    video_id: str
    item_id: str = ""
    title: str = ""


def _normalize(s: str) -> str:
    """Normalize string for comparison."""
    return " ".join(s.lower().split())


def _track_matches_video(track: Track, title: str) -> bool:
    """Check if YouTube video title matches Spotify track."""
    norm_title = _normalize(title)
    norm_track = _normalize(track.name)
    norm_artist = _normalize(track.artist)
    return norm_track in norm_title and norm_artist in norm_title


def _find_lis_indices(current: list[str], target: list[str]) -> set[int]:
    """
    Find indices in current list that form LIS matching target order.
    Returns set of indices in current that should be kept in place.
    """
    if not current or not target:
        return set()
    
    target_pos = {vid: i for i, vid in enumerate(target)}
    # (index in current, position in target)
    items = [(i, target_pos[vid]) for i, vid in enumerate(current) if vid in target_pos]
    
    if not items:
        return set()
    
    n = len(items)
    dp = [1] * n
    parent = [-1] * n
    
    for i in range(1, n):
        for j in range(i):
            if items[j][1] < items[i][1] and dp[j] + 1 > dp[i]:
                dp[i] = dp[j] + 1
                parent[i] = j
    
    # Reconstruct LIS - get indices in current list
    max_idx = dp.index(max(dp))
    result = set()
    idx = max_idx
    while idx != -1:
        result.add(items[idx][0])  # Add index in current list
        idx = parent[idx]
    
    return result


class SyncEngine:
    """Orchestrates playlist synchronization using insert-first strategy."""
    
    def __init__(self, spotify: SpotifyClientProtocol, 
                 youtube: YouTubeClientProtocol, cache: VideoCache):
        self._spotify = spotify
        self._youtube = youtube
        self._cache = cache
    
    def _resolve_video_id(self, track: Track, yt_items: list[PlaylistItem]) -> str | None:
        """Find YouTube video ID for track, using cache and existing playlist."""
        # First check if already in YouTube playlist
        for item in yt_items:
            if _track_matches_video(track, item.title):
                self._cache.set(track.name, track.artist, item.video_id)
                return item.video_id
        
        # Check cache
        cached = self._cache.get(track.name, track.artist)
        if cached:
            logger.debug(f"Cache hit: {track.name}")
            return cached
        
        # Search YouTube (expensive: 100 quota units)
        logger.debug(f"Searching: {track.name} by {track.artist}")
        video_id = self._youtube.search_video(track.name, track.artist)
        if video_id:
            self._cache.set(track.name, track.artist, video_id)
        return video_id
    
    def _build_target_list(self, spotify_tracks: list[Track], 
                           yt_items: list[PlaylistItem]) -> tuple[list[tuple[Track, str]], list[str]]:
        """Build target video list from Spotify tracks."""
        target = []
        errors = []
        
        for track in spotify_tracks:
            video_id = self._resolve_video_id(track, yt_items)
            if video_id:
                target.append((track, video_id))
            else:
                errors.append(f"No match: {track.name} by {track.artist}")
        
        return target, errors
    
    def _compute_operations(self, target: list[tuple[Track, str]], 
                            yt_items: list[PlaylistItem]) -> tuple[list[SyncOp], list[SyncOp]]:
        """
        Compute sync operations using LIS algorithm.
        Returns (inserts, deletes) as separate lists.
        
        Strategy:
        1. Find LIS - items already in correct relative order (keep these)
        2. Delete items not in target + items in target but out of order
        3. Insert items not in current + items that were deleted for reordering
        """
        current_ids = [item.video_id for item in yt_items]
        target_ids = [vid for _, vid in target]
        target_set = set(target_ids)
        
        # Map video_id to item for deletion lookups
        vid_to_item = {item.video_id: item for item in yt_items}
        
        # Find indices in current that form LIS (items to keep in place)
        lis_indices = _find_lis_indices(current_ids, target_ids)
        lis_video_ids = {current_ids[i] for i in lis_indices}
        
        logger.debug(f"LIS size: {len(lis_indices)} of {len(current_ids)} current items")
        
        deletes = []
        inserts = []
        
        # Delete: items not in target OR in target but not in LIS (out of order)
        for i, item in enumerate(yt_items):
            if item.video_id not in target_set:
                # Not in target at all - delete
                deletes.append(SyncOp("delete", item.position, item.video_id,
                                      item.item_id, item.title))
            elif i not in lis_indices:
                # In target but out of order - delete (will re-insert)
                deletes.append(SyncOp("delete", item.position, item.video_id,
                                      item.item_id, item.title))
        
        # Insert: items not in current OR items that were deleted for reordering
        for pos, (track, video_id) in enumerate(target):
            if video_id not in lis_video_ids:
                # Either new item or was deleted for reordering
                inserts.append(SyncOp("insert", pos, video_id,
                                      title=f"{track.name} by {track.artist}"))
        
        # Sort inserts by position (ascending) - critical for correct ordering
        inserts.sort(key=lambda x: x.position)
        
        return inserts, deletes
    
    def _execute_operations(self, inserts: list[SyncOp], deletes: list[SyncOp],
                            playlist_id: str) -> tuple[int, int, list[str]]:
        """
        Execute sync operations: INSERTS FIRST, then DELETES.
        Aborts on 409 errors to prevent state corruption.
        Returns (added, removed, errors).
        """
        added = removed = 0
        errors = []
        
        logger.info(f"Executing {len(inserts)} inserts, {len(deletes)} deletes")
        
        # Execute INSERTS FIRST in position order
        # Each insert at position N shifts items at N+ to the right
        for op in inserts:
            try:
                if self._youtube.add_to_playlist(playlist_id, op.video_id, 
                                                  op.title, op.position):
                    added += 1
                else:
                    errors.append(f"Failed to add: {op.title}")
                    # Check if this was a 409 abort situation
                    # The youtube client will raise SyncAbortError on 409
            except SyncAbortError:
                errors.append(f"ABORT: 409 error on add {op.title}")
                logger.error("Sync aborted due to 409 error during insert")
                return added, removed, errors
            except Exception as e:
                error_str = str(e).lower()
                if "409" in error_str or "service_unavailable" in error_str:
                    errors.append(f"ABORT: {op.title} - {e}")
                    logger.error("Sync aborted due to transient error during insert")
                    return added, removed, errors
                errors.append(f"Error adding {op.title}: {e}")
                if "quota" in error_str:
                    return added, removed, errors
        
        # Execute DELETES using item_id (position-independent)
        for op in deletes:
            try:
                if self._youtube.remove_from_playlist(op.item_id, op.title):
                    removed += 1
                else:
                    errors.append(f"Failed to remove: {op.title}")
            except SyncAbortError:
                errors.append(f"ABORT: 409 error on remove {op.title}")
                logger.error("Sync aborted due to 409 error during delete")
                return added, removed, errors
            except Exception as e:
                error_str = str(e).lower()
                if "409" in error_str or "service_unavailable" in error_str:
                    errors.append(f"ABORT: {op.title} - {e}")
                    logger.error("Sync aborted due to transient error during delete")
                    return added, removed, errors
                errors.append(f"Error removing {op.title}: {e}")
                if "quota" in error_str:
                    return added, removed, errors
        
        return added, removed, errors
    
    def sync(self, spotify_playlist_id: str, youtube_playlist_id: str) -> SyncResult:
        """Perform full sync. Returns SyncResult."""
        start = time.time()
        
        logger.info("=" * 50)
        logger.info("Starting sync")
        logger.info(f"Cache entries: {len(self._cache)}")
        
        # Fetch Spotify tracks
        spotify_tracks = self._spotify.get_playlist_tracks(spotify_playlist_id)
        if spotify_tracks is None:
            return SyncResult.failure("Failed to fetch Spotify playlist")
        
        spotify_count = len(spotify_tracks)
        logger.info(f"Spotify: {spotify_count} tracks")
        
        # Fetch YouTube items
        yt_items = self._youtube.get_playlist_items(youtube_playlist_id)
        if yt_items is None:
            return SyncResult.failure("Failed to fetch YouTube playlist")
        
        yt_count = len(yt_items)
        logger.info(f"YouTube: {yt_count} items")
        
        # Build target and compute operations
        target, resolve_errors = self._build_target_list(spotify_tracks, yt_items)
        inserts, deletes = self._compute_operations(target, yt_items)
        
        if not inserts and not deletes:
            logger.info("Playlists already in sync!")
            self._cache.save()
            return SyncResult(
                success=True, tracks_added=0, tracks_removed=0,
                errors=resolve_errors, spotify_count=spotify_count,
                youtube_count=yt_count, duration=time.time() - start
            )
        
        # Execute: inserts first, then deletes
        added, removed, exec_errors = self._execute_operations(inserts, deletes, 
                                                                youtube_playlist_id)
        self._cache.save()
        
        all_errors = resolve_errors + exec_errors
        duration = time.time() - start
        final_yt = yt_count + added - removed
        
        # Check if we aborted
        aborted = any("ABORT" in e for e in exec_errors)
        
        logger.info(f"Completed in {duration:.1f}s: +{added} -{removed}" + 
                   (" (ABORTED)" if aborted else ""))
        logger.info("=" * 50)
        
        return SyncResult(
            success=len(exec_errors) == 0,
            tracks_added=added,
            tracks_removed=removed,
            errors=all_errors,
            spotify_count=spotify_count,
            youtube_count=final_yt,
            duration=duration
        )
