"""
YouTube Data API v3 Client

Handles OAuth authentication and playlist operations.
Includes retry logic for rate limiting and transient errors.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, TypeVar

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from core.models import PlaylistItem, SyncAbortError

logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent.parent
TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/youtube"]

T = TypeVar('T')


class YouTubeAuthError(Exception):
    """YouTube authentication failed."""
    pass


class YouTubeAPIError(Exception):
    """YouTube API operation failed."""
    pass


class YouTubeQuotaExceededError(Exception):
    """YouTube API quota exceeded."""
    pass


def _load_client_credentials() -> tuple[str, str]:
    """Load OAuth client credentials from env vars or client_secrets.json."""
    # Try environment variables first
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    
    if client_id and client_secret:
        return client_id, client_secret
    
    # Fall back to client_secrets.json
    secrets_file = SCRIPT_DIR / "client_secrets.json"
    if secrets_file.exists():
        try:
            secrets = json.loads(secrets_file.read_text())
            creds = secrets.get("installed") or secrets.get("web")
            if creds:
                return creds["client_id"], creds["client_secret"]
        except Exception as e:
            logger.warning(f"Failed to parse client_secrets.json: {e}")
    
    raise YouTubeAuthError(
        "OAuth credentials not found. Set GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET "
        "or provide client_secrets.json"
    )


class YouTubeClient:
    """YouTube Data API client with retry logic."""
    
    def __init__(self, refresh_token: str):
        try:
            client_id, client_secret = _load_client_credentials()
            
            credentials = Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri=TOKEN_URI,
                client_id=client_id,
                client_secret=client_secret,
                scopes=SCOPES
            )
            
            self._service = build("youtube", "v3", credentials=credentials)
            logger.info("YouTube client initialized")
            
        except YouTubeAuthError:
            raise
        except Exception as e:
            raise YouTubeAuthError(f"Failed to authenticate: {e}")
    
    def _retry(self, operation: Callable[[], T], name: str, max_retries: int = 3) -> T:
        """Execute operation with retry logic for transient errors."""
        for attempt in range(max_retries):
            try:
                return operation()
            except HttpError as e:
                status = e.resp.status if e.resp else 0
                error_str = str(e)
                
                # Quota exceeded - don't retry
                if status == 403 and "quotaExceeded" in error_str:
                    raise YouTubeQuotaExceededError(f"Quota exceeded: {e}")
                
                # Rate limit - wait and retry once
                if status == 403 and attempt == 0:
                    logger.warning(f"Rate limited on {name}, waiting 60s...")
                    time.sleep(60)
                    continue
                
                # 409 SERVICE_UNAVAILABLE - abort sync immediately
                # State is uncertain, continuing would corrupt playlist order
                if status == 409 and "SERVICE_UNAVAILABLE" in error_str:
                    logger.error(f"409 SERVICE_UNAVAILABLE on {name} - aborting sync")
                    raise SyncAbortError(f"409 error on {name}: {e}")
                
                # Server error - retry with backoff
                if status >= 500 and attempt < max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning(f"Server error on {name}, retrying in {wait}s...")
                    time.sleep(wait)
                    continue
                
                raise YouTubeAPIError(f"API error on {name}: {e}")
                
            except (ConnectionError, TimeoutError, OSError) as e:
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning(f"Network error on {name}, retrying in {wait}s...")
                    time.sleep(wait)
                    continue
                raise YouTubeAPIError(f"Network error on {name}: {e}")
        
        raise YouTubeAPIError(f"{name} failed after {max_retries} attempts")
    
    def search_video(self, track: str, artist: str) -> str | None:
        """
        Search for a video matching track and artist.
        
        Returns the best matching video ID, prioritizing:
        1. Official audio/video from the artist's channel
        2. Results containing both track name and artist in title
        3. Results from verified/official channels
        """
        query = f"{track} {artist} official audio"
        
        def do_search():
            return self._service.search().list(
                part="snippet",
                q=query,
                type="video",
                videoCategoryId="10",  # Music category
                maxResults=5  # Get multiple results to find best match
            ).execute()
        
        try:
            response = self._retry(do_search, f"search '{track} {artist}'")
            items = response.get("items", [])
            
            if not items:
                logger.warning(f"No results for: {track} by {artist}")
                return None
            
            # Score and rank results
            best_match = self._find_best_match(items, track, artist)
            
            if best_match:
                time.sleep(0.5)
                return best_match
            
            # Fallback: first result if no good match found
            logger.warning(f"No confident match for '{track}' by '{artist}', using first result")
            time.sleep(0.5)
            return items[0]["id"]["videoId"]
            
        except YouTubeQuotaExceededError:
            raise
        except Exception as e:
            logger.error(f"Search failed for '{track}': {e}")
            return None
    
    def _find_best_match(self, items: list, track: str, artist: str) -> str | None:
        """
        Find the best matching video from search results.
        
        Scoring:
        - +10: Title contains track name
        - +10: Title contains artist name  
        - +5: Channel name contains artist name (likely official)
        - +3: Title contains "official" 
        - +2: Title contains "audio" (prefer audio over video)
        - -5: Title contains "cover", "remix", "live" (unless in original)
        """
        track_lower = track.lower()
        artist_lower = artist.lower()
        
        # Check if original track/artist contains these words
        original_has_live = "live" in track_lower
        original_has_remix = "remix" in track_lower
        
        scored = []
        
        for item in items:
            snippet = item.get("snippet", {})
            title = snippet.get("title", "").lower()
            channel = snippet.get("channelTitle", "").lower()
            video_id = item["id"]["videoId"]
            
            score = 0
            
            # Must-have: track name in title
            if self._fuzzy_contains(title, track_lower):
                score += 10
            else:
                continue  # Skip if track name not in title
            
            # Artist match
            if self._fuzzy_contains(title, artist_lower):
                score += 10
            if self._fuzzy_contains(channel, artist_lower):
                score += 5  # Likely official channel
            
            # Quality indicators
            if "official" in title:
                score += 3
            if "audio" in title:
                score += 2
            if "vevo" in channel:
                score += 3  # Vevo = official
            
            # Penalties for covers/remixes (unless original has them)
            if "cover" in title:
                score -= 10  # Strong penalty for covers
            if "remix" in title and not original_has_remix:
                score -= 5
            if "live" in title and not original_has_live:
                score -= 3
            if "karaoke" in title or "instrumental" in title:
                score -= 10
            
            if score > 0:
                scored.append((score, video_id, title))
        
        if not scored:
            return None
        
        # Return highest scoring match
        scored.sort(reverse=True, key=lambda x: x[0])
        best_score, best_id, best_title = scored[0]
        
        logger.debug(f"Best match (score={best_score}): {best_title}")
        return best_id
    
    def _fuzzy_contains(self, haystack: str, needle: str) -> bool:
        """Check if needle is contained in haystack, with some fuzzy tolerance."""
        # Direct containment
        if needle in haystack:
            return True
        
        # Handle common variations (e.g., "The Weeknd" vs "Weeknd")
        needle_words = needle.split()
        if len(needle_words) > 1:
            # Check if most words match
            matches = sum(1 for word in needle_words if word in haystack)
            return matches >= len(needle_words) * 0.7
        
        return False
    
    def get_playlist_items(self, playlist_id: str) -> list[PlaylistItem] | None:
        """Get all items from a playlist."""
        items = []
        page_token = None
        
        try:
            while True:
                def do_list():
                    return self._service.playlistItems().list(
                        part="snippet,contentDetails",
                        playlistId=playlist_id,
                        maxResults=50,
                        pageToken=page_token
                    ).execute()
                
                response = self._retry(do_list, f"list playlist {playlist_id}")
                
                for item in response.get("items", []):
                    pi = self._extract_item(item)
                    if pi:
                        items.append(pi)
                
                page_token = response.get("nextPageToken")
                if not page_token:
                    break
            
            logger.info(f"Retrieved {len(items)} items from YouTube playlist")
            return items
            
        except YouTubeQuotaExceededError:
            raise
        except Exception as e:
            logger.error(f"Failed to fetch playlist: {e}")
            return None
    
    def _extract_item(self, item: dict) -> PlaylistItem | None:
        """Extract PlaylistItem from API response."""
        try:
            item_id = item.get("id", "")
            snippet = item.get("snippet", {})
            content = item.get("contentDetails", {})
            
            video_id = content.get("videoId", "")
            if not item_id or not video_id:
                return None
            
            return PlaylistItem(
                item_id=item_id,
                video_id=video_id,
                title=snippet.get("title", ""),
                channel=snippet.get("videoOwnerChannelTitle", ""),
                position=snippet.get("position", 0)
            )
        except Exception:
            return None
    
    def add_to_playlist(self, playlist_id: str, video_id: str, 
                        title: str = "", position: int | None = None) -> bool:
        """Add video to playlist. Returns True on success."""
        def do_insert():
            body = {
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id}
                }
            }
            if position is not None:
                body["snippet"]["position"] = position
            
            return self._service.playlistItems().insert(
                part="snippet", body=body
            ).execute()
        
        try:
            self._retry(do_insert, f"add {video_id}")
            logger.info(f"Added: {title or video_id}")
            time.sleep(0.5)
            return True
        except YouTubeQuotaExceededError:
            raise
        except Exception as e:
            logger.error(f"Failed to add {video_id}: {e}")
            return False
    
    def remove_from_playlist(self, item_id: str, title: str = "") -> bool:
        """Remove item from playlist. Returns True on success."""
        def do_delete():
            return self._service.playlistItems().delete(id=item_id).execute()
        
        try:
            self._retry(do_delete, f"remove {item_id}")
            logger.info(f"Removed: {title or item_id}")
            time.sleep(0.5)
            return True
        except YouTubeQuotaExceededError:
            raise
        except Exception as e:
            logger.error(f"Failed to remove {item_id}: {e}")
            return False
