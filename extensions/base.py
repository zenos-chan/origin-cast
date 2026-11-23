import ssl
import urllib.request
import urllib.parse
import urllib.error
from typing import Dict, List, Optional, Any


class BaseExtension:
    """
    Base class for site-specific scrapers and media extensions in OriginCast.
    All custom scrapers should inherit from BaseExtension.
    """
    id: str = "base"
    name: str = "Base Extension"
    description: str = "Base scraper interface"
    base_url: str = "https://example.com"
    version: str = "1.0.0"
    author: str = "OriginCast Team"
    icon: str = "puzzle"
    enabled: bool = True
    supported_types: List[str] = ["movie", "tv"]

    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def fetch_html(self, url: str, timeout: int = 10, headers: Optional[Dict[str, str]] = None) -> str:
        """
        Fetches raw HTML content from the given URL with custom User-Agent, headers,
        and unverified SSL context to ensure resilient connection across mirror domains.
        """
        req_headers = {
            "User-Agent": self.DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if headers:
            req_headers.update(headers)

        req = urllib.request.Request(url, headers=req_headers)
        ctx = ssl._create_unverified_context()
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=timeout) as response:
                content_bytes = response.read()
                try:
                    return content_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    return content_bytes.decode("latin-1", errors="ignore")
        except Exception as e:
            raise RuntimeError(f"Failed to fetch {url}: {e}")

    def get_latest(self, page: int = 1) -> List[Dict[str, Any]]:
        """
        Fetches the latest movies/shows available on the target website.
        Should return a list of dicts:
        [{
            "title": str,
            "url": str,
            "poster": str,
            "year": Optional[str],
            "quality": Optional[str],
            "rating": Optional[str],
            "extension": str
        }]
        """
        return []

    def search(self, query: str) -> List[Dict[str, Any]]:
        """
        Searches the target website for titles matching the query string.
        Should return a list of dicts:
        [{
            "title": str,
            "url": str,
            "poster": Optional[str],
            "year": Optional[str],
            "extra": Optional[dict]
        }]
        """
        raise NotImplementedError("Subclasses must implement search()")

    def scrape(self, url: str) -> Dict[str, Any]:
        """
        Scrapes a movie / post page and returns structured metadata,
        available qualities, and download links:
        {
            "id": str,
            "title": str,
            "poster": str,
            "year": str,
            "overview": str,
            "qualities": [
                {
                    "quality": "1080p",
                    "size": "2.5 GB",
                    "links": [
                        {"label": "Direct Link", "url": "https://...", "type": "Direct"}
                    ]
                }
            ],
            "source_url": url,
            "extension": str
        }
        """
        raise NotImplementedError("Subclasses must implement scrape()")

    def to_dict(self) -> Dict[str, Any]:
        """Returns a serializable dictionary representation of the extension metadata."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "base_url": self.base_url,
            "version": self.version,
            "author": self.author,
            "icon": self.icon,
            "enabled": self.enabled,
            "supported_types": self.supported_types
        }
