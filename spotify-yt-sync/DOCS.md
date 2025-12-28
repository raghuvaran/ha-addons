# Spotify YouTube Sync

Automatically syncs Spotify's "Top 50 Global" playlist to your YouTube playlist.

## How It Works

This is a **one-shot add-on** - it runs the sync once then stops. Use Home Assistant automations to schedule it.

## Prerequisites

1. A YouTube playlist (create one at youtube.com)
2. Google Cloud project with YouTube Data API v3 enabled
3. OAuth 2.0 credentials (Client ID and Secret)

## Setup

### 1. Create Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project
3. Enable "YouTube Data API v3"
4. Go to "Credentials" → "Create Credentials" → "OAuth 2.0 Client ID"
5. Choose "Desktop app" as application type
6. Note the Client ID and Client Secret

### 2. Get Refresh Token

Run this locally (one-time setup):

```bash
pip install google-auth-oauthlib
python3 -c "
from google_auth_oauthlib.flow import InstalledAppFlow
flow = InstalledAppFlow.from_client_config(
    {'installed': {
        'client_id': 'YOUR_CLIENT_ID',
        'client_secret': 'YOUR_CLIENT_SECRET',
        'redirect_uris': ['http://localhost'],
        'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
        'token_uri': 'https://oauth2.googleapis.com/token'
    }},
    ['https://www.googleapis.com/auth/youtube']
)
creds = flow.run_local_server(port=8080)
print(f'Refresh Token: {creds.refresh_token}')
"
```

### 3. Configure Add-on

- **youtube_playlist_id**: Your YouTube playlist ID (from URL: `youtube.com/playlist?list=PLxxxxxxxxxx`)
- **youtube_refresh_token**: The refresh token from step 2
- **google_client_id**: From your OAuth credentials
- **google_client_secret**: From your OAuth credentials
- **log_level**: debug, info, warning, or error

## Usage

### Manual Run
Click "Start" in the add-on page. It will sync and then stop automatically.

### Scheduled (Recommended)
Create an automation to run daily:

```yaml
automation:
  - alias: "Daily Spotify Sync"
    trigger:
      - platform: time
        at: "02:00:00"
    action:
      - service: hassio.addon_start
        data:
          addon: d0d184f6_spotify_yt_sync
```

## Quota Usage

YouTube API has a daily quota of 10,000 units:
- Search: 100 units per track (cached 30 days)
- List playlist: 1 unit
- Add/remove: 50 units each

Typical daily sync with 5-10 changes: ~500-1000 units
