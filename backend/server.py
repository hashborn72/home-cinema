"""Development milestone: authenticated, durable playback session ledger."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

CONTENT_ID = 'probe:big-buck-bunny'
FILE_KEY = 'big-buck-bunny:sample:v1'
STREAM_URL = 'http://192.168.0.221:18093/probe-media'

class Start(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start_position_ms: StrictInt = Field(default=0, ge=0, le=2_147_483_647)

class Result(BaseModel):
    model_config = ConfigDict(extra='forbid')
    event_id: uuid.UUID
    session_id: str | None = None
    result_ok: StrictBool
    position_ms: StrictInt | None = Field(default=None, ge=0, le=2_147_483_647)
    duration_ms: StrictInt | None = Field(default=None, gt=0, le=2_147_483_647)
    end_by: str | None = None

def create_app(data_dir: Path, test_token: str | None = None):
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    token_file = data_dir / 'device-token'
    if test_token is None and not token_file.exists():
        fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as out:
            out.write(secrets.token_urlsafe(32))
    token = test_token or token_file.read_text().strip()
    db_path = data_dir / 'library.sqlite3'

    @contextmanager
    def db():
        connection = sqlite3.connect(db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    with db() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.executescript('''
          CREATE TABLE IF NOT EXISTS playback_sessions (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
            profile_id TEXT NOT NULL DEFAULT 'main', content_id TEXT NOT NULL,
            file_key TEXT NOT NULL, started_at REAL NOT NULL, start_position_ms INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS playback_results (
            event_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES playback_sessions(id),
            received_at REAL NOT NULL, payload TEXT NOT NULL, fingerprint TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS progress (
            profile_id TEXT NOT NULL, content_id TEXT NOT NULL, file_key TEXT NOT NULL,
            position_ms INTEGER, duration_ms INTEGER, last_session_seq INTEGER NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
            PRIMARY KEY(profile_id, content_id, file_key));
          PRAGMA user_version=1;
        ''')
    os.chmod(db_path, 0o600)
    app = FastAPI(title='Home Cinema development backend', docs_url=None, redoc_url=None, openapi_url=None)

    def auth(authorization: str = Header(default='')):
        if not hmac.compare_digest(authorization.encode(), ('Bearer ' + token).encode()):
            raise HTTPException(401, 'Device authentication required')

    @app.get('/health')
    def health():
        return {'status': 'ok', 'version': '0.1.0-probe', 'environment': 'development'}

    @app.get('/probe-media', include_in_schema=False)
    def probe_media():
        # Public CC-BY sample only; never expose the data directory or a user-supplied path.
        media = data_dir / 'BigBuckBunny_320x180.mp4'
        if not media.is_file():
            raise HTTPException(404, 'Development sample not installed')
        return FileResponse(media, media_type='video/mp4')

    @app.get('/api/v1/probe', dependencies=[Depends(auth)])
    def probe():
        with db() as conn:
            row = conn.execute('SELECT * FROM progress WHERE profile_id=? AND content_id=? AND file_key=?', ('main', CONTENT_ID, FILE_KEY)).fetchone()
        return {'content_id': CONTENT_ID, 'file_key': FILE_KEY, 'title': 'Big Buck Bunny',
                'stream_url': STREAM_URL, 'position_ms': row['position_ms'] if row else None,
                'duration_ms': row['duration_ms'] if row else None,
                'completed': bool(row['completed']) if row else False}

    @app.post('/api/v1/playback/sessions', dependencies=[Depends(auth)])
    def start(body: Start):
        session_id = str(uuid.uuid4())
        with db() as conn:
            conn.execute('INSERT INTO playback_sessions(id,content_id,file_key,started_at,start_position_ms) VALUES (?,?,?,?,?)',
                         (session_id, CONTENT_ID, FILE_KEY, time.time(), body.start_position_ms))
        return {'id': session_id, 'content_id': CONTENT_ID, 'file_key': FILE_KEY}

    @app.post('/api/v1/playback/sessions/{session_id}/result', dependencies=[Depends(auth)])
    def result(session_id: uuid.UUID, body: Result):
        session_id = str(session_id)
        if body.session_id is not None and body.session_id != session_id:
            raise HTTPException(422, 'Session mismatch')
        if body.end_by not in (None, 'user', 'playback_completion'):
            raise HTTPException(422, 'Unknown player result')
        if body.position_ms is not None and body.duration_ms is not None and body.position_ms > body.duration_ms:
            raise HTTPException(422, 'Position exceeds duration')
        payload = body.model_dump_json()
        fingerprint = hashlib.sha256((session_id + payload).encode()).hexdigest()
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            existing = conn.execute('SELECT fingerprint FROM playback_results WHERE event_id=?', (str(body.event_id),)).fetchone()
            if existing:
                if existing['fingerprint'] != fingerprint:
                    raise HTTPException(409, 'Event id already used with different content')
                return {'saved': True, 'duplicate': True}
            session = conn.execute('SELECT * FROM playback_sessions WHERE id=?', (session_id,)).fetchone()
            if session is None:
                raise HTTPException(404, 'Session not found')
            conn.execute('INSERT INTO playback_results VALUES (?,?,?,?,?)', (str(body.event_id), session_id, time.time(), payload, fingerprint))
            known_completion = body.result_ok and body.end_by == 'playback_completion'
            if body.result_ok and (body.position_ms is not None or known_completion):
                # Do not replace known position with null, nor newer playback with a delayed old result.
                conn.execute('''INSERT INTO progress VALUES (?,?,?,?,?,?,?,?)
                  ON CONFLICT(profile_id,content_id,file_key) DO UPDATE SET
                    position_ms=COALESCE(excluded.position_ms,progress.position_ms),
                    duration_ms=COALESCE(excluded.duration_ms,progress.duration_ms),
                    last_session_seq=excluded.last_session_seq, completed=excluded.completed, updated_at=excluded.updated_at
                  WHERE excluded.last_session_seq >= progress.last_session_seq''',
                  (session['profile_id'], session['content_id'], session['file_key'], body.position_ms,
                   body.duration_ms, session['seq'], int(known_completion), time.time()))
        return {'saved': True, 'duplicate': False}

    @app.get('/api/v1/playback/diagnostics', dependencies=[Depends(auth)])
    def diagnostics():
        with db() as conn:
            sessions = [dict(r) for r in conn.execute('SELECT * FROM playback_sessions ORDER BY seq DESC LIMIT 20')]
            results = [dict(r) for r in conn.execute('SELECT session_id,received_at,payload FROM playback_results ORDER BY received_at DESC LIMIT 20')]
        return {'sessions': sessions, 'results': results}

    return app

def production_app():
    return create_app(Path(os.environ.get('CINEMA_DATA', '/data')))
