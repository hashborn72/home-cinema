"""Local personal state, independent of provider metadata and player completion."""
import time


class Library:
    def __init__(self,catalog):
        self.catalog=catalog
        with catalog.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS library_flags(
                  profile_id TEXT NOT NULL,content_id TEXT NOT NULL,
                  favorite INTEGER NOT NULL DEFAULT 0,watch_later INTEGER NOT NULL DEFAULT 0,
                  watched INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL,
                  PRIMARY KEY(profile_id,content_id));
                CREATE TABLE IF NOT EXISTS playback_targets(
                  session_id TEXT PRIMARY KEY,release_id TEXT NOT NULL,file_id INTEGER NOT NULL,title TEXT NOT NULL);
            ''')

    def flags(self,cid):
        with self.catalog.db() as db:
            row=db.execute('SELECT favorite,watch_later,watched FROM library_flags WHERE profile_id=? AND content_id=?',('main',cid)).fetchone()
        return {key:bool(row[key]) if row else False for key in ('favorite','watch_later','watched')}

    def update(self,cid,changes):
        if self.catalog.detail(cid) is None: raise KeyError(cid)
        if not changes or any(k not in ('favorite','watch_later','watched') or type(v) is not bool for k,v in changes.items()): raise ValueError('Invalid flags')
        with self.catalog.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO library_flags(profile_id,content_id,updated_at) VALUES (?,?,?)',('main',cid,time.time()))
            for key,value in changes.items():
                db.execute('UPDATE library_flags SET '+key+'=?,updated_at=? WHERE profile_id=? AND content_id=?',(int(value),time.time(),'main',cid))
        return self.flags(cid)

    def shelves(self):
        with self.catalog.db() as db:
            flags=[dict(r) for r in db.execute('SELECT * FROM library_flags WHERE profile_id=? ORDER BY updated_at DESC',('main',))]
            progress=[dict(r) for r in db.execute('''SELECT p.*,t.release_id,t.file_id,t.title AS file_title
                FROM progress p JOIN playback_sessions s ON s.seq=p.last_session_seq
                JOIN playback_targets t ON t.session_id=s.id
                WHERE p.profile_id=? AND (p.position_ms>0 OR p.completed=1) ORDER BY p.updated_at DESC''',('main',))]
        def card(cid):
            result=self.catalog.detail(cid)
            if result:
                result.pop('releases',None)
                result['library']=self.flags(cid)
            return result
        shelves=[]
        for kind,title in [('continue','Продолжить'),('history','История'),('favorite','Избранное'),('watch_later','Посмотреть позже'),('watched','Просмотрено')]:
            cards=[];seen=set()
            rows=progress if kind in ('continue','history') else [r for r in flags if r[kind]]
            for row in rows:
                cid=row['content_id']
                if cid in seen: continue
                item=card(cid)
                if not item: continue
                # Choose latest file per content before filtering completion: do not offer an older episode instead.
                seen.add(cid)
                if kind=='continue' and (row['completed'] or item['library']['watched']): continue
                if kind in ('continue','history'):
                    item['resume_target']={k:row[k] for k in ('release_id','file_id','file_title','position_ms','duration_ms','completed','updated_at')}
                cards.append(item)
                if len(cards)>=100: break
            shelves.append({'id':kind,'title':title,'results':cards})
        return {'shelves':shelves}
