import os
import queue
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

import requests
import yt_dlp

import db
from scanner import AUDIO_EXTENSIONS, extract_metadata

LOSSLESS_EXTENSIONS = {".wav", ".flac", ".aiff", ".aif"}


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def has_aria2() -> bool:
    return shutil.which("aria2c") is not None


def compress_to_opus(src_path: str, dest_dir: str, *, force: bool = False) -> tuple[str, bool]:
    """Compress lossless or imported audio to 128 kbps Opus if ffmpeg is available."""
    ext = Path(src_path).suffix.lower()
    should_compress = force and ext != ".opus" or ext in LOSSLESS_EXTENSIONS
    if not should_compress or not has_ffmpeg():
        return src_path, False

    os.makedirs(dest_dir, exist_ok=True)
    stem = Path(src_path).stem
    out_path = os.path.join(dest_dir, f"{stem}.opus")
    counter = 1
    while os.path.exists(out_path) and os.path.abspath(out_path) != os.path.abspath(src_path):
        out_path = os.path.join(dest_dir, f"{stem}_{counter}.opus")
        counter += 1

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        src_path,
        "-vn",
        "-c:a",
        "libopus",
        "-b:a",
        "128k",
        out_path,
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode == 0 and os.path.exists(out_path):
        if os.path.abspath(src_path) != os.path.abspath(out_path):
            try:
                os.remove(src_path)
            except OSError:
                pass
        return out_path, True
    return src_path, False


def _unique_dest(dest_dir: str, filename: str) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    clean_name = "".join(c for c in filename if c not in '<>:"/\\|?*').strip() or "track.opus"
    base = Path(clean_name).stem
    ext = Path(clean_name).suffix.lower()
    candidate = os.path.join(dest_dir, f"{base}{ext}")
    idx = 1
    while os.path.exists(candidate):
        candidate = os.path.join(dest_dir, f"{base}_{idx}{ext}")
        idx += 1
    return candidate


class ImportManager:
    def __init__(self, db_path: str, music_dir: str, covers_dir: str) -> None:
        self.db_path = db_path
        self.music_dir = os.path.abspath(music_dir)
        self.covers_dir = os.path.abspath(covers_dir)
        self._queue: queue.Queue[tuple[int, str, str, int]] = queue.Queue()
        self._thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._started = False

    def start(self) -> None:
        if not self._started:
            self._started = True
            self._thread.start()

    def submit(self, job_id: int, source_type: str, source_value: str, user_id: int) -> None:
        self.start()
        self._queue.put((job_id, source_type, source_value, user_id))

    def _index_file(self, file_path: str, user_id: int) -> None:
        full_path = os.path.abspath(file_path)
        meta = extract_metadata(full_path, self.covers_dir)
        db.upsert_track(
            self.db_path,
            title=meta["title"],
            artist=meta["artist"],
            album=meta["album"],
            duration=meta["duration"],
            file_path=full_path,
            cover_path=meta["cover_path"],
            lyrics=meta["lyrics"],
            added_by=user_id,
        )

    def _worker_loop(self) -> None:
        while True:
            job_id, source_type, source_value, user_id = self._queue.get()
            try:
                if source_type == "url":
                    self._handle_url(job_id, source_value, user_id)
                elif source_type == "youtube":
                    self._handle_youtube(job_id, source_value, user_id)
                elif source_type == "torrent":
                    self._handle_torrent(job_id, source_value, user_id)
                else:
                    db.update_import_job(self.db_path, job_id, "error", "Unknown import type.")
            except Exception as exc:
                db.update_import_job(self.db_path, job_id, "error", str(exc)[:300])
            finally:
                self._queue.task_done()

    def _handle_url(self, job_id: int, url: str, user_id: int) -> None:
        db.update_import_job(self.db_path, job_id, "downloading", "Downloading audio from URL...")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("Only http/https URLs are supported.")

        filename = os.path.basename(parsed.path) or "downloaded_track.mp3"
        ext = Path(filename).suffix.lower()
        if ext not in AUDIO_EXTENSIONS:
            ext = ".mp3"
            filename = f"{Path(filename).stem}{ext}"

        dest_path = _unique_dest(self.music_dir, filename)
        with requests.get(url, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)

        db.update_import_job(self.db_path, job_id, "processing", "Optimizing & reading metadata...")
        final_path, compressed = compress_to_opus(dest_path, self.music_dir, force=False)
        self._index_file(final_path, user_id)
        note = "Imported & compressed to Opus 128k." if compressed else "Imported successfully."
        db.update_import_job(self.db_path, job_id, "done", note)

    def _handle_youtube(self, job_id: int, url: str, user_id: int) -> None:
        db.update_import_job(self.db_path, job_id, "downloading", "Fetching audio via yt-dlp...")
        with tempfile.TemporaryDirectory() as tmpdir:
            opts: dict = {
                "format": "bestaudio/best",
                "outtmpl": os.path.join(tmpdir, "%(title)s.%(ext)s"),
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
            }
            if has_ffmpeg():
                opts["writethumbnail"] = True
                opts["postprocessors"] = [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "opus",
                        "preferredquality": "128",
                    },
                    {"key": "FFmpegMetadata"},
                    {"key": "EmbedThumbnail"},
                ]

            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])

            db.update_import_job(self.db_path, job_id, "processing", "Indexing downloaded audio...")
            imported = 0
            for name in os.listdir(tmpdir):
                ext = Path(name).suffix.lower()
                if ext not in AUDIO_EXTENSIONS and ext != ".webm":
                    continue
                src = os.path.join(tmpdir, name)
                dest = _unique_dest(self.music_dir, name)
                shutil.move(src, dest)
                if Path(dest).suffix.lower() in AUDIO_EXTENSIONS:
                    self._index_file(dest, user_id)
                    imported += 1

            if imported == 0:
                raise RuntimeError("No playable audio file was produced by yt-dlp.")

        msg = "Downloaded & compressed to Opus 128k." if has_ffmpeg() else "Downloaded (install ffmpeg for Opus conversion)."
        db.update_import_job(self.db_path, job_id, "done", msg)

    def _handle_torrent(self, job_id: int, source: str, user_id: int) -> None:
        if not has_aria2():
            raise RuntimeError(
                "aria2c is not installed on this machine (it is pre-installed in the Docker image for your server)."
            )

        db.update_import_job(self.db_path, job_id, "downloading", "Downloading torrent via aria2c...")
        with tempfile.TemporaryDirectory() as tmpdir:
            cmd = [
                "aria2c",
                "--seed-time=0",
                "--max-overall-upload-limit=100K",
                "--summary-interval=0",
                "-d",
                tmpdir,
                source,
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"aria2c failed: {res.stderr.strip() or res.stdout.strip()}")

            db.update_import_job(self.db_path, job_id, "processing", "Compressing & importing tracks...")
            imported = 0
            for root, _, files in os.walk(tmpdir):
                for name in sorted(files):
                    ext = Path(name).suffix.lower()
                    if ext not in AUDIO_EXTENSIONS:
                        continue
                    src = os.path.join(root, name)
                    dest = _unique_dest(self.music_dir, name)
                    shutil.move(src, dest)
                    final_path, _ = compress_to_opus(dest, self.music_dir, force=False)
                    self._index_file(final_path, user_id)
                    imported += 1

            if imported == 0:
                raise RuntimeError("Torrent finished, but no supported audio files were found.")

        db.update_import_job(self.db_path, job_id, "done", f"Imported {imported} track(s) from torrent.")
