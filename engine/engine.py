import os
import re
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Any
import urllib.request
import urllib.parse
import urllib.error
from engine.gdrive import GoogleDriveClient, extract_gdrive_folder_id
from metadata_fetcher import fetch_from_imdb

# Supported extensions
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v'}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
SUBTITLE_EXTENSIONS = {'.vtt', '.srt', '.sub', '.ass'}

SUBTITLE_LANG_MAP = {
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
    "ko": "Korean", "kor": "Korean",
}

# Regex patterns for cleaning titles and extracting metadata
PAREN_YEAR_REGEX = re.compile(r'[\(\[]\s*(19\d{2}|20\d{2})\s*[\)\]]')
YEAR_REGEX = re.compile(r'\b(19\d{2}|20\d{2})\b')
SEASON_EPISODE_REGEX = re.compile(r'[Ss](\d{1,2})[Ee](\d{1,2})|(\d{1,2})x(\d{1,2})')
SEASON_FOLDER_REGEX = re.compile(r'[Ss]eason\s*(\d{1,2})', re.IGNORECASE)

CLEANUP_TOKENS = [
    r'1080p', r'720p', r'480p', r'2160p', r'4k', r'bluray', r'bdrip', r'brrip',
    r'web-dl', r'webrip', r'hdrip', r'dvdrip', r'x264', r'x265', r'hevc', r'aac',
    r'ac3', r'dts', r'yify', r'rarbg', r'eztv', r'10bit', r'hdr', r'remux',
    r'proper', r'repack', r'extended', r'unrated'
]
CLEANUP_REGEX = re.compile(r'\b(' + '|'.join(CLEANUP_TOKENS) + r')\b', re.IGNORECASE)

# Curated visual posters & backdrops for common media titles
CURATED_ART = {
    "the odessye": {
        "poster": "https://images.unsplash.com/photo-1506703719100-a0f3a48c0f86?auto=format&fit=crop&w=600&q=80",
        "backdrop": "https://images.unsplash.com/photo-1451187580459-43490279c0fa?auto=format&fit=crop&w=1920&q=80",
        "rating": "9.1",
        "genres": ["Sci-Fi", "Adventure"],
        "overview": "An epic odyssey across cosmic expanses to discover humanity's primeval origins."
    },
    "inception": {
        "poster": "https://images.unsplash.com/photo-1514565131-fce0801e5785?auto=format&fit=crop&w=600&q=80",
        "backdrop": "https://images.unsplash.com/photo-1486406146926-c627a92ad1ab?auto=format&fit=crop&w=1920&q=80",
        "rating": "8.8",
        "genres": ["Action", "Sci-Fi"],
        "overview": "A thief who steals corporate secrets through the use of dream-sharing technology is given the inverse task of planting an idea."
    },
    "interstellar": {
        "poster": "https://images.unsplash.com/photo-1536440136628-849c177e76a1?auto=format&fit=crop&w=600&q=80",
        "backdrop": "https://images.unsplash.com/photo-1506703719100-a0f3a48c0f86?auto=format&fit=crop&w=1920&q=80",
        "rating": "8.7",
        "genres": ["Adventure", "Drama", "Sci-Fi"],
        "overview": "When Earth becomes uninhabitable in the future, a farmer and ex-NASA pilot is tasked to pilot a spacecraft along with a team of researchers."
    },
    "dune part two": {
        "poster": "https://images.unsplash.com/photo-1509198397868-475647b2a1e5?auto=format&fit=crop&w=600&q=80",
        "backdrop": "https://images.unsplash.com/photo-1509316975850-ff9c5deb0cd9?auto=format&fit=crop&w=1920&q=80",
        "rating": "8.6",
        "genres": ["Action", "Adventure", "Sci-Fi"],
        "overview": "Paul Atreides unites with Chani and the Fremen while seeking revenge against the conspirators who destroyed his family."
    },
    "blade runner 2049": {
        "poster": "https://images.unsplash.com/photo-1578632767115-351597cf2477?auto=format&fit=crop&w=600&q=80",
        "backdrop": "https://images.unsplash.com/photo-1518709268805-4e9042af9f23?auto=format&fit=crop&w=1920&q=80",
        "rating": "8.0",
        "genres": ["Action", "Drama", "Mystery"],
        "overview": "Young Blade Runner K's discovery of a long-buried secret leads him to track down former Blade Runner Rick Deckard."
    },
    "the dark knight": {
        "poster": "https://images.unsplash.com/photo-1478760329108-5c3ed9d495a0?auto=format&fit=crop&w=600&q=80",
        "backdrop": "https://images.unsplash.com/photo-1509198397868-475647b2a1e5?auto=format&fit=crop&w=1920&q=80",
        "rating": "9.0",
        "genres": ["Action", "Crime", "Drama"],
        "overview": "When the menace known as the Joker wreaks havoc and chaos on the people of Gotham, Batman must accept one of the greatest psychological tests."
    },
    "shadow protocol": {
        "poster": "https://images.unsplash.com/photo-1518709268805-4e9042af9f23?auto=format&fit=crop&w=600&q=80",
        "backdrop": "https://images.unsplash.com/photo-1534447677768-be436bb09401?auto=format&fit=crop&w=1920&q=80",
        "rating": "9.2",
        "genres": ["Drama", "Mystery", "Thriller"],
        "overview": "When an undercover operative is betrayed by their agency, they must expose a syndicate operating within global power structures."
    },
    "cyber city": {
        "poster": "https://images.unsplash.com/photo-1526374965328-7f61d4dc18c5?auto=format&fit=crop&w=600&q=80",
        "backdrop": "https://images.unsplash.com/photo-1574375927938-d5a98e8ffe85?auto=format&fit=crop&w=1920&q=80",
        "rating": "8.8",
        "genres": ["Sci-Fi", "Crime"],
        "overview": "In a sprawling neon metropolis, bounty hunters uncover an artificial consciousness controlling the city's power grid."
    },
    "severance": {
        "poster": "https://images.unsplash.com/photo-1497366216548-37526070297c?auto=format&fit=crop&w=600&q=80",
        "backdrop": "https://images.unsplash.com/photo-1497215728101-856f4ea42174?auto=format&fit=crop&w=1920&q=80",
        "rating": "8.7",
        "genres": ["Drama", "Mystery", "Sci-Fi"],
        "overview": "Mark leads a team of office workers whose memories have been surgically divided between their work and personal lives."
    },
    "the last of us": {
        "poster": "https://images.unsplash.com/photo-1511497584788-87676104235f?auto=format&fit=crop&w=600&q=80",
        "backdrop": "https://images.unsplash.com/photo-1506744038136-46273834b3fb?auto=format&fit=crop&w=1920&q=80",
        "rating": "8.8",
        "genres": ["Action", "Adventure", "Drama"],
        "overview": "After a global pandemic destroys civilization, a hardened survivor takes charge of a 14-year-old girl who may be humanity's last hope."
    },
    "stranger things": {
        "poster": "https://images.unsplash.com/photo-1509114397022-ed747cca3f65?auto=format&fit=crop&w=600&q=80",
        "backdrop": "https://images.unsplash.com/photo-1518709268805-4e9042af9f23?auto=format&fit=crop&w=1920&q=80",
        "rating": "8.7",
        "genres": ["Drama", "Fantasy", "Horror"],
        "overview": "When a young boy vanishes, a small town uncovers a mystery involving secret experiments, terrifying supernatural forces and one strange little girl."
    },
    "breaking bad": {
        "poster": "https://images.unsplash.com/photo-1534447677768-be436bb09401?auto=format&fit=crop&w=600&q=80",
        "backdrop": "https://images.unsplash.com/photo-1509316975850-ff9c5deb0cd9?auto=format&fit=crop&w=1920&q=80",
        "rating": "9.5",
        "genres": ["Crime", "Drama", "Thriller"],
        "overview": "A chemistry teacher diagnosed with inoperable lung cancer turns to manufacturing and selling methamphetamine with a former student."
    }
}


def format_timestamp(seconds: float) -> str:
    """Formats seconds into MM:SS or H:MM:SS for continue-watching displays."""
    total = max(0, int(seconds or 0))
    hours, rem = divmod(total, 3600)
    mins, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


def extract_year(text: str) -> Optional[str]:
    """Finds a 4-digit release year, prioritizing bracketed (YYYY) or [YYYY] years."""
    paren_match = PAREN_YEAR_REGEX.search(text)
    if paren_match:
        return paren_match.group(1)
    matches = YEAR_REGEX.findall(text)
    return matches[-1] if matches else None


def clean_title(raw_name: str, strip_year: bool = True) -> str:
    """Cleans punctuation, quality tags, and extra spaces from a filename or folder name."""
    name = raw_name
    # First, strip parenthesized year like (2026) or [2026]
    if strip_year:
        name = PAREN_YEAR_REGEX.sub(' ', name)
    # Remove remaining bracketed/parenthetical quality or group info like [1080p] or (YIFY)
    name = re.sub(r'\[.*?\]|\(.*?\)', ' ', name)
    # Replace dots, underscores, dashes with spaces
    name = re.sub(r'[\._\-+]', ' ', name)
    # Remove common video release keywords
    name = CLEANUP_REGEX.sub(' ', name)
    # Remove multiple spaces and strip
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def slugify(text: str) -> str:
    """Converts a title or string into a clean, URL-safe kebab-case slug."""
    text = (text or "").lower().strip()
    # Replace non-alphanumeric characters with hyphens
    text = re.sub(r'[^a-z0-9]+', '-', text)
    # Collapse multiple hyphens and trim
    return re.sub(r'-+', '-', text).strip('-')


class Engine:
    """
    OriginCast Metadata Engine.
    Scans media folders (movies & TV shows), parses filenames/directory structures,
    detects local artwork and metadata, and optionally fetches metadata online.
    """

    def __init__(
        self,
        movies_dir: str = "movies",
        tv_dir: str = "tv",
        cache_file: str = ".metadata_cache.json",
        auto_fetch_online: bool = True,
        omdb_api_key: Optional[str] = None,
        gdrive_config_file: str = ".gdrive_config.json",
        library_config_file: str = ".library_config.json",
        cloud_metadata_file: str = ".cloud_metadata.json",
        watch_history_file: str = ".watch_history.json",
    ):
        data_dir = Path(os.environ.get("DATA_DIR", "."))
        if os.environ.get("DATA_DIR"):
            data_dir.mkdir(parents=True, exist_ok=True)

        def _state_path(p_str: str) -> Path:
            p = Path(p_str)
            if not p.is_absolute() and p.parent == Path("."):
                return data_dir / p
            return p

        self.movies_dir = Path(movies_dir)
        self.tv_dir = Path(tv_dir)
        self.cache_file = _state_path(cache_file)
        self.auto_fetch_online = auto_fetch_online
        self.omdb_api_key = omdb_api_key or os.environ.get("OMDB_API_KEY")
        self.gdrive_config_file = _state_path(gdrive_config_file)
        self.library_config_file = _state_path(library_config_file)
        self.cloud_metadata_file = _state_path(cloud_metadata_file)
        self.watch_history_file = _state_path(watch_history_file)

        self.cache: Dict[str, Any] = {
            "movies": {},
            "tv": {}
        }
        self.cloud_metadata: Dict[str, Any] = {
            "movies": {},
            "tv": {}
        }
        self.watch_history: List[Dict[str, Any]] = []
        self.movies: List[Dict[str, Any]] = []
        self.tv_shows: List[Dict[str, Any]] = []
        self.gdrive_status: Dict[str, Any] = {
            "movies_error": "",
            "tv_error": "",
            "movies_count": 0,
            "tv_count": 0,
        }

        self.local_enabled: bool = self.load_library_config()
        self.gdrive_config: Dict[str, Any] = self.load_gdrive_config()
        self.gdrive_client = GoogleDriveClient(api_key=self.gdrive_config.get("api_key", ""))

        self.load_cloud_metadata()
        self.load_watch_history()
        self.load_cache()

    def load_watch_history(self) -> List[Dict[str, Any]]:
        """Loads persisted last-watched history (up to 3 most recent items)."""
        if self.watch_history_file.exists():
            try:
                with open(self.watch_history_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self.watch_history = data[:3]
                    return self.watch_history
            except Exception:
                pass
        self.watch_history = []
        return self.watch_history

    def save_watch_history(self) -> None:
        """Persists last-watched 3 items to disk."""
        try:
            with open(self.watch_history_file, "w", encoding="utf-8") as f:
                json.dump(self.watch_history[:3], f, indent=2)
        except Exception as exc:
            print(f"[!] Warning: Could not save watch history: {exc}")

    def record_watch_progress(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Records or updates watch progress for a Movie or TV episode,
        retaining only the 3 most recently watched items.
        """
        media_id = str(payload.get("media_id") or "").strip()
        media_type = str(payload.get("media_type") or "movie").strip().lower()
        if media_type not in ("movie", "tv") or not media_id:
            return self.watch_history[:3]

        try:
            position_seconds = max(0, int(float(payload.get("position_seconds") or 0)))
        except (ValueError, TypeError):
            position_seconds = 0
        try:
            duration_seconds = max(0, int(float(payload.get("duration_seconds") or 0)))
        except (ValueError, TypeError):
            duration_seconds = 0

        progress_percent = (
            round(min(100.0, (position_seconds / duration_seconds) * 100.0), 1)
            if duration_seconds > 0
            else 0.0
        )

        season = None
        episode = None
        episode_title = ""
        if media_type == "tv":
            try:
                season = int(payload.get("season") or 1)
            except (ValueError, TypeError):
                season = 1
            try:
                episode = int(payload.get("episode") or 1)
            except (ValueError, TypeError):
                episode = 1
            episode_title = str(payload.get("episode_title") or f"Episode {episode}").strip()
            resume_url = f"/tv/{media_id}?season={season}&episode={episode}&t={position_seconds}"
        else:
            resume_url = f"/movie/{media_id}?t={position_seconds}"

        entry = {
            "media_id": media_id,
            "media_type": media_type,
            "title": str(payload.get("title") or media_id).strip(),
            "poster": str(payload.get("poster") or "").strip(),
            "backdrop": str(payload.get("backdrop") or payload.get("poster") or "").strip(),
            "year": str(payload.get("year") or "").strip(),
            "position_seconds": position_seconds,
            "duration_seconds": duration_seconds,
            "position_formatted": format_timestamp(position_seconds),
            "duration_formatted": format_timestamp(duration_seconds) if duration_seconds > 0 else "",
            "progress_percent": progress_percent,
            "season": season,
            "episode": episode,
            "episode_title": episode_title,
            "resume_url": resume_url,
            "updated_at": time.time(),
        }

        self.load_watch_history()
        filtered = [
            item for item in self.watch_history
            if not (item.get("media_id") == media_id and item.get("media_type") == media_type)
        ]
        filtered.insert(0, entry)
        self.watch_history = filtered[:3]
        self.save_watch_history()
        return self.watch_history

    def get_continue_watching(self, limit: int = 3) -> List[Dict[str, Any]]:
        """Returns up to `limit` most recently watched items present in active catalogs."""
        self.load_watch_history()
        if not self.movies:
            self.scan_movies()
        if not self.tv_shows:
            self.scan_tv()

        active_movies = {m.get("id"): m for m in self.movies}
        active_tv = {t.get("id"): t for t in self.tv_shows}

        items: List[Dict[str, Any]] = []
        for entry in self.watch_history:
            m_type = entry.get("media_type")
            m_id = entry.get("media_id")
            catalog_item = active_movies.get(m_id) if m_type == "movie" else active_tv.get(m_id)
            if not catalog_item:
                continue
            enriched = dict(entry)
            enriched["title"] = catalog_item.get("title") or enriched.get("title")
            enriched["poster"] = catalog_item.get("poster") or enriched.get("poster")
            enriched["backdrop"] = catalog_item.get("backdrop") or enriched.get("backdrop") or enriched["poster"]
            enriched["year"] = catalog_item.get("year") or enriched.get("year")
            enriched["position_formatted"] = format_timestamp(enriched.get("position_seconds", 0))
            dur = enriched.get("duration_seconds", 0)
            enriched["duration_formatted"] = format_timestamp(dur) if dur else ""
            items.append(enriched)
            if len(items) >= limit:
                break
        return items

    def load_cloud_metadata(self) -> Dict[str, Any]:
        """Loads locally persisted metadata for cloud (Google Drive) media."""
        if self.cloud_metadata_file.exists():
            try:
                with open(self.cloud_metadata_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.cloud_metadata = {
                        "movies": data.get("movies", {}) if isinstance(data.get("movies"), dict) else {},
                        "tv": data.get("tv", {}) if isinstance(data.get("tv"), dict) else {},
                    }
                    return self.cloud_metadata
            except Exception:
                pass
        self.cloud_metadata = {"movies": {}, "tv": {}}
        return self.cloud_metadata

    def save_cloud_metadata(self) -> None:
        """Persists cloud media metadata locally so read-only Drive folders retain rich metadata."""
        try:
            with open(self.cloud_metadata_file, "w", encoding="utf-8") as f:
                json.dump(self.cloud_metadata, f, indent=2)
        except Exception as exc:
            print(f"[!] Warning: Could not save cloud metadata store: {exc}")

    # -------------------------------------------------------------------------
    # Library Source & Google Drive Config Persistence
    # -------------------------------------------------------------------------
    def load_library_config(self) -> bool:
        """Loads whether the local media library is enabled (defaults to True)."""
        if self.library_config_file.exists():
            try:
                with open(self.library_config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "local_enabled" in data:
                    return bool(data["local_enabled"])
            except Exception:
                pass
        return True

    def save_library_sources(
        self,
        local_enabled: Optional[bool] = None,
        cloud_enabled: Optional[bool] = None,
    ) -> Dict[str, bool]:
        """Updates on/off switches for local library and cloud (Google Drive) library."""
        if local_enabled is not None:
            self.local_enabled = bool(local_enabled)
            try:
                with open(self.library_config_file, "w", encoding="utf-8") as f:
                    json.dump({"local_enabled": self.local_enabled}, f, indent=2)
            except Exception as exc:
                print(f"[!] Warning: Could not save library config: {exc}")

        if cloud_enabled is not None:
            self.save_gdrive_config(
                enabled=bool(cloud_enabled),
                api_key=self.gdrive_config.get("api_key", ""),
                movies_folder=self.gdrive_config.get("movies_folder", ""),
                tv_folder=self.gdrive_config.get("tv_folder", ""),
            )

        return self.get_library_sources()

    def get_library_sources(self) -> Dict[str, bool]:
        """Returns current on/off switch states for local and cloud libraries."""
        return {
            "local_enabled": bool(self.local_enabled),
            "cloud_enabled": bool(self.gdrive_config.get("enabled", True)),
        }

    def load_gdrive_config(self) -> Dict[str, Any]:
        """Loads Google Drive folder/API configuration from disk or environment."""
        config: Dict[str, Any] = {
            "enabled": True,
            "api_key": os.environ.get("GDRIVE_API_KEY", "").strip(),
            "movies_folder": os.environ.get("GDRIVE_MOVIES_FOLDER", "").strip(),
            "tv_folder": os.environ.get("GDRIVE_TV_FOLDER", "").strip(),
        }
        if self.gdrive_config_file.exists():
            try:
                with open(self.gdrive_config_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    config["enabled"] = bool(loaded.get("enabled", True))
                    if loaded.get("api_key") is not None:
                        config["api_key"] = str(loaded.get("api_key", "")).strip()
                    if loaded.get("movies_folder") is not None:
                        config["movies_folder"] = str(loaded.get("movies_folder", "")).strip()
                    if loaded.get("tv_folder") is not None:
                        config["tv_folder"] = str(loaded.get("tv_folder", "")).strip()
            except Exception:
                pass
        return config

    def save_gdrive_config(
        self,
        enabled: bool = True,
        api_key: str = "",
        movies_folder: str = "",
        tv_folder: str = "",
    ) -> Dict[str, Any]:
        """Saves Google Drive configuration and updates the active Drive client."""
        self.gdrive_config = {
            "enabled": bool(enabled),
            "api_key": (api_key or "").strip(),
            "movies_folder": (movies_folder or "").strip(),
            "tv_folder": (tv_folder or "").strip(),
        }
        self.gdrive_client = GoogleDriveClient(api_key=self.gdrive_config["api_key"])
        try:
            with open(self.gdrive_config_file, "w", encoding="utf-8") as f:
                json.dump(self.gdrive_config, f, indent=2)
        except Exception as exc:
            print(f"[!] Warning: Could not save Google Drive config: {exc}")
        return self.gdrive_config

    # -------------------------------------------------------------------------
    # Cache Persistence
    # -------------------------------------------------------------------------
    def load_cache(self) -> None:
        """Loads cached metadata from disk and migrates old keys to clean kebab-case slugs."""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    self.cache = json.load(f)
            except Exception:
                self.cache = {"movies": {}, "tv": {}}

        # Migrate movie cache keys to clean slugs (preserve gdrive-* keys)
        norm_movies = {}
        for k, m in self.cache.get("movies", {}).items():
            year = m.get("year") or extract_year(m.get("title", "")) or ""
            title = m.get("title") or k
            slug = f"{slugify(title)}-{year}" if year else slugify(title)
            slug = slug.strip('-')
            m["slug"] = slug
            m["id"] = slug
            cache_key = k if (k.startswith("gdrive-") or m.get("source") == "gdrive") else slug
            norm_movies[cache_key] = m
        self.cache["movies"] = norm_movies

        # Migrate TV cache keys to clean slugs (preserve gdrive-tv-* keys)
        norm_tv = {}
        for k, t in self.cache.get("tv", {}).items():
            year = t.get("year") or extract_year(t.get("title", "")) or ""
            title = t.get("title") or k
            if title.lower().startswith("tv_") or title.lower().startswith("tv-"):
                title = title[3:]
            slug = f"{slugify(title)}-{year}" if year else slugify(title)
            slug = slug.strip('-')
            t["slug"] = slug
            t["id"] = slug
            cache_key = k if (k.startswith("gdrive-tv-") or t.get("source") == "gdrive") else slug
            norm_tv[cache_key] = t
        self.cache["tv"] = norm_tv

    def save_cache(self) -> None:
        """Persists metadata cache to disk."""
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=2)
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Local File & Artwork Inspection
    # -------------------------------------------------------------------------
    def find_local_art(self, folder: Path) -> Dict[str, Optional[str]]:
        """Finds local poster, backdrop, or cover image files in a folder."""
        art: Dict[str, Optional[str]] = {"poster": None, "backdrop": None}
        if not folder.is_dir():
            return art

        try:
            for file in folder.iterdir():
                if file.suffix.lower() in IMAGE_EXTENSIONS:
                    stem = file.stem.lower()
                    if stem in ("poster", "cover", "folder", "front") and not art["poster"]:
                        art["poster"] = str(file)
                    elif stem in ("backdrop", "fanart", "background", "hero") and not art["backdrop"]:
                        art["backdrop"] = str(file)
        except Exception:
            pass
        return art

    def find_local_nfo_or_json(self, folder: Path) -> Optional[Dict[str, Any]]:
        """Reads metadata.json or .nfo if present in directory."""
        if not folder.is_dir():
            return None

        meta_file = folder / "metadata.json"
        if meta_file.exists():
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return None

    # -------------------------------------------------------------------------
    # Online Metadata Providers (TVMaze, OMDb fallback, Graceful Fallback)
    # -------------------------------------------------------------------------
    def fetch_tvmaze_show(self, title: str) -> Optional[Dict[str, Any]]:
        """Fetches TV show metadata and episodes from TVMaze API (free, no key needed)."""
        if not self.auto_fetch_online:
            return None

        query = urllib.parse.quote(title)
        url = f"https://api.tvmaze.com/singlesearch/shows?q={query}&embed=episodes"
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "OriginCast/1.0"}
            )
            with urllib.request.urlopen(req, timeout=4) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    return {
                        "title": data.get("name"),
                        "overview": re.sub(r'<[^>]+>', '', data.get("summary") or ""),
                        "rating": str(data.get("rating", {}).get("average") or "8.0"),
                        "year": data.get("premiered", "")[:4] if data.get("premiered") else "",
                        "genres": data.get("genres", []),
                        "poster": (data.get("image") or {}).get("medium") or (data.get("image") or {}).get("original"),
                        "backdrop": (data.get("image") or {}).get("original"),
                        "status": data.get("status")
                    }
        except Exception:
            pass
        return None

    def fetch_omdb_movie(self, title: str, year: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Fetches Movie metadata from OMDb if an API key is configured."""
        if not self.auto_fetch_online or not self.omdb_api_key:
            return None

        params = {"t": title, "apikey": self.omdb_api_key, "type": "movie"}
        if year:
            params["y"] = year
        url = "http://www.omdbapi.com/?" + urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "OriginCast/1.0"})
            with urllib.request.urlopen(req, timeout=4) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    if data.get("Response") == "True":
                        return {
                            "title": data.get("Title"),
                            "year": data.get("Year"),
                            "rating": data.get("imdbRating", "7.5"),
                            "overview": data.get("Plot", ""),
                            "duration": data.get("Runtime", ""),
                            "genres": [g.strip() for g in data.get("Genre", "").split(",") if g.strip()],
                            "poster": data.get("Poster") if data.get("Poster") != "N/A" else None
                        }
        except Exception:
            pass
        return None

    # -------------------------------------------------------------------------
    # Shared Metadata & Google Drive Helpers
    # -------------------------------------------------------------------------
    def _build_movie_record(
        self,
        slug: str,
        parsed_title: str,
        year: Optional[str],
        file_path: str,
        folder_path: str,
        file_size: int,
        modified_time: float,
        local_art: Optional[Dict[str, Optional[str]]] = None,
        local_meta: Optional[Dict[str, Any]] = None,
        source: str = "local",
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Constructs a normalized movie metadata dictionary for local or Drive media."""
        local_art = local_art or {"poster": None, "backdrop": None}
        curated = CURATED_ART.get(parsed_title.lower(), {})

        online_meta = None
        if self.auto_fetch_online and not local_meta:
            if self.omdb_api_key:
                online_meta = self.fetch_omdb_movie(parsed_title, year)
            if not online_meta:
                online_meta = fetch_from_imdb(parsed_title, year)

        poster_img = (
            local_art.get("poster")
            or (local_meta and local_meta.get("poster"))
            or (online_meta and online_meta.get("poster"))
            or curated.get("poster")
            or "https://images.unsplash.com/photo-1536440136628-849c177e76a1?auto=format&fit=crop&w=600&q=80"
        )
        backdrop_img = (
            local_art.get("backdrop")
            or (local_meta and local_meta.get("backdrop"))
            or (online_meta and online_meta.get("backdrop"))
            or curated.get("backdrop")
            or poster_img
        )

        record = {
            "id": slug,
            "slug": slug,
            "title": (local_meta and local_meta.get("title")) or (online_meta and online_meta.get("title")) or parsed_title,
            "year": (local_meta and local_meta.get("year")) or (online_meta and online_meta.get("year")) or year or "2024",
            "rating": (local_meta and local_meta.get("rating")) or (online_meta and online_meta.get("rating")) or curated.get("rating") or "8.0",
            "duration": (local_meta and local_meta.get("duration")) or (online_meta and online_meta.get("duration")) or "2h 00m",
            "genres": (local_meta and local_meta.get("genres")) or (online_meta and online_meta.get("genres")) or curated.get("genres") or ["Action", "Sci-Fi"],
            "overview": (local_meta and local_meta.get("overview")) or (online_meta and online_meta.get("overview")) or curated.get("overview") or f"A feature film: {parsed_title}.",
            "actors": (local_meta and local_meta.get("actors")) or (online_meta and online_meta.get("actors")) or "",
            "imdb_id": (local_meta and local_meta.get("imdb_id")) or (online_meta and online_meta.get("imdb_id")) or "",
            "poster": poster_img,
            "backdrop": backdrop_img,
            "img": poster_img,
            "file_path": file_path,
            "folder_path": folder_path,
            "file_size": file_size,
            "modified_time": modified_time,
            "type": "movie",
            "source": source,
        }
        if extra:
            record.update(extra)
        return record

    def _build_tv_record(
        self,
        slug: str,
        clean_show_title: str,
        year: Optional[str],
        folder_path: str,
        seasons: Dict[int, List[Dict[str, Any]]],
        local_art: Optional[Dict[str, Optional[str]]] = None,
        local_meta: Optional[Dict[str, Any]] = None,
        source: str = "local",
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Constructs a normalized TV series metadata dictionary for local or Drive media."""
        local_art = local_art or {"poster": None, "backdrop": None}
        curated = CURATED_ART.get(clean_show_title.lower(), {})

        online_meta = None
        if self.auto_fetch_online and not local_meta:
            online_meta = self.fetch_tvmaze_show(clean_show_title)
            if not online_meta:
                online_meta = fetch_from_imdb(clean_show_title, year)

        poster_img = (
            local_art.get("poster")
            or (local_meta and local_meta.get("poster"))
            or (online_meta and online_meta.get("poster"))
            or curated.get("poster")
            or "https://images.unsplash.com/photo-1526374965328-7f61d4dc18c5?auto=format&fit=crop&w=600&q=80"
        )
        backdrop_img = (
            local_art.get("backdrop")
            or (local_meta and local_meta.get("backdrop"))
            or (online_meta and online_meta.get("backdrop"))
            or curated.get("backdrop")
            or poster_img
        )

        record = {
            "id": slug,
            "slug": slug,
            "title": (local_meta and local_meta.get("title")) or (online_meta and online_meta.get("title")) or clean_show_title,
            "year": (local_meta and local_meta.get("year")) or (online_meta and online_meta.get("year")) or year or "2024",
            "rating": (local_meta and local_meta.get("rating")) or (online_meta and online_meta.get("rating")) or curated.get("rating") or "8.5",
            "overview": (local_meta and local_meta.get("overview")) or (online_meta and online_meta.get("overview")) or curated.get("overview") or f"TV Series: {clean_show_title}.",
            "genres": (local_meta and local_meta.get("genres")) or (online_meta and online_meta.get("genres")) or curated.get("genres") or ["Drama", "Mystery"],
            "actors": (local_meta and local_meta.get("actors")) or (online_meta and online_meta.get("actors")) or "",
            "imdb_id": (local_meta and local_meta.get("imdb_id")) or (online_meta and online_meta.get("imdb_id")) or "",
            "poster": poster_img,
            "backdrop": backdrop_img,
            "img": poster_img,
            "folder_path": folder_path,
            "season": f"Season {len(seasons) or 1}",
            "seasons_count": len(seasons) or 1,
            "seasons": seasons,
            "total_episodes": sum(len(eps) for eps in seasons.values()),
            "type": "tv",
            "source": source,
        }
        if extra:
            record.update(extra)
        return record

    @staticmethod
    def _extract_gdrive_subtitles(items: List[Dict[str, Any]], video_stem: str = "") -> List[Dict[str, Any]]:
        """Builds subtitle track descriptors from Google Drive folder items."""
        subtitles: List[Dict[str, Any]] = []
        seen_ids = set()
        target_stem = video_stem.lower()

        for item in sorted(items, key=lambda x: x.get("name", "")):
            if item.get("is_folder"):
                continue
            name = item.get("name", "")
            ext = Path(name).suffix.lower()
            if ext not in SUBTITLE_EXTENSIONS:
                continue
            file_id = item.get("id", "")
            if not file_id or file_id in seen_ids:
                continue

            stem = Path(name).stem
            if target_stem and not stem.lower().startswith(target_stem) and len(items) > 4:
                # In multi-video folders, match subtitles belonging to this video stem
                continue

            seen_ids.add(file_id)
            matched_lang = None
            for tok in re.split(r"[\._\-\s]", stem.lower()):
                if tok in SUBTITLE_LANG_MAP:
                    matched_lang = SUBTITLE_LANG_MAP[tok]
                    break

            clean_lang = matched_lang or ("Default" if stem.lower() == target_stem else stem)
            sub_type = "vtt" if ext == ".vtt" else "srt"
            subtitles.append({
                "name": clean_lang,
                "url": f"/subtitles/gdrive/{file_id}?ext={ext.lstrip('.')}",
                "file_id": file_id,
                "file_path": f"gdrive/{file_id}/{name}",
                "type": sub_type,
            })
        return subtitles

    def _fetch_gdrive_metadata_json(self, items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Reads metadata.json from a Google Drive folder if present."""
        for item in items:
            if not item.get("is_folder") and item.get("name", "").lower() == "metadata.json":
                try:
                    raw = self.gdrive_client.fetch_bytes(item["id"], max_bytes=256 * 1024)
                    return json.loads(raw.decode("utf-8"))
                except Exception:
                    return None
        return None

    def scan_gdrive_movies(self, existing_slugs: Optional[set] = None) -> List[Dict[str, Any]]:
        """Scans configured Google Drive movies folder without downloading video files."""
        self.gdrive_status["movies_error"] = ""
        self.gdrive_status["movies_count"] = 0

        if not self.gdrive_config.get("enabled", True):
            return []
        folder_ref = self.gdrive_config.get("movies_folder", "")
        folder_id = extract_gdrive_folder_id(folder_ref)
        if not folder_id:
            return []

        try:
            entries = self.gdrive_client.list_folder(folder_id)
        except Exception as exc:
            self.gdrive_status["movies_error"] = str(exc)
            return []

        used_slugs = set(existing_slugs or ())
        drive_movies: List[Dict[str, Any]] = []

        for entry in sorted(entries, key=lambda x: x.get("name", "")):
            name = entry.get("name", "")
            if not name or name.startswith("."):
                continue

            video_item: Optional[Dict[str, Any]] = None
            sub_candidates: List[Dict[str, Any]] = []
            drive_meta: Optional[Dict[str, Any]] = None
            item_folder_id = folder_id

            if entry.get("is_folder"):
                item_folder_id = entry["id"]
                sub_entries = self.gdrive_client.list_folder(item_folder_id)
                for se in sub_entries:
                    se_name = se.get("name", "")
                    if not se.get("is_folder") and Path(se_name).suffix.lower() in VIDEO_EXTENSIONS:
                        if video_item is None:
                            video_item = se
                    elif se.get("is_folder") and se_name.lower() in ("subs", "subtitles"):
                        sub_candidates.extend(self.gdrive_client.list_folder(se["id"]))
                sub_candidates.extend(sub_entries)
                drive_meta = self._fetch_gdrive_metadata_json(sub_entries)
                raw_title = name
            elif Path(name).suffix.lower() in VIDEO_EXTENSIONS:
                video_item = entry
                sub_candidates = entries
                raw_title = Path(name).stem
            else:
                continue

            if not video_item:
                continue

            year = extract_year(name) or extract_year(video_item.get("name", ""))
            parsed_title = clean_title(raw_title)
            base_slug = f"{slugify(parsed_title)}-{year}" if year else slugify(parsed_title)
            base_slug = base_slug.strip("-") or f"gdrive-{video_item['id'][:8]}"
            slug = base_slug if base_slug not in used_slugs else f"{base_slug}-gdrive"
            used_slugs.add(slug)

            cache_key = f"gdrive-{video_item['id']}"
            video_stem = Path(video_item.get("name", "")).stem
            subtitles = self._extract_gdrive_subtitles(sub_candidates, video_stem if not entry.get("is_folder") else "")

            stored_cloud_meta = self.cloud_metadata.get("movies", {}).get(video_item["id"])
            cached_entry = self.cache.get("movies", {}).get(cache_key)
            has_placeholder_poster = bool(
                cached_entry and "images.unsplash.com" in str(cached_entry.get("poster", ""))
            )

            if cached_entry and (stored_cloud_meta or not has_placeholder_poster):
                meta = dict(cached_entry)
                meta["slug"] = slug
                meta["id"] = slug
                meta["source"] = "gdrive"
                meta["gdrive_file_id"] = video_item["id"]
                meta["stream_url"] = f"/stream/gdrive/{video_item['id']}"
                meta["file_path"] = f"gdrive/{video_item['id']}/{video_item['name']}"
                meta["gdrive_subtitles"] = subtitles
                if video_item.get("size"):
                    meta["file_size"] = video_item["size"]
                if not stored_cloud_meta:
                    self._persist_cloud_meta_entry("movies", video_item["id"], meta, raw_title)
                drive_movies.append(meta)
                continue

            effective_meta = drive_meta or stored_cloud_meta
            record = self._build_movie_record(
                slug=slug,
                parsed_title=parsed_title,
                year=year,
                file_path=f"gdrive/{video_item['id']}/{video_item['name']}",
                folder_path=f"gdrive://folder/{item_folder_id}",
                file_size=int(video_item.get("size") or 0),
                modified_time=time.time(),
                local_meta=effective_meta,
                source="gdrive",
                extra={
                    "gdrive_file_id": video_item["id"],
                    "stream_url": f"/stream/gdrive/{video_item['id']}",
                    "gdrive_subtitles": subtitles,
                },
            )
            self._persist_cloud_meta_entry("movies", video_item["id"], record, raw_title)
            self.cache["movies"][cache_key] = record
            drive_movies.append(record)

        self.save_cloud_metadata()
        self.gdrive_status["movies_count"] = len(drive_movies)
        return drive_movies

    def scan_gdrive_tv(self, existing_slugs: Optional[set] = None) -> List[Dict[str, Any]]:
        """Scans configured Google Drive TV folder without downloading video files."""
        self.gdrive_status["tv_error"] = ""
        self.gdrive_status["tv_count"] = 0

        if not self.gdrive_config.get("enabled", True):
            return []
        folder_ref = self.gdrive_config.get("tv_folder", "")
        folder_id = extract_gdrive_folder_id(folder_ref)
        if not folder_id:
            return []

        try:
            show_entries = self.gdrive_client.list_folder(folder_id)
        except Exception as exc:
            self.gdrive_status["tv_error"] = str(exc)
            return []

        used_slugs = set(existing_slugs or ())
        drive_shows: List[Dict[str, Any]] = []

        for show_entry in sorted(show_entries, key=lambda x: x.get("name", "")):
            if not show_entry.get("is_folder"):
                continue
            raw_show_name = show_entry.get("name", "")
            if not raw_show_name or raw_show_name.startswith("."):
                continue

            clean_show_title = clean_title(raw_show_name)
            year = extract_year(raw_show_name)
            base_slug = f"{slugify(clean_show_title)}-{year}" if year else slugify(clean_show_title)
            base_slug = base_slug.strip("-") or f"gdrive-{show_entry['id'][:8]}"
            slug = base_slug if base_slug not in used_slugs else f"{base_slug}-gdrive"
            used_slugs.add(slug)

            show_items = self.gdrive_client.list_folder(show_entry["id"])
            drive_meta = self._fetch_gdrive_metadata_json(show_items)
            seasons: Dict[int, List[Dict[str, Any]]] = {}

            # Gather (video_item, sibling_items, parent_folder_name) across show root and season subfolders
            folders_to_process: List[tuple] = [(show_items, raw_show_name)]
            for item in show_items:
                if item.get("is_folder") and item.get("name", "").lower() not in ("subs", "subtitles"):
                    sub_items = self.gdrive_client.list_folder(item["id"])
                    folders_to_process.append((sub_items, item.get("name", "")))

            for folder_items, parent_name in folders_to_process:
                for item in sorted(folder_items, key=lambda x: x.get("name", "")):
                    if item.get("is_folder"):
                        continue
                    item_name = item.get("name", "")
                    if Path(item_name).suffix.lower() not in VIDEO_EXTENSIONS:
                        continue

                    match = SEASON_EPISODE_REGEX.search(item_name)
                    season_num = 1
                    ep_num = 1
                    if match:
                        s_str = match.group(1) or match.group(3)
                        e_str = match.group(2) or match.group(4)
                        season_num = int(s_str) if s_str else 1
                        ep_num = int(e_str) if e_str else 1
                    else:
                        season_match = SEASON_FOLDER_REGEX.search(parent_name)
                        if season_match:
                            season_num = int(season_match.group(1))
                        ep_num = len(seasons.get(season_num, [])) + 1

                    if season_num not in seasons:
                        seasons[season_num] = []

                    ep_stem = Path(item_name).stem
                    ep_title = clean_title(ep_stem)
                    ep_subs = self._extract_gdrive_subtitles(folder_items, ep_stem)
                    seasons[season_num].append({
                        "season": season_num,
                        "episode": ep_num,
                        "title": ep_title or f"Episode {ep_num}",
                        "file_path": f"gdrive/{item['id']}/{item_name}",
                        "stream_url": f"/stream/gdrive/{item['id']}",
                        "gdrive_file_id": item["id"],
                        "gdrive_subtitles": ep_subs,
                        "file_size": int(item.get("size") or 0),
                        "source": "gdrive",
                    })

            if not seasons:
                continue

            for s_num in seasons:
                seasons[s_num].sort(key=lambda ep: (ep.get("episode", 0), ep.get("title", "")))

            cache_key = f"gdrive-tv-{show_entry['id']}"
            stored_cloud_meta = self.cloud_metadata.get("tv", {}).get(show_entry["id"])
            cached_entry = self.cache.get("tv", {}).get(cache_key)
            has_placeholder_poster = bool(
                cached_entry and "images.unsplash.com" in str(cached_entry.get("poster", ""))
            )

            if cached_entry and (stored_cloud_meta or not has_placeholder_poster):
                meta = dict(cached_entry)
                meta["slug"] = slug
                meta["id"] = slug
                meta["source"] = "gdrive"
                meta["seasons"] = seasons
                meta["seasons_count"] = len(seasons) or 1
                meta["total_episodes"] = sum(len(eps) for eps in seasons.values())
                if not stored_cloud_meta:
                    self._persist_cloud_meta_entry("tv", show_entry["id"], meta, raw_show_name)
                drive_shows.append(meta)
                continue

            effective_meta = drive_meta or stored_cloud_meta
            record = self._build_tv_record(
                slug=slug,
                clean_show_title=clean_show_title,
                year=year,
                folder_path=f"gdrive://folder/{show_entry['id']}",
                seasons=seasons,
                local_meta=effective_meta,
                source="gdrive",
                extra={"gdrive_folder_id": show_entry["id"]},
            )
            self._persist_cloud_meta_entry("tv", show_entry["id"], record, raw_show_name)
            self.cache["tv"][cache_key] = record
            drive_shows.append(record)

        self.save_cloud_metadata()
        self.gdrive_status["tv_count"] = len(drive_shows)
        return drive_shows

    def _persist_cloud_meta_entry(
        self,
        media_bucket: str,
        cloud_id: str,
        record: Dict[str, Any],
        folder_name: str,
    ) -> None:
        """Stores fetched metadata for a Google Drive item into the local cloud metadata store."""
        if media_bucket not in self.cloud_metadata:
            self.cloud_metadata[media_bucket] = {}
        self.cloud_metadata[media_bucket][cloud_id] = {
            "title": record.get("title", ""),
            "year": record.get("year", ""),
            "rating": record.get("rating", ""),
            "duration": record.get("duration", ""),
            "genres": record.get("genres", []),
            "overview": record.get("overview", ""),
            "actors": record.get("actors", ""),
            "imdb_id": record.get("imdb_id", ""),
            "poster": record.get("poster", ""),
            "backdrop": record.get("backdrop", ""),
            "folder_name": folder_name,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    def refresh_cloud_metadata(self, force: bool = True) -> Dict[str, Any]:
        """Re-fetches online metadata for Google Drive items and stores it in .cloud_metadata.json."""
        if force:
            self.cloud_metadata = {"movies": {}, "tv": {}}
            for bucket in ("movies", "tv"):
                self.cache[bucket] = {
                    k: v for k, v in self.cache.get(bucket, {}).items()
                    if not k.startswith("gdrive-")
                }
        return self.scan_all()

    # -------------------------------------------------------------------------
    # Movie Scanner
    # -------------------------------------------------------------------------
    def scan_movies(self) -> List[Dict[str, Any]]:
        """
        Scans the `movies/` directory and any configured Google Drive movies folder.
        Returns a list of parsed movie metadata dictionaries.
        """
        movies = []
        seen_slugs = set()
        self.load_cloud_metadata()

        if self.local_enabled and self.movies_dir.exists():
            for entry in sorted(self.movies_dir.iterdir()):
                if entry.name.startswith('.'):
                    continue

                movie_path: Optional[Path] = None
                parent_folder: Optional[Path] = None
                raw_title = entry.stem
                year = extract_year(entry.name)

                if entry.is_dir():
                    parent_folder = entry
                    video_files = [f for f in entry.rglob("*") if f.suffix.lower() in VIDEO_EXTENSIONS]
                    if video_files:
                        movie_path = video_files[0]
                elif entry.suffix.lower() in VIDEO_EXTENSIONS:
                    movie_path = entry
                    parent_folder = entry.parent

                if not movie_path:
                    continue

                parsed_title = clean_title(raw_title)
                slug = f"{slugify(parsed_title)}-{year}" if year else slugify(parsed_title)
                slug = slug.strip('-')
                cache_key = slug
                seen_slugs.add(slug)

                if cache_key in self.cache.get("movies", {}):
                    meta = self.cache["movies"][cache_key]
                    meta["file_path"] = str(movie_path)
                    meta["slug"] = slug
                    meta["id"] = slug
                    meta.setdefault("source", "local")
                    movies.append(meta)
                    continue

                local_art = self.find_local_art(parent_folder) if parent_folder else {"poster": None, "backdrop": None}
                local_meta = self.find_local_nfo_or_json(parent_folder) if parent_folder else None

                movie_record = self._build_movie_record(
                    slug=slug,
                    parsed_title=parsed_title,
                    year=year,
                    file_path=str(movie_path),
                    folder_path=str(parent_folder) if parent_folder else "",
                    file_size=movie_path.stat().st_size if movie_path.exists() else 0,
                    modified_time=movie_path.stat().st_mtime if movie_path.exists() else time.time(),
                    local_art=local_art,
                    local_meta=local_meta,
                    source="local",
                )
                self.cache["movies"][cache_key] = movie_record
                movies.append(movie_record)

        # Merge Google Drive movies (streamed directly without downloading)
        movies.extend(self.scan_gdrive_movies(existing_slugs=seen_slugs))

        self.save_cache()
        self.movies = movies
        return movies

    # -------------------------------------------------------------------------
    # TV Shows Scanner
    # -------------------------------------------------------------------------
    def scan_tv(self) -> List[Dict[str, Any]]:
        """
        Scans the `tv/` directory and any configured Google Drive TV folder.
        Returns a list of parsed TV show metadata dictionaries.
        """
        shows = []
        seen_slugs = set()
        self.load_cloud_metadata()

        if self.local_enabled and self.tv_dir.exists():
            for show_dir in sorted(self.tv_dir.iterdir()):
                if not show_dir.is_dir() or show_dir.name.startswith('.'):
                    continue

                raw_show_name = show_dir.name
                clean_show_title = clean_title(raw_show_name)
                year = extract_year(raw_show_name)
                slug = f"{slugify(clean_show_title)}-{year}" if year else slugify(clean_show_title)
                slug = slug.strip('-')
                cache_key = slug
                seen_slugs.add(slug)

                if cache_key in self.cache.get("tv", {}):
                    meta = self.cache["tv"][cache_key]
                    meta["slug"] = slug
                    meta["id"] = slug
                    meta.setdefault("source", "local")
                    shows.append(meta)
                    continue

                seasons: Dict[int, List[Dict[str, Any]]] = {}
                for item in sorted(show_dir.rglob("*")):
                    if item.suffix.lower() in VIDEO_EXTENSIONS:
                        match = SEASON_EPISODE_REGEX.search(item.name)
                        season_num = 1
                        ep_num = 1
                        if match:
                            s_str = match.group(1) or match.group(3)
                            e_str = match.group(2) or match.group(4)
                            season_num = int(s_str) if s_str else 1
                            ep_num = int(e_str) if e_str else 1
                        else:
                            season_match = SEASON_FOLDER_REGEX.search(item.parent.name)
                            if season_match:
                                season_num = int(season_match.group(1))

                        if season_num not in seasons:
                            seasons[season_num] = []

                        ep_title = clean_title(item.stem)
                        seasons[season_num].append({
                            "season": season_num,
                            "episode": ep_num,
                            "title": ep_title or f"Episode {ep_num}",
                            "file_path": str(item),
                            "file_size": item.stat().st_size if item.exists() else 0,
                            "source": "local",
                        })

                local_art = self.find_local_art(show_dir)
                local_meta = self.find_local_nfo_or_json(show_dir)
                show_record = self._build_tv_record(
                    slug=slug,
                    clean_show_title=clean_show_title,
                    year=year,
                    folder_path=str(show_dir),
                    seasons=seasons,
                    local_art=local_art,
                    local_meta=local_meta,
                    source="local",
                )
                self.cache["tv"][cache_key] = show_record
                shows.append(show_record)

        # Merge Google Drive TV shows (streamed directly without downloading)
        shows.extend(self.scan_gdrive_tv(existing_slugs=seen_slugs))

        self.save_cache()
        self.tv_shows = shows
        return shows

    # -------------------------------------------------------------------------
    # Combined & Convenience Queries
    # -------------------------------------------------------------------------
    def scan_all(self) -> Dict[str, Any]:
        """Scans both movies and TV directories."""
        return {
            "movies": self.scan_movies(),
            "tv": self.scan_tv()
        }

    def get_slider_items(self, limit: int = 3) -> List[Dict[str, Any]]:
        """Returns items for the hero slider."""
        if not self.movies:
            self.scan_movies()
        if not self.tv_shows:
            self.scan_tv()
        slider_candidates = []
        if self.movies:
            slider_candidates.extend(self.movies[:2])
        if self.tv_shows:
            slider_candidates.extend(self.tv_shows[:1])
        return slider_candidates[:limit]

    def get_recent_movies(self, limit: int = 6) -> List[Dict[str, Any]]:
        """Returns the most recently added or modified movies."""
        if not self.movies:
            self.scan_movies()
        sorted_movies = sorted(self.movies, key=lambda x: x.get("modified_time", 0), reverse=True)
        return sorted_movies[:limit]

    def get_top_movies(self, limit: int = 6) -> List[Dict[str, Any]]:
        """Returns the highest rated movies."""
        if not self.movies:
            self.scan_movies()

        def parse_rating(item: Dict[str, Any]) -> float:
            try:
                return float(item.get("rating", 0))
            except (ValueError, TypeError):
                return 0.0

        sorted_movies = sorted(self.movies, key=parse_rating, reverse=True)
        ranked = []
        for i, item in enumerate(sorted_movies[:limit], start=1):
            item_copy = dict(item)
            item_copy["rank"] = i
            ranked.append(item_copy)
        return ranked

    def get_recent_tv(self, limit: int = 6) -> List[Dict[str, Any]]:
        """Returns recently added TV shows."""
        if not self.tv_shows:
            self.scan_tv()
        return self.tv_shows[:limit]

    def get_top_tv(self, limit: int = 6) -> List[Dict[str, Any]]:
        """Returns highest rated TV shows."""
        if not self.tv_shows:
            self.scan_tv()

        def parse_rating(item: Dict[str, Any]) -> float:
            try:
                return float(item.get("rating", 0))
            except (ValueError, TypeError):
                return 0.0

        sorted_tv = sorted(self.tv_shows, key=parse_rating, reverse=True)
        ranked = []
        for i, item in enumerate(sorted_tv[:limit], start=1):
            item_copy = dict(item)
            item_copy["rank"] = i
            ranked.append(item_copy)
        return ranked

    def get_homepage_context(self) -> Dict[str, Any]:
        """
        Returns a complete dictionary ready to be passed to Flask's
        render_template('homepage.html', **context).
        """
        return {
            "continue_watching": self.get_continue_watching(3),
            "slider_items": self.get_slider_items(3),
            "recent_movies": self.get_recent_movies(6),
            "top_movies": self.get_top_movies(6),
            "recent_tv": self.get_recent_tv(6),
            "top_tv": self.get_top_tv(6),
        }
