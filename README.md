# YTBot - Multi-Platform Content Download & Management Bot

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Code Style](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

YTBot downloads and manages content from YouTube and Twitter/X through a Telegram bot, and can also run as a one-shot command-line tool. It ships with Nextcloud upload, local fallback storage with an automatic retry queue, health/connection monitoring, and an extensible per-platform architecture.

## Features

### Content platforms

- **YouTube**
  - Single videos and **playlists** — playlists are expanded into individual videos and processed one by one (download one, upload one), with a running `i/N` progress indicator
  - **Audio or video** download, with smart format selection: audio prefers Opus 160kbps (251) → M4A 128kbps (140) → highest bitrate; video prefers 1080p (137) and combines the best video + audio streams
  - Playlist-wide format is chosen once from the first video and inherited by all entries; entries missing that format automatically fall back to the default
  - Large-file (>500 MB) confirmation in bot mode; real-time download progress
  - Cookie file support for restricted content
- **Twitter/X**
  - Playwright/headless-Chromium scraping that bypasses anti-bot protection
  - Automatic "Show more" expansion for long tweets; analytics/ads/recommendations filtered out
  - Markdown formatting preserved (bold, links, code blocks, italic); images and media downloaded
  - Long articles detected and exported (Markdown / PDF)

### Storage

- **Nextcloud first**: direct WebDAV upload into a configurable directory, with chunked transfer and retries
- **Local fallback**: if Nextcloud is unavailable, files are saved locally and added to a persistent **cache queue**, then automatically re-uploaded by a background task
- Configurable local storage path, size quota, and old-file cleanup
- Uploaded local temp files are cleaned up after a successful upload

### Telegram bot

- Async long-polling bot with audio/video choice flow, progress edits, and status commands
- **Flood-control aware**: outbound messages are paused during Telegram flood windows with discardable progress edits so retries do not extend the block
- Single-instance **process lock**, graceful shutdown on SIGINT/SIGTERM, and startup notification to the admin
- Multi-step per-user interaction state with automatic timeout cleanup

### One-shot CLI mode

- `ytbot --download <url> --type video|audio` downloads and exits — **no Telegram initialization, no polling, no Telegram messages**, and no process lock (can run alongside the bot, e.g. during flood-control pauses)
- Same download and storage semantics as the bot, including playlist expansion, shared format inheritance, format fallback, and the Nextcloud → cache-queue flow
- Throttled terminal progress and a final success/failure summary; exit code `0` on full success, `1` otherwise

### Operations

- Phased startup with dependency checks (ffmpeg) and yt-dlp version checking
- Health monitoring (CPU/memory/disk) and connection monitoring with automatic reconnect/polling recovery
- Structured rotating logs, `--log-level` override, and cache inspection/retry commands
- Fully async architecture; unit-tested service layer

## Installation

### Prerequisites

- **Python 3.8+**
- **ffmpeg** (required for audio extraction and video/audio merging)
  - macOS: `brew install ffmpeg`
  - Debian/Ubuntu: `sudo apt install ffmpeg`
  - Windows: `winget install ffmpeg` or download from https://ffmpeg.org/download.html
- A Chromium browser for Twitter/X scraping and PDF export (installed via Playwright below; Google Chrome is also auto-detected)

### Install

```bash
# Clone and enter the repository
git clone https://github.com/yourusername/ytbot.git
cd ytbot

# Create and activate a virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# Install the package and dependencies (registers the `ytbot` command)
pip install -r requirements.txt
pip install -e .

# Required only for Twitter/X support and PDF export
playwright install chromium
```

Verify the installation:

```bash
ytbot --version
ytbot --status          # checks configuration and whether another instance is running
```

### Configuration

All configuration is read from a `.env` file in the project root. Create it from the template and fill in your values:

```bash
cp .env.example .env
```

Key settings:

| Variable | Required for | Purpose |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Bot mode | Token from @BotFather |
| `ADMIN_CHAT_ID` | Bot mode | Admin chat id (get it from @userinfobot) |
| `NEXTCLOUD_URL` / `NEXTCLOUD_USERNAME` / `NEXTCLOUD_PASSWORD` | Nextcloud upload | Leave unset to use local storage only |
| `NEXTCLOUD_UPLOAD_DIR` | Nextcloud upload | Remote directory (default `/YTBot`) |
| `LOCAL_STORAGE_PATH` | — | Local/cache directory (default `./downloads`) |
| `LOG_LEVEL` | — | `DEBUG`/`INFO`/`WARNING`/`ERROR` |
| `AUDIO_CODEC` / `AUDIO_QUALITY` | — | Extracted audio settings (default mp3 / 192) |

See [.env.example](.env.example) for the full list (retries, timeouts, rate-limit sleep intervals, monitoring thresholds).

> **Note:** Telegram settings are only required when running the bot. One-shot downloads work without them as long as local storage or Nextcloud is configured.

## Usage

### Run the bot

```bash
ytbot                                # start long-polling bot
ytbot --config /path/to/.env         # use an alternative config file
ytbot --log-level DEBUG              # verbose logging
ytbot --force                        # start even if the lock is held
```

Press `Ctrl+C` for graceful shutdown.

In Telegram, send `/start` or `/help` to the bot, then paste a supported link:

- YouTube video: `https://www.youtube.com/watch?v=VIDEO_ID` or `https://youtu.be/VIDEO_ID`
- YouTube playlist: `https://www.youtube.com/playlist?list=PLAYLIST_ID`
- Twitter/X: `https://x.com/username/status/POST_ID`

The bot detects the platform, asks audio vs. video (YouTube), reports progress, and stores the result.

Telegram commands:

| Command | Description |
| --- | --- |
| `/start` | Start using the bot |
| `/help` | Show help |
| `/status` | System status |
| `/storage` | Storage status (admin) |
| `/cancel` | Cancel active downloads |

### One-shot download from the terminal

Download without touching Telegram — useful while the bot is paused by flood control:

```bash
# Single video or audio
ytbot --download "https://www.youtube.com/watch?v=VIDEO_ID" --type video
ytbot --download "https://www.youtube.com/watch?v=VIDEO_ID" --type audio

# Entire playlist (expanded; each video downloaded and uploaded separately)
ytbot --download "https://www.youtube.com/playlist?list=PLAYLIST_ID" --type video
```

- `--download <url>` triggers one-shot mode; `--type video|audio` is **mandatory**
- Playlists print `[i/N]` progress per video and a final `succeeded / failed / total` summary
- Files follow the normal storage flow: Nextcloud first, local storage + cache queue on failure
- Exit code `0` = all items succeeded, `1` = any failure; the process exits immediately when done

### Utility commands

```bash
ytbot --status             # validate config and show instance status
ytbot --cache-status       # inspect the pending upload cache queue
ytbot --retry-cache        # upload cached files once and exit
ytbot --version            # print version
```

## Storage strategy

1. Upload to Nextcloud when configured and reachable; remove the local temp file on success
2. On failure, mark Nextcloud unavailable and save the file locally, adding it to the cache queue
3. Retry cached uploads automatically via the bot's background task, or manually with `ytbot --retry-cache`

## Architecture

```
ytbot/
├── cli.py                     # Entry point: bot mode + one-shot/utility commands
├── core/                      # Config, logging, startup, process lock, user state
├── platforms/                 # Per-platform handlers
│   ├── youtube.py             # YouTube videos & playlists (yt-dlp)
│   └── twitter.py             # Twitter/X scraping (Playwright)
├── services/
│   ├── telegram_service.py    # Telegram polling and messaging
│   ├── download_service.py    # Platform dispatch for downloads
│   ├── storage_service.py     # Nextcloud + local/cache storage
│   └── one_shot_download.py   # Telegram-free one-shot orchestration
├── handlers/
│   └── telegram_handler.py    # Telegram command/message flows
├── storage/                   # Local storage, Nextcloud WebDAV, cache manager
└── monitoring/                # Health and connection monitors
```

New platforms are added by implementing the `PlatformHandler` interface in `platforms/` and registering it in `DownloadService._setup_platforms()`.

## Development

```bash
pip install -e ".[dev]"        # test/lint/type-check dependencies
pytest tests/unit              # run unit tests
pytest --cov=ytbot             # with coverage
black ytbot/                   # formatting
mypy ytbot/                    # type checking
```

## Troubleshooting

- **Bot won't start** — run `ytbot --status`; verify `TELEGRAM_BOT_TOKEN` and `ADMIN_CHAT_ID`, and check `ytbot.log`
- **"Another instance is already running"** — stop the running bot, or start with `--force`
- **Downloads failing** — verify ffmpeg is installed, the URL is accessible, and disk space is available; rerun with `--log-level DEBUG`
- **Nextcloud upload issues** — check credentials, server reachability, and the remote directory; files are kept locally and queued for retry
- **Telegram flood control** — wait for the pause to end, or use `ytbot --download <url> --type ...` to keep downloading from the terminal

## License

Licensed under the MIT License — see [LICENSE](LICENSE).
