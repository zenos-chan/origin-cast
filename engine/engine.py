import re
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Any
import urllib.request
import urllib.parse
import urllib.error

# Supported extensions
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v'}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}

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
        omdb_api_key: Optional[str] = None
    ):
        self.movies_dir = Path(movies_dir)
        self.tv_dir = Path(tv_dir)
        self.cache_file = Path(cache_file)
        self.auto_fetch_online = auto_fetch_online
        self.omdb_api_key = omdb_api_key

        self.cache: Dict[str, Any] = {
            "movies": {},
            "tv": {}
        }
        self.movies: List[Dict[str, Any]] = []
        self.tv_shows: List[Dict[str, Any]] = []

        self.load_cache()

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

        # Migrate movie cache keys to clean slugs
        norm_movies = {}
        for k, m in self.cache.get("movies", {}).items():
            year = m.get("year") or extract_year(m.get("title", "")) or ""
            title = m.get("title") or k
            slug = f"{slugify(title)}-{year}" if year else slugify(title)
            slug = slug.strip('-')
            m["slug"] = slug
            m["id"] = slug
            norm_movies[slug] = m
        self.cache["movies"] = norm_movies

        # Migrate TV cache keys to clean slugs (no 'tv_' prefix, no spaces)
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
            norm_tv[slug] = t
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
    # Movie Scanner
    # -------------------------------------------------------------------------
    def scan_movies(self) -> List[Dict[str, Any]]:
        """
        Scans the `movies/` directory for movie folders or individual video files.
        Returns a list of parsed movie metadata dictionaries.
        """
        if not self.movies_dir.exists():
            return []

        movies = []
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

            # Check cache
            if cache_key in self.cache.get("movies", {}):
                meta = self.cache["movies"][cache_key]
                meta["file_path"] = str(movie_path)
                meta["slug"] = slug
                meta["id"] = slug
                movies.append(meta)
                continue

            # Curated visual defaults if matched
            curated = CURATED_ART.get(parsed_title.lower(), {})

            # Inspect local artwork & json
            local_art = self.find_local_art(parent_folder) if parent_folder else {"poster": None, "backdrop": None}
            local_meta = self.find_local_nfo_or_json(parent_folder) if parent_folder else None

            # Attempt online metadata
            online_meta = self.fetch_omdb_movie(parsed_title, year) if self.omdb_api_key else None

            poster_img = (
                local_art["poster"]
                or (local_meta and local_meta.get("poster"))
                or (online_meta and online_meta.get("poster"))
                or curated.get("poster")
                or "https://images.unsplash.com/photo-1536440136628-849c177e76a1?auto=format&fit=crop&w=600&q=80"
            )
            backdrop_img = (
                local_art["backdrop"]
                or (local_meta and local_meta.get("backdrop"))
                or (online_meta and online_meta.get("backdrop"))
                or curated.get("backdrop")
                or poster_img
            )

            movie_record = {
                "id": slug,
                "slug": slug,
                "title": (online_meta and online_meta.get("title")) or (local_meta and local_meta.get("title")) or parsed_title,
                "year": (online_meta and online_meta.get("year")) or (local_meta and local_meta.get("year")) or year or "2024",
                "rating": (online_meta and online_meta.get("rating")) or (local_meta and local_meta.get("rating")) or curated.get("rating") or "8.0",
                "duration": (online_meta and online_meta.get("duration")) or "2h 00m",
                "genres": (online_meta and online_meta.get("genres")) or (local_meta and local_meta.get("genres")) or curated.get("genres") or ["Action", "Sci-Fi"],
                "overview": (online_meta and online_meta.get("overview")) or (local_meta and local_meta.get("overview")) or curated.get("overview") or f"A feature film: {parsed_title}.",
                "poster": poster_img,
                "backdrop": backdrop_img,
                "img": poster_img,
                "file_path": str(movie_path),
                "folder_path": str(parent_folder) if parent_folder else "",
                "file_size": movie_path.stat().st_size if movie_path.exists() else 0,
                "modified_time": movie_path.stat().st_mtime if movie_path.exists() else time.time(),
                "type": "movie"
            }

            self.cache["movies"][cache_key] = movie_record
            movies.append(movie_record)

        self.save_cache()
        self.movies = movies
        return movies

    # -------------------------------------------------------------------------
    # TV Shows Scanner
    # -------------------------------------------------------------------------
    def scan_tv(self) -> List[Dict[str, Any]]:
        """
        Scans the `tv/` directory for series folders, seasons, and episodes.
        Returns a list of parsed TV show metadata dictionaries.
        """
        if not self.tv_dir.exists():
            return []

        shows = []
        for show_dir in sorted(self.tv_dir.iterdir()):
            if not show_dir.is_dir() or show_dir.name.startswith('.'):
                continue

            raw_show_name = show_dir.name
            clean_show_title = clean_title(raw_show_name)
            year = extract_year(raw_show_name)
            slug = f"{slugify(clean_show_title)}-{year}" if year else slugify(clean_show_title)
            slug = slug.strip('-')
            cache_key = slug

            # Check cache
            if cache_key in self.cache.get("tv", {}):
                meta = self.cache["tv"][cache_key]
                meta["slug"] = slug
                meta["id"] = slug
                shows.append(meta)
                continue

            # Discover seasons and episodes
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
                    })

            curated = CURATED_ART.get(clean_show_title.lower(), {})
            local_art = self.find_local_art(show_dir)
            local_meta = self.find_local_nfo_or_json(show_dir)
            online_meta = self.fetch_tvmaze_show(clean_show_title) if self.auto_fetch_online else None

            poster_img = (
                local_art["poster"]
                or (local_meta and local_meta.get("poster"))
                or (online_meta and online_meta.get("poster"))
                or curated.get("poster")
                or "https://images.unsplash.com/photo-1526374965328-7f61d4dc18c5?auto=format&fit=crop&w=600&q=80"
            )
            backdrop_img = (
                local_art["backdrop"]
                or (local_meta and local_meta.get("backdrop"))
                or (online_meta and online_meta.get("backdrop"))
                or curated.get("backdrop")
                or poster_img
            )

            show_record = {
                "id": slug,
                "slug": slug,
                "title": (online_meta and online_meta.get("title")) or (local_meta and local_meta.get("title")) or clean_show_title,
                "year": (online_meta and online_meta.get("year")) or (local_meta and local_meta.get("year")) or year or "2024",
                "rating": (online_meta and online_meta.get("rating")) or (local_meta and local_meta.get("rating")) or curated.get("rating") or "8.5",
                "overview": (online_meta and online_meta.get("overview")) or (local_meta and local_meta.get("overview")) or curated.get("overview") or f"TV Series: {clean_show_title}.",
                "genres": (online_meta and online_meta.get("genres")) or (local_meta and local_meta.get("genres")) or curated.get("genres") or ["Drama", "Mystery"],
                "poster": poster_img,
                "backdrop": backdrop_img,
                "img": poster_img,
                "folder_path": str(show_dir),
                "season": f"Season {len(seasons) or 1}",
                "seasons_count": len(seasons) or 1,
                "seasons": seasons,
                "total_episodes": sum(len(eps) for eps in seasons.values()),
                "type": "tv"
            }

            self.cache["tv"][cache_key] = show_record
            shows.append(show_record)

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
            "slider_items": self.get_slider_items(3),
            "recent_movies": self.get_recent_movies(6),
            "top_movies": self.get_top_movies(6),
            "recent_tv": self.get_recent_tv(6),
            "top_tv": self.get_top_tv(6),
        }
