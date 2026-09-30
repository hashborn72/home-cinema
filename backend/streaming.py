"""Capability-scoped, range-preserving relay for IP-bound Anwap URLs; no disk media cache."""
import secrets
import time
import re
import os
import httpx
from fastapi import HTTPException,Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from anwap import HEADERS,allowed_media,resolve,BASE
from settings import public_url

class Streams:
    def __init__(self,catalog):
        self.catalog=catalog
        with catalog.db() as db:db.execute('CREATE TABLE IF NOT EXISTS stream_tickets(ticket TEXT PRIMARY KEY,film_id INTEGER NOT NULL,format_id INTEGER NOT NULL,file_key TEXT NOT NULL,url TEXT NOT NULL,refreshed_at REAL NOT NULL,expires_at REAL NOT NULL)')
    def ticket(self,film_id,format_id,item):
        token=secrets.token_urlsafe(32);now=time.time()
        if not allowed_media(item['stream_url']):raise ValueError('Untrusted stream')
        with self.catalog.db() as db:
            db.execute('DELETE FROM stream_tickets WHERE expires_at<?',(now,))
            db.execute('INSERT INTO stream_tickets VALUES (?,?,?,?,?,?,?)',(token,film_id,format_id,item['file_key'],item['stream_url'],now,now+43200))
        return public_url()+'/play/'+token
    async def stream(self,token,request:Request):
        with self.catalog.db() as db:row=db.execute('SELECT * FROM stream_tickets WHERE ticket=? AND expires_at>?',(token,time.time())).fetchone()
        if not row:raise HTTPException(404,'Playback link expired')
        byte_range=request.headers.get('range')
        if byte_range and not re.fullmatch(r'bytes=(?:\d+-\d*|-\d+)',byte_range):raise HTTPException(416,'Single byte range required')
        url=row['url']
        if time.time()-row['refreshed_at']>900:
            try:fresh=await run_in_threadpool(resolve,row['film_id'],row['format_id'])
            except Exception:raise HTTPException(502,'Source unavailable')
            if fresh['file_key']!=row['file_key']:raise HTTPException(409,'Source file changed; reopen the release')
            url=fresh['stream_url']
            with self.catalog.db() as db:db.execute('UPDATE stream_tickets SET url=?,refreshed_at=? WHERE ticket=?',(url,time.time(),token))
        if not allowed_media(url):raise HTTPException(502,'Invalid source')
        client=httpx.AsyncClient(timeout=httpx.Timeout(20,connect=8),follow_redirects=False,trust_env=False)
        headers=dict(HEADERS,Referer=BASE+'/films/'+str(row['film_id']))
        if byte_range:headers['Range']=byte_range
        try:
            upstream=await client.send(client.build_request('GET',url,headers=headers),stream=True)
            if upstream.status_code not in (200,206) or not upstream.headers.get('content-type','').startswith(('video/','application/octet-stream')):
                await upstream.aclose();await client.aclose();raise HTTPException(502,'Source did not return video')
        except HTTPException:raise
        except Exception:
            await client.aclose();raise HTTPException(502,'Source unavailable')
        response_headers={k:v for k,v in upstream.headers.items() if k.lower() in ('content-length','content-range','accept-ranges','content-type')}
        response_headers['Cache-Control']='no-store'
        async def chunks():
            try:
                async for chunk in upstream.aiter_bytes(chunk_size=65536):yield chunk
            finally:
                await upstream.aclose();await client.aclose()
        return StreamingResponse(chunks(),status_code=upstream.status_code,headers=response_headers)
