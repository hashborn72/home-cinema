"""One low-priority worker: persistent interests, warm catalogues and series, no video downloads."""
import json
import threading
import time


class Indexer:
    def __init__(self, catalog, providers, metadata):
        self.catalog, self.providers, self.metadata = catalog, providers, metadata
        self.stop_event = threading.Event()
        self.last_success = None
        self.last_error = None
        self.cursor = 0
        with catalog.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS index_interests(content_id TEXT PRIMARY KEY,touched_at REAL NOT NULL)')

    def touch(self, cards):
        self.metadata.prioritize(cards)
        with self.catalog.db() as db:
            for card in reversed(cards[:40]):
                if card.get('media_type') == 'tv':
                    db.execute('INSERT OR REPLACE INTO index_interests VALUES (?,?)', (card['id'], time.time()))
            db.execute('DELETE FROM index_interests WHERE content_id NOT IN (SELECT content_id FROM index_interests ORDER BY touched_at DESC LIMIT 120)')

    def candidates(self):
        # Alternate providers and series so a large series archive cannot starve the other sources.
        jobs = [(s, '', 0, '') for s in ('lostfilm', 'exkinoray', 'rutor', 'anwap')]
        with self.catalog.db() as db:
            recent = [r[0] for r in db.execute('SELECT content_id FROM index_interests ORDER BY touched_at DESC LIMIT 40')]
        home = self.catalog.home()['shelves']
        ids = list(dict.fromkeys(recent + [c['id'] for row in home for c in row['results'][:8] if c['media_type'] == 'tv']))
        for cid in ids:
            card = self.catalog.detail(cid)
            if card and card.get('sources'):
                jobs.append((card['sources'][0], card['title'][:120], 0, cid))
        # Cache the next LostFilm directory pages gradually, not all thousands of releases at once.
        jobs.extend(('lostfilm', '', offset, '') for offset in (20, 40, 60, 80))
        return jobs

    def tick(self):
        jobs=self.candidates()
        for delta in range(len(jobs)):
            position=(self.cursor+delta)%len(jobs)
            source, query, offset, cid=jobs[position]
            old = self.providers.state(source, query, offset, cid)
            # Series renew every 30 minutes; directory pages every 10 minutes. Retry failures after 5m.
            ttl = (1800 if cid else 600) if old['status'] == 'ready' else 300
            if time.time() - old.get('updated_at', 0) < ttl:
                continue
            key = self.providers.key(source, query, offset, cid)
            with self.providers.lock:
                # Keep an interactive slot free. Background work never queues behind itself.
                if self.providers.active:
                    return
                self.providers.active.add(key)
            self.cursor=(position+1)%len(jobs)
            self.providers.run(key, source, query, offset, cid)
            state = self.providers.state(source, query, offset, cid)
            if state['status'] == 'ready':
                self.metadata.prioritize(state.get('results', [])[:20])
                self.last_success = time.time()
                self.last_error = None
            else:
                self.last_error = 'A source is unavailable; cached data retained'
            return

    def start(self):
        def run():
            while not self.stop_event.wait(5):
                try: self.tick()
                except Exception: self.last_error = 'Indexing will retry'
        threading.Thread(target=run, name='catalog-index', daemon=True).start()

    def stop(self):
        self.stop_event.set()
