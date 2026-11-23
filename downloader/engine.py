import re
import time
import json
import uuid
import ssl
import base64
import threading
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Tuple
from concurrent.futures import ThreadPoolExecutor

try:
    import lxml.html  # noqa: F401
    HAS_LXML = True
except ImportError:
    HAS_LXML = False


def format_bytes(bytes_val: float) -> str:
    """Formats byte counts into human-readable B, KB, MB, GB, TB."""
    try:
        val = float(bytes_val or 0)
    except (ValueError, TypeError):
        return "0 B"
    if val <= 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if val < 1024.0:
            return f"{val:.1f} {unit}"
        val /= 1024.0
    return f"{val:.1f} PB"


def format_speed(bytes_per_sec: float) -> str:
    """Formats byte transfer rate per second."""
    if bytes_per_sec <= 0:
        return "0 KB/s"
    return f"{format_bytes(bytes_per_sec)}/s"


def format_eta(seconds: float) -> str:
    """Formats remaining seconds into mm:ss or hh:mm:ss."""
    try:
        s = int(seconds)
    except (ValueError, TypeError):
        return "Calculating..."
    if s <= 0:
        return "0s"
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


def parse_size_str_to_bytes(size_str: str) -> int:
    """Parses strings like '1.2 GB', '540MB', or '450 MB' to raw bytes."""
    if not size_str:
        return 0

    match = re.search(r'([\d\.]+)\s*(GB|MB|KB|B)\b', size_str, re.IGNORECASE)
    if not match:
        return 0

    val = float(match.group(1))
    unit = match.group(2).upper()
    if unit == "GB":
        return int(val * 1024 * 1024 * 1024)
    elif unit == "MB":
        return int(val * 1024 * 1024)
    elif unit == "KB":
        return int(val * 1024)
    return int(val)


def resolve_direct_download_url(url: str, timeout: int = 8) -> Tuple[str, int, str]:
    """
    Intelligently resolves indirect gateway/redirect links (such as HubCloud,
    HubCDN, HubDrive, and shortener landing pages) into direct binary stream URLs.
    Returns: (resolved_url, content_length, content_type)
    """
    clean_url = url.strip()
    ctx = ssl._create_unverified_context()
    user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    headers = {"User-Agent": user_agent, "Accept": "*/*"}

    # 1. If already recognized as a direct video storage endpoint (e.g. Cloudflare R2, direct file extension)
    lower_url = clean_url.lower()
    if any(lower_url.endswith(ext) for ext in [".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts"]) or "r2.dev" in lower_url:
        try:
            req = urllib.request.Request(clean_url, headers={"User-Agent": user_agent, "Range": "bytes=0-1"})
            with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
                cr = resp.headers.get("Content-Range", "")
                m = re.search(r'/(\d+)', cr)
                cl = int(m.group(1)) if m else int(resp.headers.get("Content-Length") or 0)
                ct = resp.headers.get("Content-Type", "application/octet-stream")
                return clean_url, cl, ct
        except Exception:
            return clean_url, 0, "application/octet-stream"

    # 2. Inspect landing page / redirect
    try:
        req = urllib.request.Request(clean_url, headers=headers)
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            final_url = resp.geturl()
            content_type = resp.headers.get("Content-Type", "")
            cl_header = resp.headers.get("Content-Length")
            cl = int(cl_header) if cl_header and cl_header.isdigit() else 0

            # If the response itself is already a binary stream
            if any(t in content_type.lower() for t in ["video/", "octet-stream", "x-matroska", "mp4"]):
                return final_url, cl, content_type

            # Parse HTML content for redirected storage links
            html = resp.read().decode("utf-8", errors="ignore")

            # A. Base64 encoded redirect parameter (HubCloud / HubCDN standard pattern)
            m_reurl = re.search(r'var\s+reurl\s*=\s*[\'"][^\'"]*\?r=([a-zA-Z0-9+/=]+)', html)
            if m_reurl:
                b64 = m_reurl.group(1)
                b64 += "=" * ((4 - len(b64) % 4) % 4)
                try:
                    decoded = base64.b64decode(b64).decode("utf-8", errors="ignore")
                    m_link = re.search(r'link=(https?://[^\s&"\']+)', decoded)
                    if m_link:
                        direct = m_link.group(1)
                        # Probe direct R2 URL
                        sub_req = urllib.request.Request(direct, headers={"User-Agent": user_agent, "Range": "bytes=0-1"})
                        with urllib.request.urlopen(sub_req, context=ctx, timeout=timeout) as sub_resp:
                            cr = sub_resp.headers.get("Content-Range", "")
                            m_sz = re.search(r'/(\d+)', cr)
                            direct_size = int(m_sz.group(1)) if m_sz else int(sub_resp.headers.get("Content-Length") or 0)
                            return direct, direct_size, sub_resp.headers.get("Content-Type", "application/octet-stream")
                except Exception as ex:
                    print(f"[!] Base64 link extraction error: {ex}")

            # B. Look for direct storage links (.r2.dev, .mkv, .mp4) in href attributes
            for href_match in re.finditer(r'href=[\'"](https?://[^\s\'"<>]+)[\'"]', html):
                found_link = href_match.group(1)
                if "r2.dev" in found_link or any(found_link.lower().endswith(ext) for ext in [".mp4", ".mkv", ".avi"]):
                    try:
                        sub_req = urllib.request.Request(found_link, headers={"User-Agent": user_agent, "Range": "bytes=0-1"})
                        with urllib.request.urlopen(sub_req, context=ctx, timeout=timeout) as sub_resp:
                            cr = sub_resp.headers.get("Content-Range", "")
                            m_sz = re.search(r'/(\d+)', cr)
                            direct_size = int(m_sz.group(1)) if m_sz else int(sub_resp.headers.get("Content-Length") or 0)
                            return found_link, direct_size, sub_resp.headers.get("Content-Type", "application/octet-stream")
                    except Exception:
                        return found_link, 0, "application/octet-stream"

            return final_url, cl, content_type

    except Exception as e:
        print(f"[!] Error resolving URL {clean_url}: {e}")
        return clean_url, 0, "unknown"


class DownloadTask:
    """
    Represents an individual download task with thread-safe progress,
    pause, resume, and cancellation mechanics.
    """
    def __init__(
        self,
        task_id: str,
        url: str,
        title: str,
        quality: str,
        media_type: str,
        target_dir: str,
        target_file: str,
        total_bytes: int = 0,
        poster: str = ""
    ):
        self.id = task_id
        self.url = url
        self.direct_url = url
        self.title = title
        self.quality = quality
        self.media_type = media_type
        self.target_dir = Path(target_dir).resolve()
        self.target_file = Path(target_file).resolve()
        self.temp_file = Path(str(target_file) + ".part").resolve()
        self.poster = poster

        self.total_bytes = total_bytes
        self.downloaded_bytes = 0
        self.status = "queued"  # queued, downloading, paused, completed, error, cancelled
        self.speed = 0.0
        self.eta = 0.0
        self.error_message = ""

        self.created_at = time.time()
        self.completed_at: Optional[float] = None

        self._pause_event = threading.Event()
        self._pause_event.set()
        self._cancel_event = threading.Event()
        self._lock = threading.Lock()

    def pause(self):
        """Pauses the download task."""
        with self._lock:
            if self.status == "downloading":
                self.status = "paused"
                self._pause_event.clear()
                self.speed = 0.0
                self.eta = 0.0

    def resume(self):
        """Resumes the download task."""
        with self._lock:
            if self.status == "paused":
                self.status = "downloading"
                self._pause_event.set()

    def cancel(self):
        """Cancels and deletes temporary partial files."""
        with self._lock:
            self.status = "cancelled"
            self._cancel_event.set()
            self._pause_event.set()
            self.speed = 0.0
            self.eta = 0.0

    @property
    def progress(self) -> float:
        """Returns live mathematically accurate progress percentage."""
        if self.status == "completed":
            return 100.0
        if self.total_bytes > 0:
            calc = (self.downloaded_bytes / self.total_bytes) * 100.0
            return min(100.0, max(0.0, round(calc, 1)))
        return 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Returns JSON-serializable representation of task with synchronized progress."""
        with self._lock:
            calc_prog = self.progress
            effective_total = self.total_bytes if self.total_bytes > 0 else self.downloaded_bytes

            return {
                "id": self.id,
                "title": self.title,
                "quality": self.quality,
                "media_type": self.media_type,
                "url": self.url,
                "direct_url": self.direct_url,
                "poster": self.poster,
                "status": self.status,
                "target_dir": str(self.target_dir),
                "target_file": str(self.target_file),
                "filename": self.target_file.name,
                "total_bytes": self.total_bytes,
                "downloaded_bytes": self.downloaded_bytes,
                "total_size_formatted": format_bytes(effective_total),
                "downloaded_size_formatted": format_bytes(self.downloaded_bytes),
                "progress": calc_prog,
                "speed": self.speed if self.status == "downloading" else 0.0,
                "speed_formatted": format_speed(self.speed) if self.status == "downloading" else "0 KB/s",
                "eta": self.eta if self.status == "downloading" else 0.0,
                "eta_formatted": format_eta(self.eta) if self.status == "downloading" else "0s",
                "error_message": self.error_message,
                "created_at": self.created_at,
                "completed_at": self.completed_at
            }


class DownloadManager:
    """
    Central background downloader manager. Orchestrates multi-threaded segmented
    HTTP streams, directory structures, queueing, pause/resume, and history persistence.
    """
    def __init__(
        self,
        movies_dir: str = "./movies",
        tv_dir: str = "./tv",
        history_file: str = "downloader/downloads.json",
        max_workers: int = 3,
        on_complete_callback: Optional[Callable] = None
    ):
        self.movies_dir = Path(movies_dir).resolve()
        self.tv_dir = Path(tv_dir).resolve()
        self.history_file = Path(history_file).resolve()
        self.on_complete_callback = on_complete_callback

        self.tasks: Dict[str, DownloadTask] = {}
        self.completed_history: List[Dict[str, Any]] = []
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="origin_downloader")
        self._lock = threading.Lock()

        self._load_history()

    def _load_history(self):
        """Loads completed downloads history."""
        if self.history_file.exists():
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.completed_history = data.get("completed", [])
            except Exception:
                self.completed_history = []
        else:
            self.completed_history = []

    def _save_history(self):
        """Persists completed downloads history."""
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump({"completed": self.completed_history}, f, indent=2)
        except Exception as e:
            print(f"[!] Warning: Could not save downloads history: {e}")

    def add_download(
        self,
        url: str,
        title: str,
        quality: str = "1080p",
        media_type: str = "movie",
        size_hint: str = "",
        poster: str = ""
    ) -> DownloadTask:
        """
        Creates and starts a new download task.
        Constructs the target folder and filename to maintain the proper media structure:
        - Movies: <movies_dir>/<Title> (<Year>)/<Title> (<Year>) [<Quality>].mp4
        - TV:     <tv_dir>/<Title> (<Year>)/Season 01/<Title> - S01E01 [<Quality>].mp4
        """
        clean_title = self._sanitize_filename(title)
        clean_quality = quality.upper().replace(" ", "")

        # Target structure paths
        if media_type == "tv":
            target_folder = self.tv_dir / clean_title / "Season 01"
            filename = f"{clean_title} - S01E01 [{clean_quality}].mp4"
        else:
            target_folder = self.movies_dir / clean_title
            filename = f"{clean_title} [{clean_quality}].mp4"

        target_folder.mkdir(parents=True, exist_ok=True)
        target_file = target_folder / filename

        task_id = f"dl_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        total_bytes = parse_size_str_to_bytes(size_hint)

        task = DownloadTask(
            task_id=task_id,
            url=url,
            title=clean_title,
            quality=clean_quality,
            media_type=media_type,
            target_dir=str(target_folder),
            target_file=str(target_file),
            total_bytes=total_bytes,
            poster=poster
        )

        with self._lock:
            self.tasks[task_id] = task

        # Dispatch background download worker
        self.executor.submit(self._run_download_worker, task)
        return task

    def pause_task(self, task_id: str) -> bool:
        """Pauses a running download."""
        task = self.tasks.get(task_id)
        if task:
            task.pause()
            return True
        return False

    def resume_task(self, task_id: str) -> bool:
        """Resumes a paused download."""
        task = self.tasks.get(task_id)
        if task:
            task.resume()
            return True
        return False

    def retry_task(self, task_id: str) -> bool:
        """Retries a failed download task."""
        task = self.tasks.get(task_id)
        if task and task.status in ("error", "paused"):
            task.status = "queued"
            task.error_message = ""
            task._cancel_event.clear()
            task._pause_event.set()
            self.executor.submit(self._run_download_worker, task)
            return True
        return False

    def delete_task(self, task_id: str) -> bool:
        """Cancels a download and removes partial or completed files."""
        task = self.tasks.get(task_id)
        if task:
            task.cancel()
            if task.temp_file.exists():
                try:
                    task.temp_file.unlink()
                except Exception:
                    pass
            with self._lock:
                if task_id in self.tasks:
                    del self.tasks[task_id]
            return True

        # Check if in completed history
        with self._lock:
            for item in list(self.completed_history):
                if item.get("id") == task_id:
                    file_path = Path(item.get("target_file", ""))
                    if file_path.exists():
                        try:
                            file_path.unlink()
                        except Exception:
                            pass
                    self.completed_history.remove(item)
                    self._save_history()
                    return True
        return False

    def get_all_tasks(self) -> Dict[str, Any]:
        """Returns active downloads and completed history."""
        active = []
        with self._lock:
            for task in self.tasks.values():
                active.append(task.to_dict())

        return {
            "active": active,
            "completed": self.completed_history
        }

    def _sanitize_filename(self, name: str) -> str:
        """
        Strips illegal filesystem characters, removes private use icons/glyphs/emojis,
        and standardizes format to 'Movie Name (Year)'.
        """
        if not name:
            return "Media Download"

        # 1. Strip Private Use Area unicode (\ue000-\uf8ff), emojis, font glyphs, zero-width chars
        cleaned = re.sub(r'[\ue000-\uf8ff\U000f0000-\U0010ffff\u200b-\u200f\ufeff]', '', name)
        cleaned = re.sub(r'[\u2600-\u27bf\U0001f300-\U0001f9ff]', '', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()

        # 2. Strip leading 'Download' prefix
        cleaned = re.sub(r'^Download\s+', '', cleaned, flags=re.IGNORECASE)

        # 3. Strip quality, rip, and audio tags
        cleaned = re.sub(
            r'[\(\[]?\s*(?:480p|720p|1080p|2160p|4k|hevc|hdrip|bluray|web-dl|hdtc|v2|hq|uncut|multi|dual\s*audio|hindi|english|tamil|telugu|clean|line|full\s*movie).*$',
            '',
            cleaned,
            flags=re.IGNORECASE
        )
        cleaned = cleaned.strip(' -[]|()')

        # 4. Extract year and format strictly as 'Movie Name (Year)'
        m_year = re.search(r'\b(19\d\d|20\d\d)\b', cleaned)
        if m_year:
            year = m_year.group(1)
            name_part = re.sub(r'\s*[\(\[]?\s*' + year + r'\s*[\)\]]?', '', cleaned).strip(' -[]|()')
            cleaned = f"{name_part} ({year})"

        # 5. Clean colons into ' - ' to prevent illegal filesystem colons
        cleaned = re.sub(r'\s*:\s*', ' - ', cleaned)

        # 6. Remove illegal filesystem characters \ / * ? " < > |
        cleaned = re.sub(r'[\\/*?\"<>|]', '', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip(' .-_')

        return cleaned or "Media Download"

    # -------------------------------------------------------------------------
    # Worker Execution Pipeline
    # -------------------------------------------------------------------------
    def _run_download_worker(self, task: DownloadTask):
        """
        Executes download. Resolves indirect links into direct binary streams,
        uses SSL unverified context, and handles pause/resume and streaming.
        """
        task.status = "resolving"
        task.error_message = ""

        # Step 1: Resolve link
        direct_url, content_len, content_type = resolve_direct_download_url(task.url)
        task.direct_url = direct_url

        if content_len > 100 * 1024:
            task.total_bytes = content_len

        # Check if resolved content is an unresolvable HTML page (e.g. ad/captcha gate)
        is_html = "text/html" in content_type.lower() and content_len < 500 * 1024
        if is_html and not any(k in direct_url.lower() for k in ["r2.dev", ".mp4", ".mkv"]):
            task.status = "error"
            task.error_message = (
                "Link requires browser verification (HTML locker). "
                "Open the link in your browser to get the direct stream, or paste direct URL."
            )
            return

        # Step 2: Stream the file
        task.status = "downloading"
        self._stream_real_file(task)

        # Step 3: Handle completion
        if task.status == "completed":
            task.completed_at = time.time()
            task.speed = 0.0
            task.eta = 0.0
            record = task.to_dict()
            with self._lock:
                self.completed_history.insert(0, record)
                if task.id in self.tasks:
                    del self.tasks[task.id]
                self._save_history()

            if self.on_complete_callback:
                try:
                    self.on_complete_callback()
                except Exception as e:
                    print(f"[!] Downloader on_complete_callback error: {e}")

    def _stream_real_file(self, task: DownloadTask):
        """Multi-chunk buffered streaming from resolved direct URL."""
        chunk_size = 256 * 1024  # 256 KB buffer
        ctx = ssl._create_unverified_context()
        user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

        # Check existing partial bytes for resuming
        existing_bytes = 0
        if task.temp_file.exists():
            existing_bytes = task.temp_file.stat().st_size
            task.downloaded_bytes = existing_bytes

        req_headers = {
            "User-Agent": user_agent,
            "Accept": "*/*"
        }
        if existing_bytes > 0:
            req_headers["Range"] = f"bytes={existing_bytes}-"

        try:
            req = urllib.request.Request(task.direct_url, headers=req_headers)
            with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
                resp_code = getattr(resp, "status", 200)

                # If server doesn't support range and sends 200, restart from 0
                if existing_bytes > 0 and resp_code == 200:
                    mode = "wb"
                    task.downloaded_bytes = 0
                else:
                    mode = "ab" if existing_bytes > 0 else "wb"

                # Check Content-Range or Content-Length to refine total bytes
                cr = resp.headers.get("Content-Range", "")
                m_cr = re.search(r'/(\d+)', cr)
                if m_cr:
                    task.total_bytes = int(m_cr.group(1))
                elif resp.headers.get("Content-Length") and int(resp.headers.get("Content-Length")) > 100 * 1024:
                    cl_val = int(resp.headers.get("Content-Length"))
                    task.total_bytes = task.downloaded_bytes + cl_val

                # Open temp file and stream
                last_calc_time = time.time()
                bytes_at_last_calc = task.downloaded_bytes

                with open(task.temp_file, mode) as f:
                    while True:
                        if task._cancel_event.is_set():
                            task.status = "cancelled"
                            if task.temp_file.exists():
                                try:
                                    task.temp_file.unlink()
                                except Exception:
                                    pass
                            return

                        # Reactive pause handling
                        if not task._pause_event.is_set():
                            task.speed = 0.0
                            task.eta = 0.0
                            time.sleep(0.3)
                            last_calc_time = time.time()
                            bytes_at_last_calc = task.downloaded_bytes
                            continue

                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break

                        f.write(chunk)
                        task.downloaded_bytes += len(chunk)

                        # Update metrics every 0.3s
                        now = time.time()
                        dt = now - last_calc_time
                        if dt >= 0.3:
                            delta_bytes = task.downloaded_bytes - bytes_at_last_calc
                            instant_speed = delta_bytes / dt
                            # Smooth exponential moving average speed
                            task.speed = instant_speed if task.speed <= 0 else (task.speed * 0.6 + instant_speed * 0.4)
                            bytes_at_last_calc = task.downloaded_bytes
                            last_calc_time = now

                            if task.total_bytes > 0 and task.speed > 0:
                                rem_bytes = max(0, task.total_bytes - task.downloaded_bytes)
                                task.eta = rem_bytes / task.speed
                            else:
                                task.eta = 0.0

            # Download finished: atomic rename to final media file
            if task.temp_file.exists():
                task.temp_file.rename(task.target_file)

            task.status = "completed"
            task.speed = 0.0
            task.eta = 0.0

        except Exception as e:
            if not task._cancel_event.is_set():
                task.status = "error"
                task.error_message = str(e)
                task.speed = 0.0
                task.eta = 0.0
