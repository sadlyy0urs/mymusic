import os
import secrets
import sqlite3
from datetime import datetime, timezone
from werkzeug.security import check_password_hash, generate_password_hash


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str) -> None:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with get_connection(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin', 'trusted', 'listener')),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS invites (
                code TEXT PRIMARY KEY,
                role TEXT NOT NULL CHECK (role IN ('admin', 'trusted', 'listener')),
                created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                used_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                artist TEXT NOT NULL DEFAULT 'Unknown Artist',
                album TEXT NOT NULL DEFAULT 'Single',
                duration REAL NOT NULL DEFAULT 0,
                file_path TEXT NOT NULL UNIQUE,
                cover_path TEXT,
                lyrics TEXT DEFAULT '',
                play_count INTEGER NOT NULL DEFAULT 0,
                added_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS likes (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                PRIMARY KEY (user_id, track_id)
            );

            CREATE TABLE IF NOT EXISTS play_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
                played_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS playlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS playlist_tracks (
                playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
                track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
                position INTEGER NOT NULL DEFAULT 0,
                added_at TEXT NOT NULL,
                PRIMARY KEY (playlist_id, track_id)
            );

            CREATE TABLE IF NOT EXISTS import_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT NOT NULL CHECK (source_type IN ('url', 'youtube', 'torrent')),
                source_value TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('queued', 'downloading', 'processing', 'done', 'error')),
                message TEXT NOT NULL DEFAULT '',
                created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def user_count(db_path: str) -> int:
    with get_connection(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def get_user_by_id(db_path: str, user_id: int) -> dict | None:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT id, username, role, created_at FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


def authenticate_user(db_path: str, username: str, password: str) -> dict | None:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, role, created_at FROM users WHERE username = ?",
            (username.strip(),),
        ).fetchone()
        if not row or not check_password_hash(row["password_hash"], password):
            return None
        return {
            "id": row["id"],
            "username": row["username"],
            "role": row["role"],
            "created_at": row["created_at"],
        }


def create_user(
    db_path: str, username: str, password: str, invite_code: str | None = None
) -> tuple[dict | None, str | None]:
    clean_name = username.strip()
    if len(clean_name) < 2 or len(password) < 4:
        return None, "Username must be at least 2 characters and password at least 4 characters."

    with get_connection(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if total == 0:
            role = "admin"
            invite_row = None
        else:
            if not invite_code:
                return None, "An invite code is required to register."
            invite_row = conn.execute(
                "SELECT code, role, used_by FROM invites WHERE code = ?",
                (invite_code.strip(),),
            ).fetchone()
            if not invite_row or invite_row["used_by"] is not None:
                return None, "Invalid or already used invite code."
            role = invite_row["role"]

        try:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                (clean_name, generate_password_hash(password), role, utc_now()),
            )
            uid = cur.lastrowid
            if invite_row:
                conn.execute(
                    "UPDATE invites SET used_by = ? WHERE code = ?",
                    (uid, invite_row["code"]),
                )
            conn.commit()
            row = conn.execute(
                "SELECT id, username, role, created_at FROM users WHERE id = ?", (uid,)
            ).fetchone()
            return (dict(row) if row else None), None
        except sqlite3.IntegrityError:
            return None, "That username is already taken."


def create_invite(db_path: str, created_by: int, role: str = "listener") -> dict:
    if role not in ("admin", "trusted", "listener"):
        role = "listener"
    code = secrets.token_urlsafe(6)
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO invites (code, role, created_by, created_at) VALUES (?, ?, ?, ?)",
            (code, role, created_by, utc_now()),
        )
    return {"code": code, "role": role}


def list_invites(db_path: str) -> list[dict]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT i.code, i.role, i.created_at, u.username AS used_by_name
            FROM invites i
            LEFT JOIN users u ON i.used_by = u.id
            ORDER BY i.created_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def list_users(db_path: str) -> list[dict]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT id, username, role, created_at FROM users ORDER BY id ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def update_user_role(db_path: str, target_user_id: int, role: str) -> bool:
    if role not in ("admin", "trusted", "listener"):
        return False
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "UPDATE users SET role = ? WHERE id = ?", (role, target_user_id)
        )
        return cur.rowcount > 0


def upsert_track(
    db_path: str,
    *,
    title: str,
    artist: str,
    album: str,
    duration: float,
    file_path: str,
    cover_path: str | None,
    lyrics: str = "",
    added_by: int | None = None,
) -> int:
    with get_connection(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM tracks WHERE file_path = ?", (file_path,)
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE tracks
                SET title = ?, artist = ?, album = ?, duration = ?,
                    cover_path = COALESCE(?, cover_path),
                    lyrics = CASE WHEN lyrics = '' THEN ? ELSE lyrics END
                WHERE id = ?
                """,
                (title, artist, album, duration, cover_path, lyrics, existing["id"]),
            )
            return existing["id"]
        cur = conn.execute(
            """
            INSERT INTO tracks (title, artist, album, duration, file_path, cover_path, lyrics, added_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (title, artist, album, duration, file_path, cover_path, lyrics, added_by, utc_now()),
        )
        return cur.lastrowid


def list_tracks(db_path: str, user_id: int, query: str = "", liked_only: bool = False) -> list[dict]:
    sql = """
        SELECT t.*,
               u.username AS added_by_name,
               CASE WHEN l.user_id IS NOT NULL THEN 1 ELSE 0 END AS liked
        FROM tracks t
        LEFT JOIN users u ON t.added_by = u.id
        LEFT JOIN likes l ON l.track_id = t.id AND l.user_id = ?
        WHERE 1=1
    """
    params: list = [user_id]
    if liked_only:
        sql += " AND l.user_id IS NOT NULL"
    if query.strip():
        like_q = f"%{query.strip()}%"
        sql += " AND (t.title LIKE ? OR t.artist LIKE ? OR t.album LIKE ?)"
        params.extend([like_q, like_q, like_q])
    sql += " ORDER BY t.created_at DESC, t.id DESC"
    with get_connection(db_path) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_track(db_path: str, track_id: int, user_id: int | None = None) -> dict | None:
    with get_connection(db_path) as conn:
        if user_id is not None:
            row = conn.execute(
                """
                SELECT t.*,
                       u.username AS added_by_name,
                       CASE WHEN l.user_id IS NOT NULL THEN 1 ELSE 0 END AS liked
                FROM tracks t
                LEFT JOIN users u ON t.added_by = u.id
                LEFT JOIN likes l ON l.track_id = t.id AND l.user_id = ?
                WHERE t.id = ?
                """,
                (user_id, track_id),
            ).fetchone()
        else:
            row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        return dict(row) if row else None


def update_track_metadata(
    db_path: str,
    track_id: int,
    *,
    title: str | None = None,
    artist: str | None = None,
    album: str | None = None,
    lyrics: str | None = None,
    cover_path: str | None = None,
) -> bool:
    fields = []
    params = []
    for col, val in (
        ("title", title),
        ("artist", artist),
        ("album", album),
        ("lyrics", lyrics),
        ("cover_path", cover_path),
    ):
        if val is not None:
            fields.append(f"{col} = ?")
            params.append(val)
    if not fields:
        return False
    params.append(track_id)
    with get_connection(db_path) as conn:
        cur = conn.execute(f"UPDATE tracks SET {', '.join(fields)} WHERE id = ?", params)
        return cur.rowcount > 0


def delete_track(db_path: str, track_id: int) -> dict | None:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
        return dict(row)


def toggle_like(db_path: str, user_id: int, track_id: int) -> bool:
    with get_connection(db_path) as conn:
        exists = conn.execute(
            "SELECT 1 FROM likes WHERE user_id = ? AND track_id = ?",
            (user_id, track_id),
        ).fetchone()
        if exists:
            conn.execute(
                "DELETE FROM likes WHERE user_id = ? AND track_id = ?",
                (user_id, track_id),
            )
            return False
        conn.execute(
            "INSERT INTO likes (user_id, track_id, created_at) VALUES (?, ?, ?)",
            (user_id, track_id, utc_now()),
        )
        return True


def record_play(db_path: str, user_id: int, track_id: int) -> None:
    with get_connection(db_path) as conn:
        conn.execute("UPDATE tracks SET play_count = play_count + 1 WHERE id = ?", (track_id,))
        conn.execute(
            "INSERT INTO play_history (user_id, track_id, played_at) VALUES (?, ?, ?)",
            (user_id, track_id, utc_now()),
        )


def get_history_and_stats(db_path: str, user_id: int) -> dict:
    with get_connection(db_path) as conn:
        recent = conn.execute(
            """
            SELECT t.*, MAX(h.played_at) AS last_played,
                   CASE WHEN l.user_id IS NOT NULL THEN 1 ELSE 0 END AS liked
            FROM play_history h
            JOIN tracks t ON t.id = h.track_id
            LEFT JOIN likes l ON l.track_id = t.id AND l.user_id = ?
            WHERE h.user_id = ?
            GROUP BY t.id
            ORDER BY last_played DESC
            LIMIT 20
            """,
            (user_id, user_id),
        ).fetchall()

        top = conn.execute(
            """
            SELECT t.*,
                   CASE WHEN l.user_id IS NOT NULL THEN 1 ELSE 0 END AS liked
            FROM tracks t
            LEFT JOIN likes l ON l.track_id = t.id AND l.user_id = ?
            WHERE t.play_count > 0
            ORDER BY t.play_count DESC, t.id DESC
            LIMIT 10
            """,
            (user_id,),
        ).fetchall()

        total_plays = conn.execute(
            "SELECT COUNT(*) FROM play_history WHERE user_id = ?", (user_id,)
        ).fetchone()[0]

        return {
            "recent": [dict(r) for r in recent],
            "top": [dict(r) for r in top],
            "total_plays": total_plays,
        }


def list_playlists(db_path: str, user_id: int) -> list[dict]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.name, p.user_id, p.created_at,
                   COUNT(pt.track_id) AS track_count,
                   (
                       SELECT t.cover_path FROM playlist_tracks pt2
                       JOIN tracks t ON t.id = pt2.track_id
                       WHERE pt2.playlist_id = p.id AND t.cover_path IS NOT NULL
                       ORDER BY pt2.position ASC LIMIT 1
                   ) AS cover_path
            FROM playlists p
            LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id
            WHERE p.user_id = ?
            GROUP BY p.id
            ORDER BY p.created_at DESC
            """,
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def create_playlist(db_path: str, user_id: int, name: str) -> dict:
    clean = name.strip() or "Untitled Playlist"
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO playlists (name, user_id, created_at) VALUES (?, ?, ?)",
            (clean, user_id, utc_now()),
        )
        return {"id": cur.lastrowid, "name": clean, "user_id": user_id, "track_count": 0}


def get_playlist_with_tracks(db_path: str, playlist_id: int, viewer_id: int) -> dict | None:
    with get_connection(db_path) as conn:
        pl = conn.execute(
            """
            SELECT p.*, u.username AS owner_name
            FROM playlists p
            JOIN users u ON u.id = p.user_id
            WHERE p.id = ?
            """,
            (playlist_id,),
        ).fetchone()
        if not pl:
            return None
        tracks = conn.execute(
            """
            SELECT t.*,
                   CASE WHEN l.user_id IS NOT NULL THEN 1 ELSE 0 END AS liked
            FROM playlist_tracks pt
            JOIN tracks t ON t.id = pt.track_id
            LEFT JOIN likes l ON l.track_id = t.id AND l.user_id = ?
            WHERE pt.playlist_id = ?
            ORDER BY pt.position ASC, pt.added_at ASC
            """,
            (viewer_id, playlist_id),
        ).fetchall()
        result = dict(pl)
        result["tracks"] = [dict(r) for r in tracks]
        return result


def add_track_to_playlist(db_path: str, playlist_id: int, user_id: int, track_id: int) -> bool:
    with get_connection(db_path) as conn:
        pl = conn.execute(
            "SELECT id FROM playlists WHERE id = ? AND user_id = ?", (playlist_id, user_id)
        ).fetchone()
        if not pl:
            return False
        pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM playlist_tracks WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT OR IGNORE INTO playlist_tracks (playlist_id, track_id, position, added_at)
            VALUES (?, ?, ?, ?)
            """,
            (playlist_id, track_id, pos, utc_now()),
        )
        return True


def remove_track_from_playlist(db_path: str, playlist_id: int, user_id: int, track_id: int) -> bool:
    with get_connection(db_path) as conn:
        pl = conn.execute(
            "SELECT id FROM playlists WHERE id = ? AND user_id = ?", (playlist_id, user_id)
        ).fetchone()
        if not pl:
            return False
        conn.execute(
            "DELETE FROM playlist_tracks WHERE playlist_id = ? AND track_id = ?",
            (playlist_id, track_id),
        )
        return True


def delete_playlist(db_path: str, playlist_id: int, user_id: int) -> bool:
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM playlists WHERE id = ? AND user_id = ?", (playlist_id, user_id)
        )
        return cur.rowcount > 0


def create_import_job(db_path: str, source_type: str, source_value: str, created_by: int) -> int:
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO import_jobs (source_type, source_value, status, message, created_by, created_at)
            VALUES (?, ?, 'queued', 'Waiting in queue...', ?, ?)
            """,
            (source_type, source_value, created_by, utc_now()),
        )
        return cur.lastrowid


def update_import_job(db_path: str, job_id: int, status: str, message: str = "") -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE import_jobs SET status = ?, message = ? WHERE id = ?",
            (status, message, job_id),
        )


def list_import_jobs(db_path: str, limit: int = 20) -> list[dict]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT j.*, u.username AS created_by_name
            FROM import_jobs j
            LEFT JOIN users u ON u.id = j.created_by
            ORDER BY j.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
