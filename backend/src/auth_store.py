"""
Authentication + RBAC store (Phase 5).

Real, backend-enforced sessions replacing the old client-side-only gate.
Each user has a role (viewer < operator < admin) and a department; login
issues an opaque session token stored server-side with an expiry. Passwords
are salted + PBKDF2-hashed with the stdlib — no plaintext, and no credentials
baked into the frontend bundle.

Department is the RBAC scope: a viewer/operator only sees cameras in their own
department; an admin (department "*") sees everything. Role gates actions:
viewers are read-only, operators can modify within their department, admins
can do anything including user management and viewing the audit log.
"""

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime

_BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
_DB_PATH = os.path.join(_BASE_DIR, "history.db")

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3}
ALL_DEPARTMENTS = "*"
SESSION_TTL_S = 12 * 3600
_PBKDF2_ITERS = 120_000

# Seed roster (demo). Passwords are provisioning-only; a real deployment would
# create users out-of-band. Departments line up with the seeded cameras so the
# RBAC difference is visible immediately.
DEFAULT_USERS = [
    {"username": "admin", "password": "admin123", "role": "admin",
     "department": ALL_DEPARTMENTS, "display_name": "System Administrator"},
    {"username": "rakesh", "password": "traffic123", "role": "operator",
     "department": "Traffic Police", "display_name": "Insp. Rakesh — Traffic Police"},
    {"username": "meena", "password": "viewer123", "role": "viewer",
     "department": "Home Guard", "display_name": "Const. Meena — Home Guard"},
]


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _conn = conn
    return _conn


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERS)
    return f"{salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split("$")
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), _PBKDF2_ITERS)
    return hmac.compare_digest(dk.hex(), dk_hex)


def init_db():
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
        conn = _get_conn()
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL,
                    department TEXT NOT NULL,
                    display_name TEXT,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )


def seed_users(users=DEFAULT_USERS):
    """Populate the roster only if empty (never clobbers real users)."""
    with _lock:
        conn = _get_conn()
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
            return
    for u in users:
        add_user(u["username"], u["password"], u["role"], u["department"], u.get("display_name"))


def add_user(username, password, role, department, display_name=None) -> dict:
    if role not in ROLE_RANK:
        raise ValueError(f"Unknown role '{role}'.")
    now = time.time()
    with _lock:
        conn = _get_conn()
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise ValueError(f"User '{username}' already exists.")
        with conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, department, display_name, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (username, _hash_password(password), role, department, display_name or username, now),
            )
    return {"username": username, "role": role, "department": department,
            "display_name": display_name or username}


def _public(row: sqlite3.Row) -> dict:
    return {"username": row["username"], "role": row["role"],
            "department": row["department"], "display_name": row["display_name"]}


def list_users() -> list[dict]:
    with _lock:
        conn = _get_conn()
        rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
        return [_public(r) for r in rows]


def verify_login(username: str, password: str) -> dict | None:
    with _lock:
        conn = _get_conn()
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if row is None or not _verify_password(password, row["password_hash"]):
        return None
    return _public(row)


def create_session(username: str) -> dict:
    token = secrets.token_urlsafe(32)
    now = time.time()
    expires = now + SESSION_TTL_S
    with _lock:
        conn = _get_conn()
        with conn:
            conn.execute(
                "INSERT INTO sessions (token, username, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token, username, now, expires),
            )
    return {"token": token, "expires_at": expires}


def get_session_user(token: str) -> dict | None:
    """Resolve a token to its user, or None if missing/expired. Expired
    sessions are cleaned up opportunistically."""
    if not token:
        return None
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            """
            SELECT u.username, u.role, u.department, u.display_name, s.expires_at
            FROM sessions s JOIN users u ON u.username = s.username
            WHERE s.token = ?
            """,
            (token,),
        ).fetchone()
        if row is None:
            return None
        if row["expires_at"] < time.time():
            with conn:
                conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            return None
    return {"username": row["username"], "role": row["role"],
            "department": row["department"], "display_name": row["display_name"]}


def delete_session(token: str) -> None:
    with _lock:
        conn = _get_conn()
        with conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def role_at_least(user: dict, minimum: str) -> bool:
    return ROLE_RANK.get(user.get("role"), 0) >= ROLE_RANK.get(minimum, 99)


def can_access_department(user: dict, department: str | None) -> bool:
    """Admins (department '*') see everything; others only their own department."""
    if user.get("department") == ALL_DEPARTMENTS:
        return True
    return department is not None and department == user.get("department")
