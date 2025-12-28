# Spotify YouTube Sync

Automatically syncs Spotify's "Top 50 Global" playlist to your YouTube playlist.

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
6. Download the credentials

### 2. Get Refresh Token

Run the auth script locally (one-time setup):

```bash
pip install google-auth-oauthlib
python3 -c "
from google_auth_oauthlib.flow import InstalledAppFlow
flow = InstalledAppFlow.from_client_secrets_file(
    'client_secrets.json',
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
- **sync_schedule**: Cron expression (default: `0 2 * * *` = daily at 2 AM)
- **log_level**: debug, info, warning, or error

## How It Works

1. Fetches Spotify's Top 50 Global playlist (no auth needed - uses web player API)
2. Searches YouTube for matching videos
3. Updates your YouTube playlist to match (adds new, removes old)
4. Caches video IDs for 30 days to minimize API calls

## Quota Usage

YouTube API has a daily quota of 10,000 units:
- Search: 100 units per track (cached)
- List playlist: 1 unit
- Add/remove: 50 units each

Typical daily sync with 5-10 changes: ~500-1000 units
