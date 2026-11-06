"""Project file progress onto explicit single TV episodes; preserve original records."""
import json
import re


def completed(position, duration, explicit=False):
    # Ten-minute minimum avoids marking trailers/samples watched at a guessed threshold.
    return bool(explicit or (position is not None and duration and duration >= 600000
                             and position >= duration * 0.9))


def episode_identity(release, file):
    if release.get('content', {}).get('media_type') != 'tv' or file.get('sample'):
        return None
    name = file.get('path', '').replace('\\', '/').rsplit('/', 1)[-1]
    match = re.search(r'(?i)(?:^|[^a-z0-9])S(\d{1,2})[ ._-]*E(\d{1,3})(?!\d)', name)
    if not match:
        match = re.search(r'(?i)(?:^|[^0-9])(\d{1,2})x(\d{1,3})(?!\d)', name)
    if match:
        season, episode = map(int, match.groups())
        tail = name[match.end():]
    else:
        ep = re.search(r'(?iu)(?:сери[яи]\s*[:№]?\s*|(?:^|[^a-z0-9])E)(\d{1,3})(?!\d)', name)
        season = release.get('content', {}).get('season') or release.get('season')
        if not ep or season is None:
            # Anwap formats share an explicit episode release, without episode numbers in format names.
            card = release.get('content', {})
            if release.get('source') != 'anwap' or season is None or not card.get('episode'):
                return None
            episode, tail = int(card['episode']), ''
        else:
            episode, tail = int(ep[1]), name[ep.end():]
    # Multi-episode files and unknown numbering never share a progress position.
    if re.match(r'(?i)(?:[ ._-]*E\d{1,3}|\s*[-–+]\s*(?:E|S\d+E)?\d{1,3}(?!\d))', tail):
        return None
    if not (0 <= int(season) <= 99 and 1 <= episode <= 999):
        return None
    return release['content']['id'], int(season), episode


class EpisodeProgress:
    def __init__(self, catalog):
        self.catalog = catalog
        with catalog.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS episode_targets(
                session_id TEXT PRIMARY KEY, content_id TEXT NOT NULL,
                season INTEGER NOT NULL, episode INTEGER NOT NULL)""")
            db.execute('CREATE INDEX IF NOT EXISTS episode_lookup ON episode_targets(content_id,season,episode)')
            old = db.execute("""SELECT t.session_id,t.release_id,t.file_id FROM playback_targets t
                LEFT JOIN episode_targets e ON e.session_id=t.session_id WHERE e.session_id IS NULL""").fetchall()
            for target in old:
                identity = self.identity(target['release_id'], target['file_id'], db)
                if identity:
                    db.execute('INSERT OR IGNORE INTO episode_targets VALUES (?,?,?,?)', (target['session_id'], *identity))

    def identity(self, rid, fid, db=None):
        if db is None:
            with self.catalog.db() as connection:
                return self.identity(rid, fid, connection)
        release = db.execute('SELECT payload FROM catalog_releases WHERE id=?', (rid,)).fetchone()
        prepared = db.execute('SELECT files FROM prepared_releases WHERE release_id=?', (rid,)).fetchone()
        if not release or not prepared:
            return None
        file = next((f for f in json.loads(prepared[0]) if f['id'] == fid), None)
        return episode_identity(json.loads(release[0]), file) if file else None

    def find(self, db, item, rid, fid):
        identity = self.identity(rid, fid, db)
        row = None
        if identity:
            row = db.execute("""SELECT p.* FROM progress p
                JOIN playback_sessions s ON s.seq=p.last_session_seq
                JOIN episode_targets e ON e.session_id=s.id
                WHERE p.profile_id='main' AND e.content_id=? AND e.season=? AND e.episode=?
                ORDER BY p.last_session_seq DESC LIMIT 1""", identity).fetchone()
        if row is None:
            row = db.execute('SELECT * FROM progress WHERE profile_id=? AND content_id=? AND file_key=?',
                             ('main', item['content_id'], item['file_key'])).fetchone()
        if row is None and item['file_key'] == 'anwap:pending':
            row = db.execute("""SELECT p.* FROM progress p
                JOIN playback_sessions s ON s.seq=p.last_session_seq
                JOIN playback_targets t ON t.session_id=s.id
                WHERE p.profile_id='main' AND t.release_id=? AND t.file_id=?
                ORDER BY p.last_session_seq DESC LIMIT 1""", (rid, fid)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result['completed'] = completed(result['position_ms'], result['duration_ms'], result['completed'])
        return result
