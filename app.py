import os
import re
import platform
import functools
import argparse
from pathlib import Path
from typing import List, Dict, Any
import urllib.parse
from flask import Flask, render_template, jsonify, request, redirect, url_for, session, flash, send_file
import flask
from engine import Engine
from extensions import default_extension_manager
from downloader import DownloadManager

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "origincast-secret-admin-key-2026-secure")

# Default admin authentication credentials
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin123")

# Default global media engine instance
media_engine = Engine(
    movies_dir="./movies",
    tv_dir="./tv",
    cache_file=".metadata_cache.json",
    auto_fetch_online=True
)

# Background media downloader manager
download_manager = DownloadManager(
    movies_dir="./movies",
    tv_dir="./tv",
    on_complete_callback=lambda: media_engine.scan_all()
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
    download_manager.movies_dir = Path(movies_dir).resolve()
    download_manager.tv_dir = Path(tv_dir).resolve()
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
    """Injects admin status and catalog counts across all templates."""
    movies_len = len(media_engine.movies) if media_engine.movies else 0
    tv_len = len(media_engine.tv_shows) if media_engine.tv_shows else 0
    exts_len = len(default_extension_manager.extensions)
    active_dl = len(download_manager.tasks)
    return {
        "is_admin": session.get("admin_logged_in", False),
        "admin_user": session.get("admin_user", "admin"),
        "movies_count": movies_len,
        "tv_count": tv_len,
        "extensions_count": exts_len,
        "active_downloads_count": active_dl
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


def find_subtitles_for_file(filepath: str) -> List[Dict[str, Any]]:
    """
    Finds subtitle files associated with a given video filepath.
    Looks in the same directory and common subdirectories ('subs', 'subtitles').
    """
    if not filepath:
        return []
    target = Path(filepath)
    if not target.is_absolute():
        target = (Path(".").resolve() / filepath).resolve()
    else:
        target = target.resolve()

    if not target.is_file():
        for base in [Path(media_engine.movies_dir).resolve(), Path(media_engine.tv_dir).resolve()]:
            c = (base / filepath).resolve()
            if c.is_file():
                target = c
                break

    if not target.parent.exists():
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


@app.route("/movie/<path:movie_id>")
def movie_detail(movie_id):
    """Movie player and detail page for viewing local video files."""
    if not media_engine.movies:
        media_engine.scan_movies()
    decoded_id = urllib.parse.unquote(movie_id).strip().lower()
    selected_movie = None
    for m in media_engine.movies:
        m_id = str(m.get("id", "")).lower()
        m_title = str(m.get("title", "")).lower()
        if decoded_id in (m_id, m_title) or decoded_id.replace("-", " ") in (m_id.replace("-", " "), m_title):
            selected_movie = m
            break
    if not selected_movie and media_engine.movies:
        selected_movie = media_engine.movies[0]
    if not selected_movie:
        return redirect(url_for("home"))

    subtitles = find_subtitles_for_file(selected_movie.get("file_path", ""))
    other_movies = [m for m in media_engine.movies if m.get("id") != selected_movie.get("id")][:6]
    return render_template(
        "movie_detail.html",
        movie=selected_movie,
        subtitles=subtitles,
        other_movies=other_movies
    )


@app.route("/tv/<path:tv_id>")
def tv_detail(tv_id):
    """TV series player and interactive episode browser page."""
    if not media_engine.tv_shows:
        media_engine.scan_tv()
    decoded_id = urllib.parse.unquote(tv_id).strip().lower()
    selected_show = None
    for t in media_engine.tv_shows:
        t_id = str(t.get("id", "")).lower()
        t_title = str(t.get("title", "")).lower()
        if decoded_id in (t_id, t_title) or decoded_id.replace("-", " ") in (t_id.replace("-", " "), t_title):
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
        season_eps = seasons.get(str(req_season)) or seasons.get(list(seasons.keys())[0])
        if season_eps:
            for ep in season_eps:
                if str(ep.get("episode")) == str(req_episode):
                    current_ep = ep
                    break
            if not current_ep:
                current_ep = season_eps[0]

    subtitles = find_subtitles_for_file(current_ep.get("file_path", "")) if current_ep else []
    return render_template(
        "tv_detail.html",
        show=selected_show,
        seasons=seasons,
        current_episode=current_ep,
        subtitles=subtitles
    )


@app.route("/stream/<path:filepath>")
def stream_video(filepath):
    """
    Streams media video files supporting HTTP 206 Range requests for seeking.
    Resolves video files stored within movies/ or tv/ directory structures.
    """
    decoded_path = urllib.parse.unquote(filepath)
    target = Path(decoded_path)
    if not target.is_absolute():
        target = (Path(".").resolve() / decoded_path).resolve()
    else:
        target = target.resolve()

    if not target.is_file():
        for base in [Path(media_engine.movies_dir).resolve(), Path(media_engine.tv_dir).resolve()]:
            c = (base / decoded_path).resolve()
            if c.is_file():
                target = c
                break

    if not target.is_file():
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
    return send_file(target, mimetype=mimetype, conditional=True, as_attachment=False)


@app.route("/subtitles/<path:filepath>")
def stream_subtitles(filepath):
    """
    Streams local subtitle files as standard WebVTT.
    Converts .srt files on-the-fly to ensure native browser playback.
    """
    decoded_path = urllib.parse.unquote(filepath)
    target = Path(decoded_path)
    if not target.is_absolute():
        target = (Path(".").resolve() / decoded_path).resolve()
    else:
        target = target.resolve()

    if not target.is_file():
        for base in [Path(media_engine.movies_dir).resolve(), Path(media_engine.tv_dir).resolve()]:
            c = (base / decoded_path).resolve()
            if c.is_file():
                target = c
                break

    if not target.is_file():
        return jsonify({"status": "error", "message": f"Subtitle '{filepath}' not found."}), 404

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception:
        content = target.read_text(encoding="latin-1", errors="replace")

    ext = target.suffix.lower()
    if ext != ".vtt":
        content = srt_to_webvtt(content)

    return flask.Response(content, mimetype="text/vtt; charset=utf-8")


@app.route("/api/media/subtitles")
def get_media_subtitles():
    """Returns JSON list of available subtitle tracks for a media file."""
    filepath = request.args.get("filepath", "")
    if not filepath:
        return jsonify({"subtitles": []})
    subs = find_subtitles_for_file(filepath)
    return jsonify({"subtitles": subs})


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

    return render_template(
        "admin/scanner.html",
        active_page="scanner",
        movies_count=len(movies_list),
        tv_count=len(tv_list),
        movies_dir_path=str(media_engine.movies_dir.resolve()),
        tv_dir_path=str(media_engine.tv_dir.resolve()),
        cache_file_path=str(cache_path),
        cache_file_size=cache_file_size
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
        python_version=platform.python_version(),
        flask_version=flask.__version__,
        os_platform=platform.platform(),
        workspace_dir=str(Path(".").resolve())
    )


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
        "movies_dir": str(media_engine.movies_dir),
        "tv_dir": str(media_engine.tv_dir)
    })


# ---------------------------------------------------------------------------
# Extensions Management Routes & Scraper APIs
# ---------------------------------------------------------------------------
@app.route("/admin/extensions")
@admin_required
def admin_extensions():
    """Scraper extensions management dashboard with latest movies grid."""
    exts = default_extension_manager.list_extensions()
    default_ext_id = exts[0]["id"] if exts else "hdhub4u"
    latest_movies = default_extension_manager.get_latest(default_ext_id)
    current_ext = default_extension_manager.get_extension(default_ext_id)
    return render_template(
        "admin/extensions.html",
        active_page="extensions",
        extensions=exts,
        extensions_count=len(exts),
        current_ext_id=default_ext_id,
        current_ext_url=current_ext.base_url if current_ext else "https://new16.hdhub4u.cl",
        initial_movies=latest_movies
    )


@app.route("/api/admin/extensions/<extension_id>/latest", methods=["GET"])
@admin_required
def api_admin_extension_latest(extension_id):
    """Returns grid of latest available movies for the extension."""
    page = request.args.get("page", 1, type=int)
    ext = default_extension_manager.get_extension(extension_id)
    if not ext:
        return jsonify({"status": "error", "message": f"Extension '{extension_id}' not found."}), 404

    try:
        movies = ext.get_latest(page=page) if hasattr(ext, "get_latest") else []
        return jsonify({"status": "success", "results": movies, "count": len(movies)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/admin/extensions", methods=["GET", "POST"])
@admin_required
def api_admin_extensions():
    """Returns list of extensions or creates a new modular scraper extension."""
    if request.method == "POST":
        data = request.get_json() or {}
        ext_id = data.get("id", "").strip()
        name = data.get("name", "").strip()
        base_url = data.get("base_url", "").strip()
        description = data.get("description", "").strip()

        if not name or not base_url:
            return jsonify({"status": "error", "message": "Extension name and base URL are required."}), 400

        try:
            new_ext = default_extension_manager.create_custom_extension(
                ext_id=ext_id,
                name=name,
                base_url=base_url,
                description=description
            )
            return jsonify({
                "status": "success",
                "message": f"Extension '{new_ext.name}' registered successfully.",
                "extension": new_ext.to_dict()
            })
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify({"status": "success", "extensions": default_extension_manager.list_extensions()})


@app.route("/api/admin/extensions/<extension_id>", methods=["DELETE"])
@admin_required
def api_admin_delete_extension(extension_id):
    """Deletes custom extension."""
    try:
        success = default_extension_manager.delete_custom_extension(extension_id)
        if success:
            return jsonify({"status": "success", "message": f"Extension '{extension_id}' deleted."})
        return jsonify({"status": "error", "message": "Could not delete extension."}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route("/api/admin/extensions/<extension_id>/toggle", methods=["POST"])
@admin_required
def api_admin_toggle_extension(extension_id):
    """Enables or disables a scraper extension."""
    data = request.get_json() or {}
    enabled = data.get("enabled")
    try:
        new_state = default_extension_manager.toggle_extension(extension_id, enabled)
        return jsonify({"status": "success", "extension_id": extension_id, "enabled": new_state})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 404


@app.route("/api/admin/extensions/<extension_id>/url", methods=["POST"])
@admin_required
def api_admin_update_extension_url(extension_id):
    """Updates target mirror or base URL for an extension."""
    data = request.get_json() or {}
    new_url = data.get("url", "").strip()
    if not new_url:
        return jsonify({"status": "error", "message": "URL parameter is required."}), 400
    try:
        updated = default_extension_manager.update_extension_url(extension_id, new_url)
        return jsonify({"status": "success", "extension_id": extension_id, "base_url": updated})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route("/api/admin/extensions/<extension_id>/search", methods=["POST"])
@admin_required
def api_admin_extension_search(extension_id):
    """Executes search query using the selected scraper extension."""
    data = request.get_json() or {}
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"status": "error", "message": "Query parameter is required."}), 400

    ext = default_extension_manager.get_extension(extension_id)
    if not ext:
        return jsonify({"status": "error", "message": f"Extension '{extension_id}' not found."}), 404

    try:
        results = ext.search(query)
        return jsonify({"status": "success", "results": results, "query": query, "count": len(results)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/admin/extensions/<extension_id>/scrape", methods=["POST"])
@admin_required
def api_admin_extension_scrape(extension_id):
    """Scrapes movie details, available video qualities, and download links from a URL."""
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"status": "error", "message": "URL parameter is required."}), 400

    ext = default_extension_manager.get_extension(extension_id)
    if not ext:
        return jsonify({"status": "error", "message": f"Extension '{extension_id}' not found."}), 404

    try:
        scrape_data = ext.scrape(url)
        return jsonify({"status": "success", "data": scrape_data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ---------------------------------------------------------------------------
# Background Downloader Routes & Management APIs
# ---------------------------------------------------------------------------
@app.route("/admin/downloads")
@admin_required
def admin_downloads():
    """Download management dashboard showing active and completed tasks."""
    tasks_data = download_manager.get_all_tasks()
    return render_template(
        "admin/downloads.html",
        active_page="downloads",
        active=tasks_data["active"],
        completed=tasks_data["completed"],
        movies_dir_path=str(download_manager.movies_dir.resolve()),
        tv_dir_path=str(download_manager.tv_dir.resolve())
    )


@app.route("/api/admin/downloads", methods=["GET"])
@admin_required
def api_admin_downloads():
    """Returns active downloads, speeds, ETAs, and completed tasks."""
    return jsonify(download_manager.get_all_tasks())


@app.route("/api/admin/downloads/add", methods=["POST"])
@admin_required
def api_admin_downloads_add():
    """Queues a new download task with high-speed segmented streaming."""
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    title = data.get("title", "").strip()
    quality = data.get("quality", "1080p").strip()
    media_type = data.get("type", "movie").strip()
    size_hint = data.get("size", "").strip()
    poster = data.get("poster", "").strip()

    if not url or not title:
        return jsonify({"status": "error", "message": "URL and Title are required."}), 400

    try:
        task = download_manager.add_download(
            url=url,
            title=title,
            quality=quality,
            media_type=media_type,
            size_hint=size_hint,
            poster=poster
        )
        return jsonify({
            "status": "success",
            "message": f"Download task for '{title}' queued.",
            "task": task.to_dict()
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/admin/downloads/<task_id>/pause", methods=["POST"])
@admin_required
def api_admin_downloads_pause(task_id):
    """Pauses an ongoing download task."""
    success = download_manager.pause_task(task_id)
    if success:
        return jsonify({"status": "success", "message": "Task paused."})
    return jsonify({"status": "error", "message": "Task not found."}), 404


@app.route("/api/admin/downloads/<task_id>/resume", methods=["POST"])
@admin_required
def api_admin_downloads_resume(task_id):
    """Resumes a paused download task."""
    success = download_manager.resume_task(task_id)
    if success:
        return jsonify({"status": "success", "message": "Task resumed."})
    return jsonify({"status": "error", "message": "Task not found."}), 404


@app.route("/api/admin/downloads/<task_id>", methods=["DELETE"])
@admin_required
def api_admin_downloads_delete(task_id):
    """Cancels/deletes download task and partial/finished files."""
    success = download_manager.delete_task(task_id)
    if success:
        return jsonify({"status": "success", "message": "Task deleted."})
    return jsonify({"status": "error", "message": "Task not found."}), 404


@app.route("/api/admin/downloads/<task_id>/retry", methods=["POST"])
@admin_required
def api_admin_downloads_retry(task_id):
    """Retries a failed download task."""
    success = download_manager.retry_task(task_id)
    if success:
        return jsonify({"status": "success", "message": "Task queued for retry."})
    return jsonify({"status": "error", "message": "Task not found."}), 404


@app.route("/api/admin/downloads/clear-completed", methods=["POST"])
@admin_required
def api_admin_downloads_clear_completed():
    """Clears completed downloads history list."""
    download_manager.completed_history.clear()
    download_manager._save_history()
    return jsonify({"status": "success", "message": "Completed history cleared."})


def parse_cli_args():
    """Parses command-line arguments for dynamic directories and server configuration."""
    parser = argparse.ArgumentParser(description="OriginCast Media Server")
    parser.add_argument(
        "--movie-dir", "-m",
        default="./movies",
        help="Path to movies directory (default: ./movies)"
    )
    parser.add_argument(
        "--tv-dir", "-t",
        default="./tv",
        help="Path to TV shows directory (default: ./tv)"
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host IP to bind to (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=5000,
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

    app.run(host=args.host, port=args.port, debug=args.debug)
