"""Anwap series directory and explicit episode pages, using only fixed source paths."""
import re
from catalog import digest
from anwap import BASE, Page, html_page


def series_ids(body):
    return list(dict.fromkeys(int(m[1]) for a in Page(body).links if (m:=re.fullmatch(r'/serials/(\d+)',a['href']))))[:10]


def series_card(body, sid):
    page=Page(body);title=page.heading.strip()
    if page.meta.get('og:url')!=BASE+'/serials/'+str(sid) or not title:raise ValueError('Not a series')
    original=re.search(r' / (.+?) на телефон',page.meta.get('og:title',''))
    year=next((int(m[1]) for a in page.links if (m:=re.fullmatch(r'/serials/god-((?:19|20)\d{2})',a['href']))),None)
    return dict(id=digest('anwap:series:'+str(sid)),title=title,aliases=[original[1]] if original else [],year=year,
                media_type='tv',match='source_series_id',season=None,episode=None,quality=None,
                release_count=0,seeders=None,published_at=0,sources=['anwap'],directory=True,anwap_series_id=sid,
                metadata={'provider':'Anwap','url':BASE+'/serials/'+str(sid),'title':title,
                          'poster':BASE+'/serials/posts/'+str(sid)+'.jpg','description':page.meta.get('og:desc',''),
                          'language':'ru','license':'','rating':None})


def episode_formats(body, eid):
    page=Page(body)
    if page.meta.get('og:type')!='video.episode' or page.meta.get('og:url')!=BASE+'/serials/down/'+str(eid):
        raise ValueError('Not an episode')
    formats=[]
    for a in page.links:
        m=re.fullmatch(r'/serials/load/(mp4|bmp4|hdmp4)/[a-zA-Z0-9]+/'+str(eid),a['href'])
        if not m or 'MP4' not in a['text']:continue
        formats.append(dict(id={'mp4':1,'bmp4':2,'hdmp4':3}[m[1]],path=' '.join(a['text'].split()),
                            load=a['href'],size=None,sample=False))
    if not formats:raise ValueError('No episode video')
    return dict(raw=page.heading.strip(),details=BASE+'/serials/down/'+str(eid),formats=formats)


def expand_series(card):
    sid=int(card['anwap_series_id']);page=Page(html_page('/serials/'+str(sid)))
    rows=[]
    seasons=[a for a in page.links if re.fullmatch(r'/serials/s\d+',a['href'])][:50]
    for season in seasons:
        number=re.search(r'(\d+)\s*Сезон',season['text'],re.I)
        if not number:continue
        for link in Page(html_page(season['href'])).links:
            m=re.fullmatch(r'/serials/down/(\d+)',link['href'])
            ep=re.search(r'(\d+)\s*сери',link['text'],re.I)
            if not m or not ep:continue
            eid=int(m[1]);raw=f"{card['title']} S{int(number[1]):02d}E{int(ep[1]):02d} · {link['text'].strip()}"
            # Negative source refs distinguish episodes from existing positive film IDs, without changing saved film playback keys.
            rows.append(dict(id=digest('anwap:episode:'+str(eid)),source='anwap',raw=raw,categories=[5000],
                             seeders=None,size=None,published_at=0,details=BASE+link['href'],download_url='',
                             film_id=-eid,series_card=card,metadata=card['metadata']))
            if len(rows)>=1000:return rows
    return list({r['id']:r for r in rows}.values())
