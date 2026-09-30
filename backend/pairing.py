"""LAN device enrollment: expiring one-use codes, hashed device credentials."""
import hashlib
import secrets
import sqlite3
import time
from contextlib import contextmanager


class Pairing:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS pairing_codes (
                digest TEXT PRIMARY KEY, expires REAL NOT NULL, label TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS paired_devices (
                digest TEXT PRIMARY KEY, label TEXT NOT NULL, created REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS pairing_limits (
                bucket INTEGER PRIMARY KEY, attempts INTEGER NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db: yield db
        finally:
            db.close()

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def issue(self, label, ttl=86400):
        code = ''.join(secrets.choice('0123456789') for _ in range(12))
        with self.connect() as db:
            db.execute('DELETE FROM pairing_codes WHERE expires <= ?', (time.time(),))
            db.execute('INSERT INTO pairing_codes VALUES (?,?,?)',
                       (self.digest(code), time.time() + ttl, label[:80]))
        return code

    def redeem(self, code):
        now = time.time()
        bucket = int(now // 60)
        credential = secrets.token_urlsafe(32)
        # Commit failed attempts too; BEGIN IMMEDIATE makes redemption atomic.
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM pairing_limits WHERE bucket < ?', (bucket,))
            db.execute('INSERT OR IGNORE INTO pairing_limits VALUES (?,0)', (bucket,))
            attempts = db.execute('SELECT attempts FROM pairing_limits WHERE bucket=?', (bucket,)).fetchone()[0]
            if attempts >= 10:
                raise RuntimeError('Try again in one minute')
            db.execute('UPDATE pairing_limits SET attempts=attempts+1 WHERE bucket=?', (bucket,))
            row = db.execute('SELECT label FROM pairing_codes WHERE digest=? AND expires>?',
                             (self.digest(code), now)).fetchone()
            if row:
                db.execute('DELETE FROM pairing_codes WHERE digest=?', (self.digest(code),))
                db.execute('INSERT INTO paired_devices VALUES (?,?,?)',
                           (self.digest(credential), row[0], now))
        if not row:
            raise ValueError('Invalid or expired code')
        return credential

    def authorized(self, credential):
        if not credential or len(credential) > 200:
            return False
        with self.connect() as db:
            return db.execute('SELECT 1 FROM paired_devices WHERE digest=?',
                              (self.digest(credential),)).fetchone() is not None
