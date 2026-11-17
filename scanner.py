import base64
import hashlib
import os
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.flac import Picture

AUDIO_EXTENSIONS = {".opus", ".mp3", ".m4a", ".ogg", ".flac", ".wav"}


def _first_tag(tags, keys: list[str], default: str = "") -> str:
    if not tags:
        return default
    for key in keys:
        val = tags.get(key)
        if val:
            if isinstance(val, list):
                return str(val[0]).strip()
            return str(val).strip()
    return default


def _extract_cover_bytes(audio) -> bytes | None:
    if not audio or not getattr(audio, "tags", None):
        return None
    tags = audio.tags

    # ID3 (MP3)
    for key in list(tags.keys()):
        if key.startswith("APIC"):
            frame = tags[key]
            if hasattr(frame, "data"):
                return frame.data

    # FLAC
    if hasattr(audio, "pictures") and audio.pictures:
        return audio.pictures[0].data

    # Ogg / Opus (metadata_block_picture)
    if "metadata_block_picture" in tags:
        try:
            raw = base64.b64decode(tags["metadata_block_picture"][0])
            pic = Picture(raw)
            return pic.data
        except Exception:
            pass

    # MP4 / M4A
    if "covr" in tags and tags["covr"]:
        return bytes(tags["covr"][0])

    return None


def _save_cover(cover_bytes: bytes | None, covers_dir: str) -> str | None:
    if not cover_bytes:
        return None
    os.makedirs(covers_dir, exist_ok=True)
    digest = hashlib.sha1(cover_bytes).hexdigest()[:16]
    ext = ".png" if cover_bytes.startswith(b"\x89PNG") else ".jpg"
    filename = f"{digest}{ext}"
    dest = os.path.join(covers_dir, filename)
    if not os.path.exists(dest):
        with open(dest, "wb") as f:
            f.write(cover_bytes)
    return filename


def _fallback_from_filename(file_path: str) -> tuple[str, str]:
    stem = Path(file_path).stem
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return title.strip() or stem, artist.strip() or "Unknown Artist"
    return stem, "Unknown Artist"


def extract_metadata(file_path: str, covers_dir: str) -> dict:
    fallback_title, fallback_artist = _fallback_from_filename(file_path)
    title = fallback_title
    artist = fallback_artist
    album = "Single"
    duration = 0.0
    lyrics = ""
    cover_filename = None

    try:
        audio = MutagenFile(file_path, easy=False)
        if audio is not None:
            if getattr(audio, "info", None) and getattr(audio.info, "length", None):
                duration = float(audio.info.length)
            cover_filename = _save_cover(_extract_cover_bytes(audio), covers_dir)

        easy = MutagenFile(file_path, easy=True)
        if easy is not None and easy.tags:
            title = _first_tag(easy.tags, ["title"], fallback_title) or fallback_title
            artist = _first_tag(easy.tags, ["artist", "albumartist"], fallback_artist) or fallback_artist
            album = _first_tag(easy.tags, ["album"], "Single") or "Single"
            lyrics = _first_tag(easy.tags, ["lyrics", "unsyncedlyrics"], "")
    except Exception:
        pass

    return {
        "title": title,
        "artist": artist,
        "album": album,
        "duration": duration,
        "lyrics": lyrics,
        "cover_path": cover_filename,
    }


def write_audio_tags(file_path: str, title: str, artist: str, album: str) -> None:
    try:
        easy = MutagenFile(file_path, easy=True)
        if easy is None:
            return
        if easy.tags is None:
            easy.add_tags()
        easy["title"] = [title]
        easy["artist"] = [artist]
        easy["album"] = [album]
        easy.save()
    except Exception:
        pass


def scan_music_folder(db_path: str, music_dir: str, covers_dir: str, upsert_fn) -> int:
    os.makedirs(music_dir, exist_ok=True)
    count = 0
    for root, _, files in os.walk(music_dir):
        for name in sorted(files):
            ext = Path(name).suffix.lower()
            if ext not in AUDIO_EXTENSIONS:
                continue
            full_path = os.path.abspath(os.path.join(root, name))
            meta = extract_metadata(full_path, covers_dir)
            upsert_fn(
                db_path,
                title=meta["title"],
                artist=meta["artist"],
                album=meta["album"],
                duration=meta["duration"],
                file_path=full_path,
                cover_path=meta["cover_path"],
                lyrics=meta["lyrics"],
            )
            count += 1
    return count
