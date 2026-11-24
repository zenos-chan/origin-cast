import http.cookiejar
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import lxml.html
    HAS_LXML = True
except ImportError:
    HAS_LXML = False

GDRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

FOLDER_ID_PATTERNS = [
    re.compile(r"/folders/([a-zA-Z0-9_-]{10,})"),
    re.compile(r"[?&]id=([a-zA-Z0-9_-]{10,})"),
    re.compile(r"^([a-zA-Z0-9_-]{10,})$"),
]

MIME_TO_EXT = {
    "video/mp4": ".mp4",
    "video/x-matroska": ".mkv",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "video/x-msvideo": ".avi",
    "video/x-m4v": ".m4v",
}

EXT_TO_MIME = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mkv": "video/webm",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
}


def extract_gdrive_folder_id(url_or_id: str) -> str:
    """Extracts a Google Drive folder ID from a share URL or returns a clean ID."""
    raw = (url_or_id or "").strip()
    if not raw:
        return ""
    for pattern in FOLDER_ID_PATTERNS:
        match = pattern.search(raw)
        if match:
            return match.group(1)
    return raw


def guess_video_mimetype(filename: str, fallback: str = "video/mp4") -> str:
    """Infers browser-compatible video MIME type from filename extension."""
    ext = Path(filename or "").suffix.lower()
    return EXT_TO_MIME.get(ext, fallback)


class GoogleDriveClient:
    """
    Lightweight Google Drive client for listing shared/API-accessible folders
    and streaming media files directly via HTTP Range requests without downloading to disk.
    """

    def __init__(
        self,
        api_key: str = "",
        timeout: int = 12,
        stream_cache_ttl: int = 1200,
    ):
        self.api_key = (api_key or "").strip()
        self.timeout = timeout
        self.stream_cache_ttl = stream_cache_ttl
        self._ssl_ctx = ssl._create_unverified_context()
        self._stream_cache: Dict[str, Dict[str, Any]] = {}

    def _build_opener(self) -> Tuple[urllib.request.OpenerDirector, http.cookiejar.CookieJar]:
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self._ssl_ctx),
            urllib.request.HTTPCookieProcessor(jar),
        )
        return opener, jar

    def _request(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
    ):
        req_headers = {"User-Agent": DEFAULT_USER_AGENT}
        if headers:
            req_headers.update(headers)
        req = urllib.request.Request(url, headers=req_headers)
        return urllib.request.urlopen(req, context=self._ssl_ctx, timeout=timeout or self.timeout)

    # -------------------------------------------------------------------------
    # Folder Listing (API v3 or Public Shared Folder Fallback)
    # -------------------------------------------------------------------------
    def list_folder(self, folder_url_or_id: str) -> List[Dict[str, Any]]:
        """
        Lists files and subfolders in a Google Drive folder.
        Uses Drive API v3 when an API key is configured, otherwise parses the
        public shared folder view.
        """
        folder_id = extract_gdrive_folder_id(folder_url_or_id)
        if not folder_id:
            return []

        if self.api_key:
            items = self._list_folder_api(folder_id)
            if items is not None:
                return items

        return self._list_folder_public(folder_id)

    def _list_folder_api(self, folder_id: str) -> Optional[List[Dict[str, Any]]]:
        """Lists folder contents via Google Drive v3 API using an API key."""
        items: List[Dict[str, Any]] = []
        page_token: Optional[str] = None

        try:
            while True:
                params = {
                    "q": f"'{folder_id}' in parents and trashed = false",
                    "fields": "nextPageToken,files(id,name,mimeType,size,modifiedTime)",
                    "pageSize": "1000",
                    "supportsAllDrives": "true",
                    "includeItemsFromAllDrives": "true",
                    "key": self.api_key,
                }
                if page_token:
                    params["pageToken"] = page_token

                url = "https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode(params)
                with self._request(url) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))

                for f in payload.get("files", []):
                    mime = f.get("mimeType", "")
                    is_folder = mime == GDRIVE_FOLDER_MIME
                    name = f.get("name", "")
                    if not is_folder and not Path(name).suffix and mime in MIME_TO_EXT:
                        name = f"{name}{MIME_TO_EXT[mime]}"

                    size_raw = f.get("size")
                    items.append({
                        "id": f.get("id", ""),
                        "name": name,
                        "mime_type": mime,
                        "is_folder": is_folder,
                        "size": int(size_raw) if size_raw and str(size_raw).isdigit() else 0,
                        "modified_time": f.get("modifiedTime", ""),
                    })

                page_token = payload.get("nextPageToken")
                if not page_token:
                    break
            return items
        except Exception as exc:
            print(f"[!] Google Drive API list_folder error for {folder_id}: {exc}")
            return None

    def _list_folder_public(self, folder_id: str) -> List[Dict[str, Any]]:
        """Lists contents of a publicly shared Google Drive folder without an API key."""
        url = f"https://drive.google.com/embeddedfolderview?id={urllib.parse.quote(folder_id)}#list"
        try:
            with self._request(url, headers={"Accept": "text/html,application/xhtml+xml"}) as resp:
                html = resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            raise RuntimeError(f"Could not access public Google Drive folder '{folder_id}': {exc}") from exc

        return self._parse_embedded_folder_html(html)

    def _parse_embedded_folder_html(self, html: str) -> List[Dict[str, Any]]:
        """Parses items from Google Drive's embeddedfolderview HTML response."""
        items: List[Dict[str, Any]] = []
        seen_ids = set()

        if HAS_LXML and html:
            try:
                tree = lxml.html.fromstring(html)
                entries = tree.xpath('//div[contains(@class, "flip-entry")]')
                for entry in entries:
                    entry_id = (entry.get("id") or "").strip()
                    item_id = entry_id[6:] if entry_id.startswith("entry-") else ""
                    links = entry.xpath(".//a[@href]")
                    href = links[0].get("href", "") if links else ""

                    if not item_id and href:
                        m_id = re.search(r"/(?:folders|d)/([a-zA-Z0-9_-]{10,})", href)
                        if m_id:
                            item_id = m_id.group(1)

                    if not item_id or item_id in seen_ids:
                        continue

                    title_nodes = entry.xpath('.//*[contains(@class, "flip-entry-title")]//text()')
                    name = "".join(title_nodes).strip()
                    if not name and links:
                        name = "".join(links[0].xpath(".//text()")).strip()
                    if not name:
                        continue

                    is_folder = "/folders/" in href or "embeddedfolderview" in href
                    if not is_folder:
                        icon_imgs = entry.xpath(".//img[@src]")
                        for img in icon_imgs:
                            if "folder" in (img.get("src") or "").lower():
                                is_folder = True
                                break

                    seen_ids.add(item_id)
                    items.append({
                        "id": item_id,
                        "name": name,
                        "mime_type": GDRIVE_FOLDER_MIME if is_folder else "",
                        "is_folder": is_folder,
                        "size": 0,
                        "modified_time": "",
                    })
                if items:
                    return items
            except Exception as exc:
                print(f"[!] Google Drive embedded HTML parse error: {exc}")

        # Regex fallback if lxml is absent or markup differs
        link_pattern = re.compile(
            r'href=["\']https://drive\.google\.com/(drive/folders|file/d)/([a-zA-Z0-9_-]{10,})[^"\']*["\'][^>]*>(.*?)</a>',
            re.IGNORECASE | re.DOTALL,
        )
        for kind, item_id, inner in link_pattern.findall(html or ""):
            if item_id in seen_ids:
                continue
            clean_name = re.sub(r"<[^>]+>", "", inner).strip()
            if not clean_name:
                continue
            is_folder = "folders" in kind.lower()
            seen_ids.add(item_id)
            items.append({
                "id": item_id,
                "name": clean_name,
                "mime_type": GDRIVE_FOLDER_MIME if is_folder else "",
                "is_folder": is_folder,
                "size": 0,
                "modified_time": "",
            })
        return items

    # -------------------------------------------------------------------------
    # Zero-Download Stream Resolution & HTTP Range Opening
    # -------------------------------------------------------------------------
    def _cookie_header_from_jar(self, jar: http.cookiejar.CookieJar) -> str:
        return "; ".join(f"{c.name}={c.value}" for c in jar)

    def _probe_stream_url(
        self,
        opener: urllib.request.OpenerDirector,
        jar: http.cookiejar.CookieJar,
        url: str,
    ) -> Tuple[Optional[str], int, str, str, str]:
        """
        Probes a candidate Drive URL with Range: bytes=0-0.
        Returns (direct_url_or_None, total_size, content_type, cookie_header, html_if_any).
        """
        req = urllib.request.Request(
            url,
            headers={"User-Agent": DEFAULT_USER_AGENT, "Range": "bytes=0-0"},
        )
        with opener.open(req, timeout=self.timeout) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            final_url = resp.geturl()
            cookie_hdr = self._cookie_header_from_jar(jar)

            if "text/html" in ctype:
                html = resp.read().decode("utf-8", errors="replace")
                return None, 0, ctype, cookie_hdr, html

            total_size = self._extract_total_size(resp.headers)
            return final_url, total_size, ctype, cookie_hdr, ""

    @staticmethod
    def _extract_total_size(headers: Any) -> int:
        content_range = headers.get("Content-Range", "")
        match = re.search(r"/(\d+)", content_range)
        if match:
            return int(match.group(1))
        content_length = headers.get("Content-Length", "")
        if content_length and str(content_length).isdigit():
            return int(content_length)
        return 0

    @staticmethod
    def _extract_confirm_url_from_html(html: str, file_id: str) -> Optional[str]:
        """Extracts Google Drive virus-scan bypass download URL from confirmation HTML."""
        if not html:
            return None

        if HAS_LXML:
            try:
                tree = lxml.html.fromstring(html)
                forms = tree.xpath('//form[@id="download-form"]') or tree.xpath("//form[@action]")
                if forms:
                    action = forms[0].get("action") or "https://drive.usercontent.google.com/download"
                    params: Dict[str, str] = {}
                    for inp in forms[0].xpath(".//input[@name]"):
                        name = inp.get("name")
                        val = inp.get("value", "")
                        if name:
                            params[name] = val
                    if "id" not in params:
                        params["id"] = file_id
                    if "export" not in params:
                        params["export"] = "download"
                    if "confirm" not in params:
                        params["confirm"] = "t"
                    sep = "&" if "?" in action else "?"
                    return f"{action}{sep}{urllib.parse.urlencode(params)}"
            except Exception:
                pass

        uuid_match = re.search(r'name="uuid"\s+value="([^"]+)"', html)
        confirm_match = re.search(r'(?:name="confirm"\s+value="|confirm=)([^"&]+)', html)
        params = {
            "id": file_id,
            "export": "download",
            "confirm": confirm_match.group(1) if confirm_match else "t",
        }
        if uuid_match:
            params["uuid"] = uuid_match.group(1)
        return "https://drive.usercontent.google.com/download?" + urllib.parse.urlencode(params)

    def resolve_stream_target(self, file_id: str, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Resolves a Google Drive file ID to a direct range-capable HTTP stream URL
        without downloading the media file. Caches the resolved URL briefly.
        """
        now = time.time()
        cached = self._stream_cache.get(file_id)
        if not force_refresh and cached and cached.get("expires_at", 0) > now:
            return cached

        if self.api_key:
            api_url = (
                f"https://www.googleapis.com/drive/v3/files/{urllib.parse.quote(file_id)}"
                f"?alt=media&supportsAllDrives=true&key={urllib.parse.quote(self.api_key)}"
            )
            opener, jar = self._build_opener()
            try:
                direct_url, total_size, ctype, cookie_hdr, _ = self._probe_stream_url(opener, jar, api_url)
                if direct_url:
                    entry = {
                        "url": direct_url,
                        "total_size": total_size,
                        "content_type": ctype,
                        "cookie": cookie_hdr,
                        "expires_at": now + self.stream_cache_ttl,
                    }
                    self._stream_cache[file_id] = entry
                    return entry
            except Exception as exc:
                print(f"[!] Drive API stream probe failed for {file_id}, trying public fallback: {exc}")

        opener, jar = self._build_opener()
        initial_url = f"https://drive.usercontent.google.com/download?id={urllib.parse.quote(file_id)}&export=download&confirm=t"
        direct_url, total_size, ctype, cookie_hdr, html = self._probe_stream_url(opener, jar, initial_url)

        if not direct_url and html:
            confirmed_url = self._extract_confirm_url_from_html(html, file_id)
            if confirmed_url:
                direct_url, total_size, ctype, cookie_hdr, _ = self._probe_stream_url(opener, jar, confirmed_url)

        if not direct_url:
            raise RuntimeError(f"Google Drive file '{file_id}' is not publicly accessible or requires authentication.")

        entry = {
            "url": direct_url,
            "total_size": total_size,
            "content_type": ctype,
            "cookie": cookie_hdr,
            "expires_at": now + self.stream_cache_ttl,
        }
        self._stream_cache[file_id] = entry
        return entry

    def open_range_stream(
        self,
        file_id: str,
        start: Optional[int] = None,
        end: Optional[int] = None,
    ):
        """
        Opens an upstream HTTP stream for a Google Drive file, forwarding optional
        byte-range headers directly without saving anything to disk.
        """
        target = self.resolve_stream_target(file_id)

        for attempt in range(2):
            headers = {"User-Agent": DEFAULT_USER_AGENT}
            if target.get("cookie"):
                headers["Cookie"] = target["cookie"]
            if start is not None:
                range_spec = f"bytes={start}-{end}" if end is not None else f"bytes={start}-"
                headers["Range"] = range_spec

            req = urllib.request.Request(target["url"], headers=headers)
            try:
                resp = urllib.request.urlopen(req, context=self._ssl_ctx, timeout=max(self.timeout, 20))
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if "text/html" in ctype and attempt == 0:
                    resp.close()
                    target = self.resolve_stream_target(file_id, force_refresh=True)
                    continue
                return resp, target
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403, 410) and attempt == 0:
                    target = self.resolve_stream_target(file_id, force_refresh=True)
                    continue
                raise

        raise RuntimeError(f"Could not open Google Drive stream for '{file_id}'.")

    def fetch_bytes(self, file_id: str, max_bytes: int = 5 * 1024 * 1024) -> bytes:
        """Fetches small non-video files (e.g., subtitles or metadata.json) into memory."""
        resp, _ = self.open_range_stream(file_id)
        try:
            return resp.read(max_bytes)
        finally:
            resp.close()
