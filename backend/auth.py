"""Account and session storage for the ASHA register."""

import hashlib
import json
import secrets
import sqlite3
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

COOKIE = "__Host-asha_session"
SESSION_SECONDS = 8 * 3600
RESET_SECONDS = 15 * 60
PASSWORDS = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))


class Account(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]
    worker_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{2,31}$")
    phone: str = Field(pattern=r"^(?:\+91)?[6-9][0-9]{9}$")
    village: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]
    password: str = Field(min_length=12, max_length=128)
    confirm_password: str = Field(max_length=128)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Accounts:
    def __init__(self, register):
        self.register = register
        with register.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS workers (
                    id INTEGER PRIMARY KEY, worker_id TEXT NOT NULL UNIQUE,
                    profile BLOB NOT NULL, password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('worker', 'admin')),
                    status TEXT NOT NULL CHECK(status IN ('pending', 'active', 'inactive')),
                    reset_requested INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    digest TEXT PRIMARY KEY, worker INTEGER NOT NULL REFERENCES workers(id),
                    expires INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS password_resets (
                    digest TEXT PRIMARY KEY, worker INTEGER NOT NULL UNIQUE REFERENCES workers(id),
                    expires INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_limits (
                    bucket TEXT PRIMARY KEY, attempts INTEGER NOT NULL, expires INTEGER NOT NULL
                );
            """)
            first = db.execute("SELECT profile FROM workers LIMIT 1").fetchone()
            if first:
                register.cipher.decrypt(first[0])

    def profile(self, row):
        profile = json.loads(self.register.cipher.decrypt(row["profile"]))
        return {**profile, **{key: row[key] for key in ("id", "worker_id", "role", "status")}}

    def create(self, account, *, bootstrap=False):
        if not account.password.strip():
            raise ValueError("Use a password that contains more than spaces.")
        if account.password != account.confirm_password:
            raise ValueError("Passwords must match.")
        profile = account.model_dump(include={"full_name", "phone", "village"})
        profile["phone"] = "+91" + profile["phone"].removeprefix("+91")
        hashed = PASSWORDS.hash(account.password)
        with self.register.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if bootstrap and db.execute("SELECT 1 FROM workers WHERE role='admin'").fetchone():
                raise ValueError("An administrator already exists.")
            try:
                db.execute("INSERT INTO workers(worker_id, profile, password_hash, role, status) VALUES (?, ?, ?, ?, ?)",
                           (account.worker_id.upper(), self.register.encode(profile), hashed,
                            "admin" if bootstrap else "worker", "active" if bootstrap else "pending"))
            except sqlite3.IntegrityError:
                raise ValueError("That worker ID is already registered.") from None

    def limited(self, *buckets):
        now = int(time.time())
        blocked = False
        with self.register.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM auth_limits WHERE expires <= ?", (now,))
            for key, limit in buckets:
                key = digest(key)
                row = db.execute("SELECT attempts FROM auth_limits WHERE bucket = ?", (key,)).fetchone()
                if row and row[0] >= limit:
                    blocked = True
                else:
                    db.execute("INSERT INTO auth_limits VALUES (?, 1, ?) "
                               "ON CONFLICT(bucket) DO UPDATE SET attempts=attempts+1", (key, now + 900))
        return blocked

    def login(self, worker_id, password):
        with self.register.connection() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM workers WHERE worker_id = ?", (worker_id,)).fetchone()
        try:
            PASSWORDS.verify(row["password_hash"] if row else DUMMY_HASH, password)
        except (VerificationError, InvalidHashError):
            return None, None
        if not row or row["status"] != "active":
            return (self.profile(row) if row else None), None
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with self.register.connection() as db:
            db.execute("DELETE FROM auth_sessions WHERE expires <= ?", (now,))
            inserted = db.execute("""
                INSERT INTO auth_sessions
                SELECT ?, id, ? FROM workers WHERE id=? AND status='active' AND password_hash=?
            """, (digest(token), now + SESSION_SECONDS, row["id"], row["password_hash"])).rowcount
        return ({**self.profile(row), "expires_at": now + SESSION_SECONDS}, token) if inserted else (None, None)

    def session(self, token):
        if not token or len(token) != 43:
            return None
        with self.register.connection() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("""
                SELECT workers.*, auth_sessions.expires FROM workers JOIN auth_sessions ON auth_sessions.worker = workers.id
                WHERE auth_sessions.digest=? AND auth_sessions.expires>? AND workers.status='active'
            """, (digest(token), int(time.time()))).fetchone()
        return self.profile(row) if row else None

    def logout(self, token):
        if token and len(token) == 43:
            with self.register.connection() as db:
                db.execute("DELETE FROM auth_sessions WHERE digest=?", (digest(token),))

    def request_reset(self, worker_id):
        with self.register.connection() as db:
            db.execute("UPDATE workers SET reset_requested=1 WHERE worker_id=? AND status='active'", (worker_id,))

    def issue_reset(self, worker):
        token = secrets.token_urlsafe(32)
        with self.register.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("UPDATE workers SET reset_requested=0 WHERE id=? AND status='active' AND reset_requested=1",
                              (worker,)).rowcount:
                return None
            db.execute("DELETE FROM password_resets WHERE worker=?", (worker,))
            db.execute("INSERT INTO password_resets VALUES (?, ?, ?)",
                       (digest(token), worker, int(time.time()) + RESET_SECONDS))
        return token

    def reset(self, token, password):
        token_hash = digest(token)
        with self.register.connection() as db:
            found = db.execute("SELECT 1 FROM password_resets WHERE digest=? AND expires>?",
                               (token_hash, int(time.time()))).fetchone()
        if not found:
            return False
        hashed = PASSWORDS.hash(password)
        with self.register.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""
                DELETE FROM password_resets WHERE digest=? AND expires>? RETURNING worker
            """, (token_hash, int(time.time()))).fetchone()
            if not row:
                return False
            worker, = row
            updated = db.execute("UPDATE workers SET password_hash=?, reset_requested=0 WHERE id=? AND status='active'",
                                 (hashed, worker)).rowcount
            db.execute("DELETE FROM auth_sessions WHERE worker=?", (worker,))
            return bool(updated)

    def recover_admin(self, worker_id, password):
        if not 12 <= len(password) <= 128 or not password.strip():
            raise ValueError("Enter a password of 12 to 128 characters.")
        hashed = PASSWORDS.hash(password)
        with self.register.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""
                UPDATE workers SET password_hash=?, reset_requested=0
                WHERE worker_id=? AND role='admin' AND status='active' RETURNING id
            """, (hashed, worker_id.upper())).fetchone()
            if row is None:
                raise ValueError("No active administrator matched that ID.")
            db.execute("DELETE FROM auth_sessions WHERE worker=?", row)
            db.execute("DELETE FROM password_resets WHERE worker=?", row)

    def list_workers(self):
        with self.register.connection() as db:
            db.row_factory = sqlite3.Row
            return [{**self.profile(row), "reset_requested": bool(row["reset_requested"])}
                    for row in db.execute("SELECT * FROM workers ORDER BY worker_id")]

    def manage_worker(self, worker, status, village):
        with self.register.connection() as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM workers WHERE id=? AND role='worker'", (worker,)).fetchone()
            if not row:
                return False
            profile = json.loads(self.register.cipher.decrypt(row["profile"]))
            profile["village"] = village
            db.execute("UPDATE workers SET status=?, profile=? WHERE id=?", (status, self.register.encode(profile), worker))
            if status != "active":
                db.execute("DELETE FROM auth_sessions WHERE worker=?", (worker,))
                db.execute("DELETE FROM password_resets WHERE worker=?", (worker,))
                db.execute("UPDATE workers SET reset_requested=0 WHERE id=?", (worker,))
            return True
