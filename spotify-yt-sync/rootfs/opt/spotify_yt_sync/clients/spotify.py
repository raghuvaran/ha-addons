"""Spotify Web Player API Client - Uses Playwright for token extraction"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import requests

from core.models import Track

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://api-partner.spotify.com/pathfinder/v2/query"
PLAYLIST_QUERY_HASH = "bb67e0af06e8d6f52b531f97468ee4acd44cd0f82b988e15c2ea47b1148efc77"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class SpotifyAuthError(Exception):
    pass


class SpotifySchemaError(Exception):
    pass


class SpotifyClient:
    def __init__(self, data_dir: Path = Path("/config/spotify_yt_sync")):
        self._token: str | None = None
        self._token_expires: int = 0
        self._session = requests.Session()
        self._token_cache = data_dir / ".spotify_token.json"
        
        if not self._load_cached_token():
            self._refresh_token()
        
        logger.info("Spotify client initialized")
    
    def _load_cached_token(self) -> bool:
        try:
            if self._token_cache.exists():
                data = json.loads(self._token_cache.read_text())
                expires = data.get("expires_at", 0)
                if time.time() * 1000 < (expires - 300000):
                    self._token = data["access_token"]
                    self._token_expires = expires
                    logger.debug("Loaded cached Spotify token")
                    return True
        except Exception as e:
            logger.debug(f"Cache load failed: {e}")
        return False
    
    def _save_token(self) -> None:
        try:
            self._token_cache.parent.mkdir(parents=True, exist_ok=True)
            self._token_cache.write_text(json.dumps({
                "access_token": self._token,
                "expires_at": self._token_expires
            }))
        except Exception as e:
            logger.warning(f"Failed to cache token: {e}")
    
    def _refresh_token(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise SpotifyAuthError("Playwright not installed")
        
        logger.info("Fetching Spotify token via browser...")
        chromium_path = os.environ.get("CHROMIUM_PATH", "/usr/bin/chromium-browser")
        
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    executable_path=chromium_path,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"]
                )
                
                context = browser.new_context(
                    user_agent=USER_AGENT,
                    viewport={"width": 1920, "height": 1080},
                )
                
                context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                    window.chrome = { runtime: {} };
                """)
                
                page = context.new_page()
                token_data = {}
                
                def capture_token(response):
                    if "/api/token" in response.url or "/get_access_token" in response.url:
                        try:
                            data = response.json()
                            if "accessToken" in data:
                                token_data["token"] = data["accessToken"]
                                token_data["expires"] = data.get(
                                    "accessTokenExpirationTimestampMs",
                                    int(time.time() * 1000) + 3600000
                                )
                        except Exception:
                            pass
                
                page.on("response", capture_token)
                page.goto(
                    "https://open.spotify.com/playlist/37i9dQZEVXbMDoHDwVN2tF",
                    wait_until="networkidle",
                    timeout=60000
                )
                page.wait_for_timeout(3000)
                
                context.close()
                browser.close()
                
                if not token_data.get("token"):
                    raise SpotifyAuthError("Failed to capture access token")
                
                self._token = token_data["token"]
                self._token_expires = token_data["expires"]
                self._save_token()
                logger.info("Spotify token obtained")
                
        except SpotifyAuthError:
            raise
        except Exception as e:
            raise SpotifyAuthError(f"Browser auth failed: {e}")
    
    def _ensure_token(self) -> None:
        if time.time() * 1000 >= (self._token_expires - 300000):
            self._refresh_token()
    
    def _graphql_request(self, payload: dict) -> dict:
        self._ensure_token()
        
        response = self._session.post(
            GRAPHQL_URL,
            headers={
                "authorization": f"Bearer {self._token}",
                "content-type": "application/json;charset=UTF-8",
                "app-platform": "WebPlayer",
                "user-agent": USER_AGENT,
            },
            json=payload
        )
        
        if response.status_code != 200:
            logger.error(f"GraphQL error {response.status_code}: {response.text[:200]}")
            response.raise_for_status()
        
        return response.json()
    
    def _validate_response(self, data: dict) -> None:
        try:
            playlist = data.get("data", {}).get("playlistV2", {})
            content = playlist.get("content", {})
            
            if "items" not in content:
                raise SpotifySchemaError("Response missing 'items'")
            if "totalCount" not in content:
                raise SpotifySchemaError("Response missing 'totalCount'")
        except SpotifySchemaError:
            raise
        except Exception as e:
            raise SpotifySchemaError(f"Schema validation failed: {e}")
    
    def get_playlist_tracks(self, playlist_id: str) -> list[Track] | None:
        tracks = []
        offset = 0
        limit = 50
        
        try:
            while True:
                payload = {
                    "variables": {
                        "uri": f"spotify:playlist:{playlist_id}",
                        "offset": offset,
                        "limit": limit,
                        "enableWatchFeedEntrypoint": False,
                    },
                    "operationName": "fetchPlaylist",
                    "extensions": {
                        "persistedQuery": {
                            "version": 1,
                            "sha256Hash": PLAYLIST_QUERY_HASH
                        }
                    }
                }
                
                result = self._graphql_request(payload)
                self._validate_response(result)
                
                content = result["data"]["playlistV2"]["content"]
                items = content.get("items", [])
                
                if not items:
                    break
                
                for item in items:
                    track = self._extract_track(item)
                    if track:
                        tracks.append(track)
                
                total = content.get("totalCount", 0)
                if offset + limit >= total:
                    break
                
                offset += limit
                time.sleep(0.1)
            
            logger.info(f"Retrieved {len(tracks)} tracks from Spotify")
            return tracks
            
        except (SpotifyAuthError, SpotifySchemaError):
            raise
        except Exception as e:
            logger.error(f"Failed to fetch playlist: {e}")
            return None
    
    def _extract_track(self, item: dict) -> Track | None:
        try:
            track_data = item.get("itemV2", {}).get("data", {})
            
            if track_data.get("__typename") != "Track":
                return None
            
            name = track_data.get("name", "")
            uri = track_data.get("uri", "")
            spotify_id = uri.split(":")[-1] if uri else ""
            
            artists = track_data.get("artists", {}).get("items", [])
            artist = artists[0].get("profile", {}).get("name", "") if artists else ""
            
            album = track_data.get("albumOfTrack", {}).get("name", "")
            
            if not name or not spotify_id:
                return None
            
            return Track(name=name, artist=artist, album=album, spotify_id=spotify_id)
            
        except Exception as e:
            logger.debug(f"Track extraction failed: {e}")
            return None
