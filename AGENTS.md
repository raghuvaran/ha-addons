# AGENTS.md

Guidelines for AI agents working on this repository.

## Repository Overview

Home Assistant add-on repository containing the **Spotify YouTube Sync** add-on. This add-on syncs Spotify's "Top 50 Global" playlist to a user's YouTube playlist.

## Architecture

```
ha-addons/
├── repository.yaml              # HA add-on repo metadata
├── README.md                    # User-facing repo docs
└── spotify-yt-sync/             # The add-on
    ├── config.yaml              # Add-on config (version, options, schema)
    ├── build.yaml               # Base image per architecture
    ├── Dockerfile               # Debian bookworm + Chromium + Python
    ├── DOCS.md                  # Setup instructions shown in HA UI
    ├── requirements.txt         # Python dependencies
    └── rootfs/
        ├── etc/services.d/sync/run    # S6 service script (cron scheduler)
        └── opt/spotify_yt_sync/       # Python application
            ├── sync.py                # Entry point
            ├── clients/
            │   ├── spotify.py         # Playwright-based token extraction
            │   └── youtube.py         # YouTube Data API v3 client
            └── core/
                ├── models.py          # Data classes
                ├── cache.py           # Video ID cache (30-day TTL)
                ├── status.py          # Status file for HA integration
                └── sync_engine.py     # LIS algorithm for minimal changes
```

## Key Technical Decisions

1. **Debian base image** - Required for Playwright (no musl/Alpine wheels)
2. **System Chromium** - Uses `/usr/bin/chromium` instead of Playwright's bundled browser
3. **Playwright for Spotify** - Editorial playlists (Top 50) blocked from official API
4. **LIS algorithm** - Minimizes YouTube API quota by computing minimal diff
5. **30-day cache TTL** - Balances quota savings vs stale video detection

## Development Guidelines

### Version Bumping
Always bump version in `spotify-yt-sync/config.yaml` when making changes - HA caches based on version.

### Testing Locally
```bash
# Build the Docker image
docker build -t spotify-yt-sync --build-arg BUILD_FROM=ghcr.io/home-assistant/aarch64-base-debian:bookworm spotify-yt-sync/

# Run with test config
docker run -it --rm \
  -e YOUTUBE_PLAYLIST_ID=PLxxxxx \
  -e YOUTUBE_REFRESH_TOKEN=1//xxxxx \
  -e GOOGLE_CLIENT_ID=xxxxx \
  -e GOOGLE_CLIENT_SECRET=xxxxx \
  spotify-yt-sync python3 /opt/spotify_yt_sync/sync.py
```

### Common Issues

| Issue | Cause | Fix |
|-------|-------|-----|
| Playwright install fails | Alpine base | Use Debian base (build.yaml) |
| Chromium not found | Wrong path | Set `CHROMIUM_PATH=/usr/bin/chromium` |
| Token capture fails | Spotify changed API | Update `PLAYLIST_QUERY_HASH` in spotify.py |
| Quota exceeded | Too many searches | Check cache is persisting to `/config/spotify_yt_sync/` |

### Environment Variables

| Variable | Source | Description |
|----------|--------|-------------|
| `YOUTUBE_PLAYLIST_ID` | config.yaml options | Target YouTube playlist |
| `YOUTUBE_REFRESH_TOKEN` | config.yaml options | OAuth refresh token |
| `GOOGLE_CLIENT_ID` | config.yaml options | OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | config.yaml options | OAuth client secret |
| `LOG_LEVEL` | config.yaml options | debug/info/warning/error |
| `CHROMIUM_PATH` | Dockerfile ENV | System Chromium binary path |

### Data Persistence

All persistent data stored in `/config/spotify_yt_sync/`:
- `.spotify_token.json` - Cached Spotify access token
- `.video_cache.json` - Track → video ID mappings
- `sync_status.json` - Last sync result for HA sensor
- `spotify_yt_sync.log` - Application logs

## API Quota Awareness

YouTube API has 10,000 units/day limit:
- `search.list`: 100 units (cached 30 days)
- `playlistItems.list`: 1 unit
- `playlistItems.insert/delete`: 50 units each

Typical sync with 5-10 changes: ~500-1000 units
