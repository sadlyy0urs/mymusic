import hashlib
import os
import secrets
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

import db
from importer import ImportManager, compress_to_opus, has_aria2, has_ffmpeg
from scanner import AUDIO_EXTENSIONS, extract_metadata, scan_music_folder, write_audio_tags

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.abspath(os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data")))
MUSIC_DIR = os.path.abspath(os.environ.get("MUSIC_DIR", os.path.join(BASE_DIR, "music")))
COVERS_DIR = os.path.join(DATA_DIR, "covers")
DB_PATH = os.path.join(DATA_DIR, "mymusic.db")
APP_NAME = os.environ.get("APP_NAME", "MyMusic")


def _load_secret_key() -> str:
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key
    os.makedirs(DATA_DIR, exist_ok=True)
    key_file = os.path.join(DATA_DIR, ".secret_key")
    if os.path.exists(key_file):
        with open(key_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(key_file, "w", encoding="utf-8") as f:
        f.write(key)
    return key


app = Flask(__name__)
app.config["SECRET_KEY"] = _load_secret_key()
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024  # 512 MB max upload

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(MUSIC_DIR, exist_ok=True)
os.makedirs(COVERS_DIR, exist_ok=True)

db.init_db(DB_PATH)
scan_music_folder(DB_PATH, MUSIC_DIR, COVERS_DIR, db.upsert_track)

import_manager = ImportManager(DB_PATH, MUSIC_DIR, COVERS_DIR)
import_manager.start()


def current_user() -> dict | None:
    uid = session.get("user_id")
    if not uid:
        return None
    return db.get_user_by_id(DB_PATH, uid)


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("login", next=request.full_path.rstrip("?")))
        return fn(user, *args, **kwargs)

    return wrapper


def uploader_required(fn):
    @wraps(fn)
    @login_required
    def wrapper(user, *args, **kwargs):
        if user["role"] not in ("admin", "trusted"):
            return jsonify({"error": "Upload/import permission required"}), 403
        return fn(user, *args, **kwargs)

    return wrapper


def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapper(user, *args, **kwargs):
        if user["role"] != "admin":
            return jsonify({"error": "Admin permission required"}), 403
        return fn(user, *args, **kwargs)

    return wrapper


@app.context_processor
def inject_branding():
    logo_file = "logo.png" if os.path.exists(os.path.join(BASE_DIR, "static", "logo.png")) else "logo.svg"
    return {"app_name": APP_NAME, "logo_file": logo_file}


# =========================
# AUTH PAGES & ENDPOINTS
# =========================


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("home"))
    first_setup = db.user_count(DB_PATH) == 0
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        user = db.authenticate_user(DB_PATH, username, password)
        if user:
            session["user_id"] = user["id"]
            next_url = request.args.get("next") or url_for("home")
            return redirect(next_url)
        error = "Invalid username or password."
    return render_template("login.html", mode="login", first_setup=first_setup, error=error)


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(url_for("home"))
    first_setup = db.user_count(DB_PATH) == 0
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        invite_code = request.form.get("invite_code", "")
        user, err = db.create_user(DB_PATH, username, password, invite_code)
        if user:
            session["user_id"] = user["id"]
            return redirect(url_for("home"))
        error = err
    return render_template("login.html", mode="register", first_setup=first_setup, error=error)


@app.route("/logout", methods=["POST", "GET"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/api/me")
@login_required
def api_me(user):
    return jsonify(
        {
            "user": user,
            "capabilities": {"ffmpeg": has_ffmpeg(), "aria2c": has_aria2()},
        }
    )


@app.route("/api/invites", methods=["GET", "POST"])
@admin_required
def api_invites(user):
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        role = data.get("role", "listener")
        invite = db.create_invite(DB_PATH, user["id"], role)
        return jsonify(invite), 201
    return jsonify({"invites": db.list_invites(DB_PATH)})


@app.route("/api/users")
@admin_required
def api_users(_user):
    return jsonify({"users": db.list_users(DB_PATH)})


@app.route("/api/users/<int:target_id>/role", methods=["PATCH"])
@admin_required
def api_update_role(user, target_id):
    if target_id == user["id"]:
        return jsonify({"error": "You cannot change your own role."}), 400
    data = request.get_json(silent=True) or {}
    role = data.get("role", "")
    if not db.update_user_role(DB_PATH, target_id, role):
        return jsonify({"error": "Invalid user or role."}), 400
    return jsonify({"ok": True})


# =========================
# MAIN APP & PWA ROUTES
# =========================


@app.route("/")
@login_required
def home(user):
    return render_template("index.html", user=user, share_type=None, share_id=None)


@app.route("/share/track/<int:track_id>")
@login_required
def share_track(user, track_id):
    return render_template("index.html", user=user, share_type="track", share_id=track_id)


@app.route("/share/playlist/<int:playlist_id>")
@login_required
def share_playlist(user, playlist_id):
    return render_template("index.html", user=user, share_type="playlist", share_id=playlist_id)


@app.route("/sw.js")
def service_worker():
    return send_from_directory(os.path.join(BASE_DIR, "static"), "sw.js", mimetype="application/javascript")


# =========================
# TRACKS, STREAMING & TAGS
# =========================


@app.route("/api/tracks")
@login_required
def api_tracks(user):
    q = request.args.get("q", "")
    liked_only = request.args.get("liked") == "1"
    tracks = db.list_tracks(DB_PATH, user["id"], query=q, liked_only=liked_only)
    return jsonify({"tracks": tracks})


@app.route("/api/tracks/<int:track_id>")
@login_required
def api_track_detail(user, track_id):
    track = db.get_track(DB_PATH, track_id, user["id"])
    if not track:
        return jsonify({"error": "Track not found"}), 404
    return jsonify({"track": track})


@app.route("/stream/<int:track_id>")
@login_required
def stream_track(_user, track_id):
    track = db.get_track(DB_PATH, track_id)
    if not track or not os.path.exists(track["file_path"]):
        abort(404)
    return send_file(track["file_path"], conditional=True)


@app.route("/download/<int:track_id>")
@login_required
def download_track(_user, track_id):
    track = db.get_track(DB_PATH, track_id)
    if not track or not os.path.exists(track["file_path"]):
        abort(404)
    ext = Path(track["file_path"]).suffix
    dl_name = f"{track['artist']} - {track['title']}{ext}"
    return send_file(track["file_path"], as_attachment=True, download_name=dl_name)


@app.route("/covers/<path:filename>")
@login_required
def serve_cover(_user, filename):
    return send_from_directory(COVERS_DIR, filename)


@app.route("/api/tracks/<int:track_id>/play", methods=["POST"])
@login_required
def api_record_play(user, track_id):
    if not db.get_track(DB_PATH, track_id):
        return jsonify({"error": "Track not found"}), 404
    db.record_play(DB_PATH, user["id"], track_id)
    return jsonify({"ok": True})


@app.route("/api/tracks/<int:track_id>", methods=["PATCH"])
@uploader_required
def api_edit_track(_user, track_id):
    track = db.get_track(DB_PATH, track_id)
    if not track:
        return jsonify({"error": "Track not found"}), 404

    cover_filename = None
    if request.content_type and request.content_type.startswith("multipart/form-data"):
        title = request.form.get("title")
        artist = request.form.get("artist")
        album = request.form.get("album")
        lyrics = request.form.get("lyrics")
        cover_file = request.files.get("cover")
        if cover_file and cover_file.filename:
            raw = cover_file.read()
            if raw:
                digest = hashlib.sha1(raw).hexdigest()[:16]
                ext = ".png" if raw.startswith(b"\x89PNG") else ".jpg"
                cover_filename = f"{digest}{ext}"
                with open(os.path.join(COVERS_DIR, cover_filename), "wb") as f:
                    f.write(raw)
    else:
        data = request.get_json(silent=True) or {}
        title = data.get("title")
        artist = data.get("artist")
        album = data.get("album")
        lyrics = data.get("lyrics")

    db.update_track_metadata(
        DB_PATH,
        track_id,
        title=title.strip() if isinstance(title, str) else None,
        artist=artist.strip() if isinstance(artist, str) else None,
        album=album.strip() if isinstance(album, str) else None,
        lyrics=lyrics if isinstance(lyrics, str) else None,
        cover_path=cover_filename,
    )
    updated = db.get_track(DB_PATH, track_id)
    if updated and os.path.exists(updated["file_path"]):
        write_audio_tags(updated["file_path"], updated["title"], updated["artist"], updated["album"])
    return jsonify({"track": updated})


@app.route("/api/tracks/<int:track_id>", methods=["DELETE"])
@login_required
def api_delete_track(user, track_id):
    track = db.get_track(DB_PATH, track_id)
    if not track:
        return jsonify({"error": "Track not found"}), 404
    if user["role"] != "admin" and track["added_by"] != user["id"]:
        return jsonify({"error": "Only an admin or the uploader can delete this track."}), 403

    removed = db.delete_track(DB_PATH, track_id)
    if removed and os.path.exists(removed["file_path"]):
        try:
            os.remove(removed["file_path"])
        except OSError:
            pass
    return jsonify({"ok": True})


@app.route("/api/history")
@login_required
def api_history(user):
    return jsonify(db.get_history_and_stats(DB_PATH, user["id"]))


# =========================
# LIKES & PLAYLISTS
# =========================


@app.route("/api/likes/<int:track_id>", methods=["POST"])
@login_required
def api_toggle_like(user, track_id):
    if not db.get_track(DB_PATH, track_id):
        return jsonify({"error": "Track not found"}), 404
    liked = db.toggle_like(DB_PATH, user["id"], track_id)
    return jsonify({"liked": liked})


@app.route("/api/playlists", methods=["GET", "POST"])
@login_required
def api_playlists(user):
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        pl = db.create_playlist(DB_PATH, user["id"], data.get("name", "My Playlist"))
        return jsonify({"playlist": pl}), 201
    return jsonify({"playlists": db.list_playlists(DB_PATH, user["id"])})


@app.route("/api/playlists/<int:playlist_id>", methods=["GET", "DELETE"])
@login_required
def api_playlist_detail(user, playlist_id):
    if request.method == "DELETE":
        if not db.delete_playlist(DB_PATH, playlist_id, user["id"]):
            return jsonify({"error": "Playlist not found or not yours."}), 404
        return jsonify({"ok": True})
    pl = db.get_playlist_with_tracks(DB_PATH, playlist_id, user["id"])
    if not pl:
        return jsonify({"error": "Playlist not found"}), 404
    return jsonify({"playlist": pl})


@app.route("/api/playlists/<int:playlist_id>/tracks", methods=["POST", "DELETE"])
@login_required
def api_playlist_tracks(user, playlist_id):
    data = request.get_json(silent=True) or {}
    track_id = data.get("track_id")
    if not isinstance(track_id, int):
        return jsonify({"error": "track_id required"}), 400
    if request.method == "POST":
        if not db.add_track_to_playlist(DB_PATH, playlist_id, user["id"], track_id):
            return jsonify({"error": "Could not add track to playlist."}), 400
        return jsonify({"ok": True})
    db.remove_track_from_playlist(DB_PATH, playlist_id, user["id"], track_id)
    return jsonify({"ok": True})


# =========================
# SCAN, UPLOAD & IMPORT
# =========================


@app.route("/api/scan", methods=["POST"])
@uploader_required
def api_scan(_user):
    count = scan_music_folder(DB_PATH, MUSIC_DIR, COVERS_DIR, db.upsert_track)
    return jsonify({"scanned": count})


@app.route("/api/upload", methods=["POST"])
@uploader_required
def api_upload(user):
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files uploaded"}), 400

    uploaded = 0
    compressed_count = 0
    for f in files:
        if not f.filename:
            continue
        safe = secure_filename(f.filename)
        ext = Path(safe).suffix.lower()
        if ext not in AUDIO_EXTENSIONS:
            continue
        dest = os.path.join(MUSIC_DIR, safe)
        stem = Path(safe).stem
        idx = 1
        while os.path.exists(dest):
            dest = os.path.join(MUSIC_DIR, f"{stem}_{idx}{ext}")
            idx += 1
        f.save(dest)
        final_path, compressed = compress_to_opus(dest, MUSIC_DIR, force=False)
        if compressed:
            compressed_count += 1
        meta = extract_metadata(final_path, COVERS_DIR)
        db.upsert_track(
            DB_PATH,
            title=meta["title"],
            artist=meta["artist"],
            album=meta["album"],
            duration=meta["duration"],
            file_path=os.path.abspath(final_path),
            cover_path=meta["cover_path"],
            lyrics=meta["lyrics"],
            added_by=user["id"],
        )
        uploaded += 1

    if uploaded == 0:
        return jsonify({"error": "No supported audio files found (.opus, .mp3, .m4a, .ogg, .flac, .wav)."}), 400
    return jsonify({"uploaded": uploaded, "compressed": compressed_count})


@app.route("/api/import", methods=["GET", "POST"])
@login_required
def api_import(user):
    if request.method == "GET":
        return jsonify({"jobs": db.list_import_jobs(DB_PATH)})
    if user["role"] not in ("admin", "trusted"):
        return jsonify({"error": "Upload/import permission required"}), 403
    data = request.get_json(silent=True) or {}
    source_type = data.get("source_type", "").strip()
    source_value = data.get("source_value", "").strip()
    if source_type not in ("url", "youtube", "torrent") or not source_value:
        return jsonify({"error": "Provide a valid source_type and source_value."}), 400
    job_id = db.create_import_job(DB_PATH, source_type, source_value, user["id"])
    import_manager.submit(job_id, source_type, source_value, user["id"])
    return jsonify({"job_id": job_id}), 202


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
