#!/usr/bin/env python3
"""
OriginCast Metadata Cron Job / Watcher.
Scans the `movies/` and `tv/` directories.
Whenever a folder without a `metadata.json` is detected (e.g., a newly added movie or show),
it fetches metadata using IMDb / TVMaze and writes `metadata.json` directly into that folder.
"""

import time
import json
import argparse
from pathlib import Path
from metadata_fetcher import fetch_media_metadata


def process_directory(base_dir: Path, media_type: str, force: bool = False) -> int:
    """
    Checks all folders inside base_dir.
    If metadata.json does not exist (or force=True), creates it.
    Returns count of updated folders.
    """
    if not base_dir.exists():
        return 0

    count = 0
    for folder in sorted(base_dir.iterdir()):
        if not folder.is_dir() or folder.name.startswith('.'):
            continue

        meta_path = folder / "metadata.json"
        if not meta_path.exists() or force:
            print(f"[*] Fetching metadata for [{media_type.upper()}]: {folder.name}...")
            meta_data = fetch_media_metadata(folder.name, media_type=media_type)
            try:
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(meta_data, f, indent=2)
                print(f"    [+] Saved -> {meta_path} ({meta_data.get('source')})")
                count += 1
            except Exception as e:
                print(f"    [-] Failed to save {meta_path}: {e}")
        else:
            # Metadata already exists
            pass

    return count


def run_sync(movies_dir: str = "movies", tv_dir: str = "tv", force: bool = False) -> None:
    """Runs a single synchronization pass across movies and TV folders."""
    m_path = Path(movies_dir)
    t_path = Path(tv_dir)

    print(f"\n--- [Cron Job] OriginCast Metadata Sync: {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
    m_count = process_directory(m_path, "movie", force=force)
    t_count = process_directory(t_path, "tv", force=force)
    print(f"--- Sync Complete: {m_count} movies updated, {t_count} TV shows updated ---\n")


def main():
    parser = argparse.ArgumentParser(description="OriginCast Metadata Cron / Watcher")
    parser.add_argument("--movies", default="movies", help="Path to movies folder")
    parser.add_argument("--tv", default="tv", help="Path to tv folder")
    parser.add_argument("--force", action="store_true", help="Force refresh metadata for all folders")
    parser.add_argument("--watch", action="store_true", help="Run continuously in background/loop mode")
    parser.add_argument("--interval", type=int, default=30, help="Check interval in seconds for --watch")

    args = parser.parse_args()

    if args.watch:
        print(f"[!] Starting OriginCast Metadata Watcher daemon (polling every {args.interval}s)...")
        try:
            while True:
                run_sync(args.movies, args.tv, force=False)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nWatcher stopped by user.")
    else:
        run_sync(args.movies, args.tv, force=args.force)


if __name__ == "__main__":
    main()
