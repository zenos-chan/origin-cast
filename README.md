<div align="center">

# OriginCast

**A lightweight, self-hosted Movies & TV streaming server with chunked HTTP byte-range playback, zero-download Google Drive streaming, and automatic metadata enrichment.**

[![License: MIT](https://img.shields.io/badge/License-MIT-indigo.svg?style=for-the-badge)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.x-000000?style=for-the-badge&logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white)](Dockerfile)
[![GitHub Stars](https://img.shields.io/github/stars/zenos-chan/origin-cast?style=for-the-badge&color=f59e0b)](https://github.com/zenos-chan/origin-cast/stargazers)
[![GitHub Issues](https://img.shields.io/github/issues/zenos-chan/origin-cast?style=for-the-badge&color=10b981)](https://github.com/zenos-chan/origin-cast/issues)

</div>

---

## Features

- **Chunked Network Streaming (Not Direct Play)** — Streams local and cloud media via HTTP `206 Partial Content` in bounded `512 KB` range windows (`64 KB` chunks) with `ETag` range caching, `HEAD`/`OPTIONS` CORS support, and zero full-file memory buffering.
- **Large-File & Slow-Network Resilience** — Built-in **Slow-Net Buffer Guard** (`6s` pre-buffer cushion), live buffer health badge (`BUF X.Xs`), and automatic exponential-backoff reconnect recovery on stalled or dropped chunks.
- **Zero-Download Google Drive Streaming** — Paste shared Google Drive Movies and TV folder links (or IDs, with optional Drive API v3 key) in the Admin Console to browse and stream directly from Google Drive without downloading files to local disk.
- **Local & Cloud Library Switches** — Independently toggle **Local Library** (`movies/` & `tv/`) and **Cloud Library (Google Drive)** on or off from the Admin Dashboard, Scanner, or Settings.
- **Continue Watching (Last 3 Watched)** — Automatically tracks the last 3 watched movies and TV shows (exact timestamp, plus season & episode for TV) and provides one-click **Resume** cards on the homepage.
- **Automatic Metadata Engine** — Enriches local and Google Drive titles via IMDb, TVMaze, optional OMDb, and local `metadata.json` / `.cloud_metadata.json` storage (including a `cron_metadata.py` sync/watcher script).
- **Modern Cinema Player** — Powered by Artplayer with automatic `.srt` to WebVTT subtitle conversion, drag-and-drop subtitles/audio, Web Audio boost (`100%`–`300%`), Speech Clarity / Night Mode compressor, and keyboard shortcuts (`?`).
- **Admin Console (`/admin`)** — Protected dashboard for managing catalogs, library switches, Google Drive folders, scanner diagnostics, and metadata refreshes.

---

## Quick Start

### Option 1: Docker Compose (Recommended)

1. Clone the repository:
   ```bash
   git clone https://github.com/zenos-chan/origin-cast.git
   cd origin-cast
   ```

2. Place any local media inside `./movies` and `./tv` (or leave them empty and connect Google Drive folders in Admin Settings).

3. Start the container:
   ```bash
   docker compose up -d --build
   ```

4. Open OriginCast in your browser:
   - **Public Catalog:** `http://localhost:5000`
   - **Admin Console:** `http://localhost:5000/admin` (default credentials: `admin` / `admin123`)

---

### Option 2: Docker CLI

```bash
docker build -t origincast .

docker run -d \
  --name origincast \
  -p 5000:5000 \
  -e ADMIN_USER=admin \
  -e ADMIN_PASS=change-me \
  -e SECRET_KEY=replace-with-a-random-secret \
  -v "$(pwd)/movies:/app/movies" \
  -v "$(pwd)/tv:/app/tv" \
  -v origincast_data:/app/data \
  --restart unless-stopped \
  origincast
```

---

### Option 3: Local Python Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run locally (localhost:5000)
python app.py

# Or bind to all network interfaces for LAN streaming
python app.py --host 0.0.0.0 --port 5000 --no-debug
```

---

## Media & Google Drive Organization

### Local Directories
```text
movies/
└── Inception (2010)/
    ├── Inception (2010).mp4
    ├── Inception (2010).en.srt     # Optional subtitles (.srt, .vtt, .sub, .ass)
    ├── poster.jpg                  # Optional local artwork
    └── metadata.json               # Optional local metadata

tv/
└── Breaking Bad (2008)/
    ├── Season 01/
    │   ├── Breaking Bad S01E01.mkv
    │   ├── Breaking Bad S01E01.en.srt
    │   └── Breaking Bad S01E02.mkv
    └── metadata.json
```

### Google Drive Streaming Setup
1. Share your Google Drive **Movies** and/or **TV Shows** folders with **"Anyone with the link" (Viewer)**.
2. Sign in to `/admin` -> **Settings**.
3. Paste the shared folder URLs or folder IDs (and optionally a Google Drive API v3 key for faster large-folder listing).
4. Click **Save & Scan Google Drive** — titles appear in the catalog and stream directly via `/stream/gdrive/<file_id>` with zero local downloads, while fetched IMDb/TVMaze metadata is stored locally in `.cloud_metadata.json`.

---

## Configuration Reference

### Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `HOST` | `127.0.0.1` (`0.0.0.0` in Docker) | Server bind address |
| `PORT` | `5000` | HTTP server port |
| `MOVIES_DIR` | `./movies` (`/app/movies` in Docker) | Local movies directory path |
| `TV_DIR` | `./tv` (`/app/tv` in Docker) | Local TV shows directory path |
| `DATA_DIR` | `.` (`/app/data` in Docker) | Directory for cache, watch history, Drive config, and cloud metadata |
| `ADMIN_USER` | `admin` | Admin portal username |
| `ADMIN_PASS` | `admin123` | Admin portal password |
| `SECRET_KEY` | built-in fallback | Flask session signing secret |
| `OMDB_API_KEY` | unset | Optional OMDb API key for extra movie metadata |
| `GDRIVE_API_KEY` | unset | Optional Google Drive API v3 key |
| `GDRIVE_MOVIES_FOLDER` | unset | Default Google Drive Movies folder URL or ID |
| `GDRIVE_TV_FOLDER` | unset | Default Google Drive TV Shows folder URL or ID |

### CLI Flags (`app.py`)

| Flag | Description |
| --- | --- |
| `-m`, `--movie-dir` | Override local movies directory |
| `-t`, `--tv-dir` | Override local TV shows directory |
| `--host` | Host IP to bind to |
| `-p`, `--port` | Port to listen on |
| `--no-debug` | Disable Flask debug reloader |

### Metadata Cron / Watcher (`cron_metadata.py`)

```bash
# One-shot metadata sync (local folders + Google Drive cloud store)
python cron_metadata.py

# Force refresh all metadata
python cron_metadata.py --force

# Run continuously as a background watcher
python cron_metadata.py --watch --interval 30
```

---

## Project Structure

```text
origin-cast/
├── app.py                  # Flask server, chunked range & Google Drive streaming routes
├── cron_metadata.py        # Metadata sync CLI / watcher for local & Drive folders
├── metadata_fetcher.py     # IMDb & TVMaze metadata lookup helpers
├── engine/
│   ├── engine.py           # Library scanner, cache, cloud metadata store, watch history
│   └── gdrive.py           # Google Drive folder parser & zero-download range proxy client
├── static/js/
│   ├── artplayer.js        # Artplayer bundle
│   └── player.js           # Custom audio engine, subtitles, buffer guard, watch-history sync
├── templates/              # Jinja2 templates for public catalog, player, and /admin console
├── Dockerfile              # Production container image definition
├── docker-compose.yml      # Ready-to-run Docker Compose stack
├── requirements.txt        # Python dependencies
└── LICENSE                 # MIT License
```

---

## License

Distributed under the **MIT License**. See [LICENSE](LICENSE) for full details.
