import os
import re
import platform
import functools
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Iterator
import urllib.parse
from flask import Flask, render_template, jsonify, request, redirect, url_for, session, flash
import flask
from engine import Engine
from engine.gdrive import guess_video_mimetype

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "origincast-secret-admin-key-2026-secure")

# Default admin authentication credentials
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin123")

# Default global media engine instance
media_engine = Engine(
    movies_dir=os.environ.get("MOVIES_DIR", "./movies"),
    tv_dir=os.environ.get("TV_DIR", "./tv"),
    cache_file=".metadata_cache.json",
    auto_fetch_online=True
)


def init_engine(movies_dir: str = "./movies", tv_dir: str = "./tv") -> Engine:
    """Initializes or reconfigures the global media engine with dynamic directories."""
    global media_engine
    media_engine = Engine(
        movies_dir=movies_dir,
        tv_dir=tv_dir,
        cache_file=".metadata_cache.json",
        auto_fetch_online=True
    )
    return media_engine


# ---------------------------------------------------------------------------
# Template Filters & Context Processors
# ---------------------------------------------------------------------------
@app.template_filter("filesize")
def format_file_size(bytes_val):
    """Formats raw byte count into human-readable B, KB, MB, GB, TB string."""
    try:
        bytes_val = float(bytes_val or 0)
    except (ValueError, TypeError):
        return "0 B"
    if bytes_val <= 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_val < 1024.0:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.1f} PB"


@app.context_processor
def inject_admin_globals():
    """Injects admin status, library source states, and catalog counts across all templates."""
    movies_len = len(media_engine.movies) if media_engine.movies else 0
    tv_len = len(media_engine.tv_shows) if media_engine.tv_shows else 0
    return {
        "is_admin": session.get("admin_logged_in", False),
        "admin_user": session.get("admin_user", "admin"),
        "movies_count": movies_len,
        "tv_count": tv_len,
        "library_sources": media_engine.get_library_sources(),
    }


# ---------------------------------------------------------------------------
# Authentication Decorator
# ---------------------------------------------------------------------------
def admin_required(f):
    """Restricts access to authenticated administrators."""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("admin_logged_in"):
            flash("Please sign in to access the administrator console.", "warning")
            return redirect(url_for("admin_login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function


# ---------------------------------------------------------------------------
# Subtitle Discovery & Conversion Helpers
# ---------------------------------------------------------------------------
SUBTITLE_EXTENSIONS = {".vtt", ".srt", ".sub", ".ass"}


def srt_to_webvtt(srt_text: str) -> str:
    """Converts SubRip (.srt) subtitle text to standard WebVTT (.vtt) format."""
    text = srt_text.lstrip("\ufeff")
    # Replace comma milliseconds separator with period: 00:01:23,456 --> 00:01:25.456
    pattern = re.compile(r"(\d{2}:\d{2}:\d{2}),(\d{3})")
    vtt = pattern.sub(r"\1.\2", text)
    return "WEBVTT\n\n" + vtt.strip() + "\n"


STREAM_CHUNK_SIZE = 64 * 1024           # 64 KB per yielded network chunk for fast TTFB on slow links
STREAM_RANGE_WINDOW = 512 * 1024        # 512 KB max byte range per HTTP 206 response
RANGE_HEADER_RE = re.compile(r"^bytes=(\d*)-(\d*)$", re.IGNORECASE)


def resolve_media_path(filepath: str) -> Optional[Path]:
    """Resolves a media or subtitle path strictly within configured movies or TV directories."""
    if not filepath:
        return None

    decoded_path = urllib.parse.unquote(filepath)
    allowed_roots = [
        Path(media_engine.movies_dir).resolve(),
        Path(media_engine.tv_dir).resolve(),
    ]

    candidates = [Path(decoded_path)]
    if not candidates[0].is_absolute():
        candidates.append(Path(".").resolve() / decoded_path)
        for root in allowed_roots:
            candidates.append(root / decoded_path)

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_file() and any(resolved.is_relative_to(root) for root in allowed_roots):
            return resolved
    return None


def parse_range_header(range_header: str, file_size: int) -> Optional[Tuple[int, int]]:
    """
    Parses an HTTP Range header and returns a bounded (start, end) inclusive byte range.
    Caps each response window to STREAM_RANGE_WINDOW so players stream in chunks rather
    than downloading the entire file in a single response.
    """
    if file_size <= 0 or not range_header:
        return None

    # Handle single byte-range specifiers; if multiple ranges are sent, use the first
    first_spec = range_header.split(",", 1)[0].strip()
    match = RANGE_HEADER_RE.match(first_spec)
    if not match:
        return None

    start_str, end_str = match.group(1), match.group(2)
    if not start_str and not end_str:
        return None

    if not start_str:
        # Suffix-byte-range-spec: bytes=-N (last N bytes)
        suffix_len = int(end_str)
        if suffix_len <= 0:
            return None
        start = max(0, file_size - suffix_len)
        end = min(start + STREAM_RANGE_WINDOW - 1, file_size - 1)
        return start, end

    start = int(start_str)
    if start >= file_size:
        return None

    if end_str:
        req_end = int(end_str)
        if start > req_end:
            return None
        end = min(req_end, start + STREAM_RANGE_WINDOW - 1, file_size - 1)
    else:
        end = min(start + STREAM_RANGE_WINDOW - 1, file_size - 1)

    return start, end


def stream_file_chunks(
    file_path: Path,
    start: int,
    length: int,
    chunk_size: int = STREAM_CHUNK_SIZE
) -> Iterator[bytes]:
    """Generator yielding bounded byte chunks from file_path starting at byte offset `start`."""
    try:
        with open(file_path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                data = f.read(min(chunk_size, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data
    except (BrokenPipeError, ConnectionResetError, GeneratorExit):
        return


def find_subtitles_for_file(filepath: str) -> List[Dict[str, Any]]:
    """
    Finds subtitle files associated with a given video filepath.
    Looks in the same directory and common subdirectories ('subs', 'subtitles').
    """
    target = resolve_media_path(filepath)
    if not target or not target.parent.exists():
        return []

    parent_dir = target.parent
    candidate_dirs = [parent_dir]
    for subfolder in ["subs", "subtitles", "Subs", "Subtitles"]:
        sub_d = parent_dir / subfolder
        if sub_d.is_dir():
            candidate_dirs.append(sub_d)

    subtitles = []
    seen_paths = set()
    video_stem = target.stem.lower()

    lang_map = {
        "en": "English", "eng": "English", "english": "English",
        "es": "Spanish", "spa": "Spanish", "spanish": "Spanish",
        "fr": "French", "fre": "French", "fra": "French",
        "de": "German", "ger": "German", "deu": "German",
        "hi": "Hindi", "hin": "Hindi", "hindi": "Hindi",
        "ja": "Japanese", "jpn": "Japanese", "japanese": "Japanese",
        "zh": "Chinese", "chi": "Chinese", "chinese": "Chinese",
        "it": "Italian", "ita": "Italian", "italian": "Italian",
        "pt": "Portuguese", "por": "Portuguese",
        "ru": "Russian", "rus": "Russian",
        "ar": "Arabic", "ara": "Arabic",
        "ko": "Korean", "kor": "Korean"
    }

    for d in candidate_dirs:
        for f in sorted(d.iterdir()):
            if f.is_file() and f.suffix.lower() in SUBTITLE_EXTENSIONS:
                if str(f) in seen_paths:
                    continue
                seen_paths.add(str(f))

                try:
                    rel_path = str(f.relative_to(Path(".").resolve()))
                except ValueError:
                    rel_path = str(f)

                f_name = f.stem
                matched_lang = None
                tokens = re.split(r"[\._\-\s]", f_name.lower())
                for tok in tokens:
                    if tok in lang_map:
                        matched_lang = lang_map[tok]
                        break

                if matched_lang:
                    clean_lang = matched_lang
                elif f_name.lower() == video_stem:
                    clean_lang = "Default"
                else:
                    clean_lang = f.stem

                subtitles.append({
                    "name": clean_lang,
                    "url": f"/subtitles/{urllib.parse.quote(rel_path)}",
                    "file_path": rel_path,
                    "type": "vtt" if f.suffix.lower() == ".vtt" else "srt"
                })

    return subtitles


# ---------------------------------------------------------------------------
# Public Front-end Routes
# ---------------------------------------------------------------------------
@app.route("/")
def home():
    """Home route: Renders homepage with folder-based movies and TV metadata."""
    context = media_engine.get_homepage_context()
    return render_template("homepage.html", **context)


@app.route("/movies")
def movies():
    """Movies route: Returns all scanned movies."""
    context = media_engine.get_homepage_context()
    return render_template("homepage.html", **context)


@app.route("/tv")
def tv():
    """TV route: Returns all scanned TV shows."""
    context = media_engine.get_homepage_context()
    return render_template("homepage.html", **context)


def normalize_slug(text: str) -> str:
    """Normalizes any text or path segment to a clean lowercase hyphenated slug."""
    text = (text or "").lower().strip()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return re.sub(r'-+', '-', text).strip('-')


@app.route("/movie/<path:movie_slug>")
def movie_detail(movie_slug):
    """Movie player and detail page for viewing local video files by clean slug."""
    if not media_engine.movies:
        media_engine.scan_movies()
    raw_query = urllib.parse.unquote(movie_slug).strip().lower()
    norm_query = normalize_slug(raw_query)

    selected_movie = None
    for m in media_engine.movies:
        m_slug = str(m.get("slug") or m.get("id", "")).lower()
        m_title = str(m.get("title", "")).lower()
        norm_title = normalize_slug(m_title)
        if norm_query in (m_slug, norm_title, normalize_slug(m_slug)) or raw_query in (m_slug, m_title):
            selected_movie = m
            break

    if not selected_movie:
        for m in media_engine.movies:
            m_slug = str(m.get("slug") or m.get("id", "")).lower()
            if m_slug.startswith(norm_query) or norm_query.startswith(m_slug):
                selected_movie = m
                break

    if not selected_movie and media_engine.movies:
        selected_movie = media_engine.movies[0]
    if not selected_movie:
        return redirect(url_for("home"))

    if selected_movie.get("source") == "gdrive":
        subtitles = selected_movie.get("gdrive_subtitles", [])
    else:
        subtitles = find_subtitles_for_file(selected_movie.get("file_path", ""))
    other_movies = [m for m in media_engine.movies if m.get("id") != selected_movie.get("id")][:6]
    resume_t = request.args.get("t", type=float)
    return render_template(
        "movie_detail.html",
        movie=selected_movie,
        subtitles=subtitles,
        other_movies=other_movies,
        resume_t=resume_t,
    )


@app.route("/tv/<path:tv_slug>")
def tv_detail(tv_slug):
    """TV series player and interactive episode browser page by clean slug."""
    if not media_engine.tv_shows:
        media_engine.scan_tv()
    raw_query = urllib.parse.unquote(tv_slug).strip().lower()
    if raw_query.startswith("tv_") or raw_query.startswith("tv-"):
        raw_query = raw_query[3:]
    norm_query = normalize_slug(raw_query)

    selected_show = None
    for t in media_engine.tv_shows:
        t_slug = str(t.get("slug") or t.get("id", "")).lower()
        t_title = str(t.get("title", "")).lower()
        norm_title = normalize_slug(t_title)
        if norm_query in (t_slug, norm_title, normalize_slug(t_slug)) or raw_query in (t_slug, t_title):
            selected_show = t
            break

    if not selected_show:
        for t in media_engine.tv_shows:
            t_slug = str(t.get("slug") or t.get("id", "")).lower()
            if t_slug.startswith(norm_query) or norm_query.startswith(t_slug):
                selected_show = t
                break

    if not selected_show and media_engine.tv_shows:
        selected_show = media_engine.tv_shows[0]
    if not selected_show:
        return redirect(url_for("home"))

    req_season = request.args.get("season", "1")
    req_episode = request.args.get("episode", "1")
    seasons = selected_show.get("seasons", {})
    current_ep = None
    if seasons:
        season_eps = seasons.get(str(req_season))
        if not season_eps and str(req_season).isdigit():
            season_eps = seasons.get(int(req_season))
        if not season_eps:
            season_eps = seasons.get(list(seasons.keys())[0])
        if season_eps:
            for ep in season_eps:
                if str(ep.get("episode")) == str(req_episode):
                    current_ep = ep
                    break
            if not current_ep:
                current_ep = season_eps[0]

    if current_ep and current_ep.get("source") == "gdrive":
        subtitles = current_ep.get("gdrive_subtitles", [])
    else:
        subtitles = find_subtitles_for_file(current_ep.get("file_path", "")) if current_ep else []
    resume_t = request.args.get("t", type=float)
    return render_template(
        "tv_detail.html",
        show=selected_show,
        seasons=seasons,
        current_episode=current_ep,
        subtitles=subtitles,
        resume_t=resume_t,
    )


@app.route("/stream/<path:filepath>", methods=["GET", "HEAD", "OPTIONS"])
def stream_video(filepath):
    """
    Streams media video files over the network in bounded HTTP 206 byte-range chunks
    via a streaming generator instead of direct file serving.
    Optimized for large files and slow connections with small 64 KB chunks,
    512 KB range windows, range caching, and CORS headers.
    """
    cors_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "Range, Origin, Content-Type, Accept",
        "Access-Control-Expose-Headers": "Content-Range, Content-Length, Accept-Ranges, X-Stream-Mode, ETag"
    }

    if request.method == "OPTIONS":
        return flask.Response(status=204, headers=cors_headers)

    target = resolve_media_path(filepath)
    if not target:
        return jsonify({"status": "error", "message": f"File '{filepath}' not found."}), 404

    ext = target.suffix.lower()
    mimetypes = {
        ".mp4": "video/mp4",
        ".m4v": "video/mp4",
        ".webm": "video/webm",
        ".mkv": "video/webm",
        ".mov": "video/quicktime",
        ".avi": "video/x-msvideo"
    }
    mimetype = mimetypes.get(ext, "video/mp4")
    stat_info = target.stat()
    file_size = stat_info.st_size
    etag = f'"{file_size}-{int(stat_info.st_mtime)}"'

    common_headers = {
        **cors_headers,
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=3600",
        "ETag": etag,
        "X-Stream-Mode": "network-chunked"
    }

    range_header = request.headers.get("Range")
    if range_header:
        byte_range = parse_range_header(range_header, file_size)
        if byte_range is None:
            return flask.Response(
                status=416,
                headers={
                    **cors_headers,
                    "Content-Range": f"bytes */{file_size}",
                    "Accept-Ranges": "bytes"
                }
            )

        start, end = byte_range
        chunk_length = end - start + 1
        range_headers = {
            **common_headers,
            "Content-Range": f"bytes {start}-{end}/{file_size}"
        }
        if request.method == "HEAD":
            resp = flask.Response(status=206, mimetype=mimetype, headers=range_headers)
            resp.headers["Content-Length"] = str(chunk_length)
            return resp

        range_headers["Content-Length"] = str(chunk_length)
        return flask.Response(
            stream_file_chunks(target, start, chunk_length),
            status=206,
            mimetype=mimetype,
            direct_passthrough=True,
            headers=range_headers
        )

    if request.method == "HEAD":
        resp = flask.Response(status=200, mimetype=mimetype, headers=common_headers)
        resp.headers["Content-Length"] = str(file_size)
        return resp

    if file_size == 0:
        return flask.Response(
            b"",
            status=200,
            mimetype=mimetype,
            headers={**common_headers, "Content-Length": "0"}
        )

    return flask.Response(
        stream_file_chunks(target, 0, file_size),
        status=200,
        mimetype=mimetype,
        direct_passthrough=True,
        headers={**common_headers, "Content-Length": str(file_size)}
    )


def stream_upstream_chunks(resp, chunk_size: int = STREAM_CHUNK_SIZE) -> Iterator[bytes]:
    """Yields bounded byte chunks directly from an upstream HTTP socket without disk writes."""
    try:
        while True:
            data = resp.read(chunk_size)
            if not data:
                break
            yield data
    except (BrokenPipeError, ConnectionResetError, GeneratorExit):
        return
    finally:
        try:
            resp.close()
        except Exception:
            pass


def find_gdrive_media_filename(file_id: str) -> str:
    """Looks up the indexed filename for a Google Drive file ID if present."""
    for m in media_engine.movies:
        if m.get("gdrive_file_id") == file_id:
            return Path(m.get("file_path", "")).name
    for show in media_engine.tv_shows:
        for eps in (show.get("seasons") or {}).values():
            for ep in eps:
                if ep.get("gdrive_file_id") == file_id:
                    return Path(ep.get("file_path", "")).name
    return ""


def find_gdrive_subtitles_by_file_id(file_id: str) -> List[Dict[str, Any]]:
    """Finds cached Google Drive subtitle tracks associated with a Drive video file ID."""
    for m in media_engine.movies:
        if m.get("gdrive_file_id") == file_id:
            return m.get("gdrive_subtitles", [])
    for show in media_engine.tv_shows:
        for eps in (show.get("seasons") or {}).values():
            for ep in eps:
                if ep.get("gdrive_file_id") == file_id:
                    return ep.get("gdrive_subtitles", [])
    return []


@app.route("/stream/gdrive/<file_id>", methods=["GET", "HEAD", "OPTIONS"])
def stream_gdrive_video(file_id):
    """
    Streams a Google Drive video file directly over HTTP byte-range chunks
    without downloading the video to local disk.
    """
    cors_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "Range, Origin, Content-Type, Accept",
        "Access-Control-Expose-Headers": "Content-Range, Content-Length, Accept-Ranges, X-Stream-Mode, ETag",
    }

    if request.method == "OPTIONS":
        return flask.Response(status=204, headers=cors_headers)

    if not re.match(r"^[a-zA-Z0-9_-]{10,}$", file_id or ""):
        return jsonify({"status": "error", "message": "Invalid Google Drive file ID."}), 400

    try:
        target = media_engine.gdrive_client.resolve_stream_target(file_id)
    except Exception as exc:
        return jsonify({"status": "error", "message": f"Unable to stream Google Drive file: {exc}"}), 502

    total_size = int(target.get("total_size") or 0)
    filename = find_gdrive_media_filename(file_id)
    upstream_ctype = target.get("content_type") or ""
    mimetype = guess_video_mimetype(filename, fallback=upstream_ctype if "video/" in upstream_ctype else "video/mp4")

    common_headers = {
        **cors_headers,
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=3600",
        "X-Stream-Mode": "gdrive-network-chunked",
    }

    range_header = request.headers.get("Range")
    if range_header:
        if total_size > 0:
            byte_range = parse_range_header(range_header, total_size)
            if byte_range is None:
                return flask.Response(
                    status=416,
                    headers={
                        **cors_headers,
                        "Content-Range": f"bytes */{total_size}",
                        "Accept-Ranges": "bytes",
                    },
                )
            start, end = byte_range
        else:
            match = RANGE_HEADER_RE.match(range_header.split(",", 1)[0].strip())
            if not match or (not match.group(1) and not match.group(2)):
                return flask.Response(status=416, headers={**cors_headers, "Accept-Ranges": "bytes"})
            start = int(match.group(1) or 0)
            req_end = int(match.group(2)) if match.group(2) else (start + STREAM_RANGE_WINDOW - 1)
            end = min(req_end, start + STREAM_RANGE_WINDOW - 1)

        if request.method == "HEAD":
            chunk_len = end - start + 1
            head_headers = {**common_headers}
            if total_size > 0:
                head_headers["Content-Range"] = f"bytes {start}-{end}/{total_size}"
            resp = flask.Response(status=206, mimetype=mimetype, headers=head_headers)
            resp.headers["Content-Length"] = str(chunk_len)
            return resp

        try:
            upstream_resp, _ = media_engine.gdrive_client.open_range_stream(file_id, start=start, end=end)
        except Exception as exc:
            return jsonify({"status": "error", "message": f"Google Drive range stream failed: {exc}"}), 502

        upstream_status = getattr(upstream_resp, "status", 206)
        resp_headers = {**common_headers}
        if upstream_resp.headers.get("Content-Range"):
            resp_headers["Content-Range"] = upstream_resp.headers["Content-Range"]
        elif total_size > 0 and upstream_status == 206:
            resp_headers["Content-Range"] = f"bytes {start}-{end}/{total_size}"
        if upstream_resp.headers.get("Content-Length"):
            resp_headers["Content-Length"] = upstream_resp.headers["Content-Length"]

        return flask.Response(
            stream_upstream_chunks(upstream_resp),
            status=upstream_status,
            mimetype=mimetype,
            direct_passthrough=True,
            headers=resp_headers,
        )

    if request.method == "HEAD":
        resp = flask.Response(status=200, mimetype=mimetype, headers=common_headers)
        if total_size > 0:
            resp.headers["Content-Length"] = str(total_size)
        return resp

    try:
        upstream_resp, _ = media_engine.gdrive_client.open_range_stream(file_id)
    except Exception as exc:
        return jsonify({"status": "error", "message": f"Google Drive stream failed: {exc}"}), 502

    resp_headers = {**common_headers}
    if upstream_resp.headers.get("Content-Length"):
        resp_headers["Content-Length"] = upstream_resp.headers["Content-Length"]
    elif total_size > 0:
        resp_headers["Content-Length"] = str(total_size)

    return flask.Response(
        stream_upstream_chunks(upstream_resp),
        status=200,
        mimetype=mimetype,
        direct_passthrough=True,
        headers=resp_headers,
    )


@app.route("/subtitles/gdrive/<file_id>")
def stream_gdrive_subtitles(file_id):
    """Streams Google Drive subtitle files directly from memory as WebVTT."""
    if not re.match(r"^[a-zA-Z0-9_-]{10,}$", file_id or ""):
        return jsonify({"status": "error", "message": "Invalid subtitle file ID."}), 400

    try:
        raw_bytes = media_engine.gdrive_client.fetch_bytes(file_id, max_bytes=2 * 1024 * 1024)
    except Exception as exc:
        return jsonify({"status": "error", "message": f"Unable to load Drive subtitle: {exc}"}), 502

    try:
        content = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        content = raw_bytes.decode("latin-1", errors="replace")

    ext = (request.args.get("ext") or "").lower().lstrip(".")
    if ext != "vtt" and not content.lstrip("\ufeff").startswith("WEBVTT"):
        content = srt_to_webvtt(content)

    return flask.Response(
        content,
        mimetype="text/vtt; charset=utf-8",
        headers={"Access-Control-Allow-Origin": "*"},
    )


@app.route("/subtitles/<path:filepath>")
def stream_subtitles(filepath):
    """
    Streams local subtitle files as standard WebVTT.
    Converts .srt files on-the-fly to ensure native browser playback.
    """
    target = resolve_media_path(filepath)
    if not target:
        return jsonify({"status": "error", "message": f"Subtitle '{filepath}' not found."}), 404

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception:
        content = target.read_text(encoding="latin-1", errors="replace")

    ext = target.suffix.lower()
    if ext != ".vtt":
        content = srt_to_webvtt(content)

    return flask.Response(
        content,
        mimetype="text/vtt; charset=utf-8",
        headers={"Access-Control-Allow-Origin": "*"}
    )


@app.route("/api/media/subtitles")
def get_media_subtitles():
    """Returns JSON list of available subtitle tracks for a media file."""
    filepath = request.args.get("filepath", "")
    if not filepath:
        return jsonify({"subtitles": []})
    if filepath.startswith("gdrive/"):
        parts = filepath.split("/")
        file_id = parts[1] if len(parts) > 1 else ""
        return jsonify({"subtitles": find_gdrive_subtitles_by_file_id(file_id)})
    subs = find_subtitles_for_file(filepath)
    return jsonify({"subtitles": subs})


@app.route("/api/watch-history", methods=["GET", "POST"])
def api_watch_history():
    """Returns or updates the last 3 watched movies/TV episodes."""
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        items = media_engine.record_watch_progress(data)
        return jsonify({"status": "success", "items": items})

    return jsonify({"status": "success", "items": media_engine.get_continue_watching(3)})


@app.route("/health")
def health_check():
    """Lightweight container health check endpoint."""
    return jsonify({"status": "ok"}), 200


@app.route("/api/scan")
def api_scan():
    """Public read-only media library status check."""
    results = media_engine.scan_all()
    return jsonify({
        "status": "success",
        "movies_dir": str(media_engine.movies_dir),
        "tv_dir": str(media_engine.tv_dir),
        "movies_count": len(results["movies"]),
        "tv_count": len(results["tv"])
    })


# ---------------------------------------------------------------------------
# Admin Authentication Routes
# ---------------------------------------------------------------------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """Admin login page with default credentials admin / admin123."""
    if session.get("admin_logged_in"):
        return redirect(url_for("admin_dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if username == ADMIN_USER and password == ADMIN_PASS:
            session["admin_logged_in"] = True
            session["admin_user"] = username
            flash(f"Welcome back, {username}! Signed in successfully.", "success")
            next_url = request.args.get("next")
            if next_url and next_url.startswith("/admin"):
                return redirect(next_url)
            return redirect(url_for("admin_dashboard"))
        else:
            flash("Invalid username or password. Please try again.", "error")
            return render_template("admin/login.html", username=username)

    return render_template("admin/login.html")


@app.route("/admin/logout")
def admin_logout():
    """Terminates admin session."""
    session.pop("admin_logged_in", None)
    session.pop("admin_user", None)
    flash("You have been signed out of the admin console.", "info")
    return redirect(url_for("admin_login"))


# ---------------------------------------------------------------------------
# Admin Dashboard & Management Routes
# ---------------------------------------------------------------------------
@app.route("/admin")
@admin_required
def admin_root():
    """Redirects /admin to the admin dashboard."""
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    """Admin command center dashboard with metrics and library overview."""
    movies_list = media_engine.movies if media_engine.movies else media_engine.scan_movies()
    tv_list = media_engine.tv_shows if media_engine.tv_shows else media_engine.scan_tv()

    # Calculate storage stats
    movie_bytes = sum(m.get("file_size", 0) for m in movies_list)
    tv_bytes = 0
    total_episodes = 0
    total_files = len(movies_list)

    for show in tv_list:
        total_episodes += show.get("total_episodes", 0)
        seasons = show.get("seasons", {})
        if isinstance(seasons, dict):
            for eps in seasons.values():
                for ep in eps:
                    tv_bytes += ep.get("file_size", 0)
                    total_files += 1

    total_storage = movie_bytes + tv_bytes

    # Cache file status
    cache_path = Path(media_engine.cache_file).resolve()
    cache_file_size = cache_path.stat().st_size if cache_path.exists() else 0
    cache_entries_count = len(media_engine.cache.get("movies", {})) + len(media_engine.cache.get("tv", {}))

    return render_template(
        "admin/dashboard.html",
        active_page="dashboard",
        movies=movies_list,
        tv_shows=tv_list,
        movies_count=len(movies_list),
        tv_count=len(tv_list),
        total_episodes=total_episodes,
        total_storage=total_storage,
        total_files=total_files,
        movies_dir_name=media_engine.movies_dir.name,
        movies_dir_path=str(media_engine.movies_dir.resolve()),
        tv_dir_path=str(media_engine.tv_dir.resolve()),
        cache_file_path=str(cache_path),
        cache_file_size=cache_file_size,
        cache_entries_count=cache_entries_count,
        server_host=request.host.split(":")[0],
        server_port=request.host.split(":")[1] if ":" in request.host else "5000",
        python_version=platform.python_version(),
        flask_version=flask.__version__,
        os_platform=platform.platform()
    )


@app.route("/admin/movies")
@admin_required
def admin_movies():
    """Admin movie inventory management."""
    movies_list = media_engine.movies if media_engine.movies else media_engine.scan_movies()
    return render_template(
        "admin/movies.html",
        active_page="movies",
        movies=movies_list,
        movies_count=len(movies_list)
    )


@app.route("/admin/tv")
@admin_required
def admin_tv():
    """Admin television series inventory management."""
    tv_list = media_engine.tv_shows if media_engine.tv_shows else media_engine.scan_tv()
    return render_template(
        "admin/tv.html",
        active_page="tv",
        tv_shows=tv_list,
        tv_count=len(tv_list)
    )


@app.route("/admin/scanner")
@admin_required
def admin_scanner():
    """Interactive media scanner & diagnostic control panel."""
    movies_list = media_engine.movies if media_engine.movies else media_engine.scan_movies()
    tv_list = media_engine.tv_shows if media_engine.tv_shows else media_engine.scan_tv()
    cache_path = Path(media_engine.cache_file).resolve()
    cache_file_size = cache_path.stat().st_size if cache_path.exists() else 0
    cloud_meta_path = Path(media_engine.cloud_metadata_file).resolve()
    cloud_meta_count = (
        len(media_engine.cloud_metadata.get("movies", {}))
        + len(media_engine.cloud_metadata.get("tv", {}))
    )

    return render_template(
        "admin/scanner.html",
        active_page="scanner",
        movies_count=len(movies_list),
        tv_count=len(tv_list),
        movies_dir_path=str(media_engine.movies_dir.resolve()),
        tv_dir_path=str(media_engine.tv_dir.resolve()),
        cache_file_path=str(cache_path),
        cache_file_size=cache_file_size,
        cloud_metadata_path=str(cloud_meta_path),
        cloud_metadata_count=cloud_meta_count,
        gdrive_config=media_engine.gdrive_config,
        gdrive_status=media_engine.gdrive_status,
    )


@app.route("/admin/settings")
@admin_required
def admin_settings():
    """Server, storage paths, and admin configuration overview."""
    cache_path = Path(media_engine.cache_file).resolve()
    return render_template(
        "admin/settings.html",
        active_page="settings",
        admin_user_configured=ADMIN_USER,
        admin_pass_masked="••••••••",
        movies_dir_path=str(media_engine.movies_dir.resolve()),
        tv_dir_path=str(media_engine.tv_dir.resolve()),
        cache_file_path=str(cache_path),
        gdrive_config=media_engine.gdrive_config,
        gdrive_status=media_engine.gdrive_status,
        python_version=platform.python_version(),
        flask_version=flask.__version__,
        os_platform=platform.platform(),
        workspace_dir=str(Path(".").resolve())
    )


@app.route("/api/admin/gdrive/config", methods=["GET", "POST"])
@admin_required
def api_admin_gdrive_config():
    """Gets or updates Google Drive folder configuration and optionally triggers a library scan."""
    if request.method == "POST":
        data = request.get_json() or {}
        enabled = bool(data.get("enabled", True))
        api_key = str(data.get("api_key", "")).strip()
        movies_folder = str(data.get("movies_folder", "")).strip()
        tv_folder = str(data.get("tv_folder", "")).strip()

        cfg = media_engine.save_gdrive_config(
            enabled=enabled,
            api_key=api_key,
            movies_folder=movies_folder,
            tv_folder=tv_folder,
        )
        results = media_engine.scan_all()
        return jsonify({
            "status": "success",
            "message": "Google Drive settings saved and library scanned.",
            "config": cfg,
            "library_sources": media_engine.get_library_sources(),
            "gdrive_status": media_engine.gdrive_status,
            "movies_count": len(results["movies"]),
            "tv_count": len(results["tv"]),
        })

    return jsonify({
        "status": "success",
        "config": media_engine.gdrive_config,
        "library_sources": media_engine.get_library_sources(),
        "gdrive_status": media_engine.gdrive_status,
    })


@app.route("/api/admin/library/sources", methods=["GET", "POST"])
@admin_required
def api_admin_library_sources():
    """Gets or toggles on/off switches for Local Library and Cloud Library (Google Drive)."""
    if request.method == "POST":
        data = request.get_json() or {}
        local_enabled = data.get("local_enabled")
        cloud_enabled = data.get("cloud_enabled")

        sources = media_engine.save_library_sources(
            local_enabled=local_enabled,
            cloud_enabled=cloud_enabled,
        )
        results = media_engine.scan_all()
        return jsonify({
            "status": "success",
            "message": "Library sources updated.",
            "library_sources": sources,
            "gdrive_status": media_engine.gdrive_status,
            "movies_count": len(results["movies"]),
            "tv_count": len(results["tv"]),
        })

    return jsonify({
        "status": "success",
        "library_sources": media_engine.get_library_sources(),
        "gdrive_status": media_engine.gdrive_status,
    })


@app.route("/api/admin/metadata/refresh", methods=["POST"])
@admin_required
def api_admin_metadata_refresh():
    """Fetches and stores online metadata for Google Drive items (and missing local folders)."""
    data = request.get_json(silent=True) or {}
    force = bool(data.get("force", True))

    local_updated = 0
    if media_engine.local_enabled:
        from cron_metadata import process_directory
        local_updated += process_directory(media_engine.movies_dir, "movie", force=False)
        local_updated += process_directory(media_engine.tv_dir, "tv", force=False)

    results = media_engine.refresh_cloud_metadata(force=force)
    cloud_count = (
        len(media_engine.cloud_metadata.get("movies", {}))
        + len(media_engine.cloud_metadata.get("tv", {}))
    )
    return jsonify({
        "status": "success",
        "message": "Metadata fetched and stored locally.",
        "movies_count": len(results["movies"]),
        "tv_count": len(results["tv"]),
        "local_updated": local_updated,
        "cloud_metadata_count": cloud_count,
        "gdrive_status": media_engine.gdrive_status,
    })


@app.route("/api/admin/scan", methods=["POST"])
@admin_required
def api_admin_scan():
    """API endpoint to trigger a fresh scan from the admin dashboard."""
    results = media_engine.scan_all()
    return jsonify({
        "status": "success",
        "message": "Library re-scanned successfully",
        "movies_count": len(results["movies"]),
        "tv_count": len(results["tv"]),
        "library_sources": media_engine.get_library_sources(),
        "gdrive_status": media_engine.gdrive_status,
        "movies_dir": str(media_engine.movies_dir),
        "tv_dir": str(media_engine.tv_dir)
    })


def parse_cli_args():
    """Parses command-line arguments for dynamic directories and server configuration."""
    parser = argparse.ArgumentParser(description="OriginCast Media Server")
    parser.add_argument(
        "--movie-dir", "-m",
        default=os.environ.get("MOVIES_DIR", "./movies"),
        help="Path to movies directory (default: ./movies)"
    )
    parser.add_argument(
        "--tv-dir", "-t",
        default=os.environ.get("TV_DIR", "./tv"),
        help="Path to TV shows directory (default: ./tv)"
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("HOST", "127.0.0.1"),
        help="Host IP to bind to (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=int(os.environ.get("PORT", "5000")),
        help="Port to listen on (default: 5000)"
    )
    parser.add_argument(
        "--no-debug",
        dest="debug",
        action="store_false",
        help="Disable Flask debug mode"
    )
    parser.set_defaults(debug=True)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_cli_args()
    init_engine(movies_dir=args.movie_dir, tv_dir=args.tv_dir)

    print("[*] Starting OriginCast Server...")
    print(f"    Movies directory: {args.movie_dir}")
    print(f"    TV directory:     {args.tv_dir}")
    print(f"    Public URL:       http://{args.host}:{args.port}")
    print(f"    Admin Portal:     http://{args.host}:{args.port}/admin")
    print(f"    Default Admin:    {ADMIN_USER} / {ADMIN_PASS}")

    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
