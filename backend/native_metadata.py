"""Metadata from the exact release page; fixed hosts and no credential forwarding."""
import html
import json
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urljoin

import httpx
from anwap import Page
from catalog import fetch, lostfilm_slug, merge_native


def plain(value):
    return html.unescape(re.sub('<[^>]+>', ' ', value)).strip()


def parse_lostfilm(body, url):
    page = Page(body)
    poster = re.search(r'/Static/Images/\d+/Posters/poster\.jpg', body)
    premiere = re.search(r'itemprop="dateCreated"\s+content="((?:19|20)\d{2})-', body)
    return {'provider': 'LostFilm', 'url': url, 'title': page.meta.get('og:title') or page.heading,
            'description': plain(page.meta.get('og:description', ''))[:6000],
            'poster': 'https://www.lostfilm.tv'+poster[0] if poster else None,
            'premiere_year': int(premiere[1]) if premiere else None,
            'language': 'ru', 'match': 'exact_source_page'}


def season_years(body):
    result = {}
    for season, section in re.findall(r'<h2>\s*(\d+) сезон</h2>(.*?)(?=<h2>|\Z)', body, re.S):
        year = re.search(r'Год:\s*((?:19|20)\d{2}(?:\s*[-–]\s*(?:19|20)\d{2})?)', section)
        if year: result[season] = year[1]
    return result


def parse_tracker(body, card, url):
    description = re.search(r'(?is)<b>\s*(?:Описание|О фильме|О сериале)\s*:?</b>\s*:?(.*?)(?=<b>|<div|\Z)', body)
    imdb = re.search(r'https?://(?:www\.)?imdb\.com/title/(tt\d+)/', body)
    kinopoisk = re.search(r'''https?://(?:www\.)?kinopoisk\.ru/(?:film/|series/|level/1/film/+)(\d+)(?=[/?#\s"'<>]|$)''', body)
    # The cover precedes the release description; screenshots follow it.
    prefix=body[:description.start()] if description else re.split(r'(?i)Скриншоты|Screenshots',body,maxsplit=1)[0]
    images=Page(prefix).images
    images.sort(key=lambda image:'p200' not in image.get('class','').split())
    poster=next((u for image in images if (u:=urljoin(url,image.get('src',''))) and tracker_poster(u)),None)
    return {'provider': 'ExKinoRay' if 'exkinoray' in urlparse(url).hostname else 'RuTor',
            'url': url, 'title': card['title'], 'description': plain(description[1])[:6000] if description else '',
            'imdb_id': imdb[1] if imdb else None, 'kinopoisk_id': kinopoisk[1] if kinopoisk else None,
            'poster':poster, 'language': 'ru', 'match': 'exact_source_page'}


def tracker_poster(url):
    p=urlparse(url)
    if p.scheme!='https' or p.query or p.fragment or p.username or p.port is not None:return False
    if re.fullmatch(r'i\d+\.fastpic\.org',p.netloc):
        return bool(re.fullmatch(r'/big/\d{4}/\d{4}/[a-f0-9]{2}/[a-f0-9]+\.(?:jpg|jpeg|png|webp)',p.path))
    if p.netloc=='lostpix.com':
        return bool(re.fullmatch(r'/img/\d{4}-\d{2}/\d{2}/[a-z0-9]+\.(?:jpg|jpeg|png|webp)',p.path))
    if p.netloc in ('exkinoray.ru','www.exkinoray.ru'):
        return bool(re.fullmatch(r'/torrents/images/\d+\.(?:jpg|jpeg|png|webp)',p.path))
    if re.fullmatch(r's\d+\.hostingkartinok\.com',p.netloc):
        return bool(re.fullmatch(r'/uploads/images/\d{4}/\d{2}/[a-f0-9]+\.(?:jpg|jpeg|png|webp)',p.path))
    if re.fullmatch(r'i\d+\.imageban\.ru',p.netloc):
        return bool(re.fullmatch(r'/out/\d{4}/\d{2}/\d{2}/[a-f0-9]+\.(?:jpg|jpeg|png|webp)',p.path))
    return False


def lookup(catalog, card):
    with catalog.db() as db:
        rows = [json.loads(r[0]) for r in db.execute('SELECT payload FROM catalog_releases WHERE content_id=?', (card['id'],))]
    rows.sort(key=lambda r: (r.get('seen_at', 0), r.get('published_at', 0)), reverse=True)
    sources=set(card.get('sources',[]))|{r['source'] for r in rows}
    if 'lostfilm' in sources:
        slug = card.get('source_slug') or next((lostfilm_slug(r.get('details', '')) for r in rows if r['source']=='lostfilm' and lostfilm_slug(r.get('details', ''))), None)
        slug = slug or lostfilm_slug((card.get('metadata') or {}).get('url', ''))
        if not slug: return None
        url = 'https://www.lostfilm.tv/series/'+slug+'/'
        meta = parse_lostfilm(fetch(url, timeout=8, limit=2_000_000).decode('utf-8', 'replace'), url)
        try:
            meta['season_years'] = season_years(fetch(url+'seasons/', timeout=8, limit=2_000_000).decode('utf-8', 'replace'))
        except Exception: pass
        return meta
    tracker_meta=card.get('metadata')
    # Series covers come from the actual detail-page image, not a guessed URL convention.
    if card.get('anwap_series_id'):
        from anwap import html_page
        from anwap_series import series_card
        sid=int(card['anwap_series_id'])
        tracker_meta=merge_native(tracker_meta,series_card(html_page('/serials/'+str(sid)),sid)['metadata'])
    for row in rows:
        if row['source']=='anwap':
            tracker_meta=merge_native((row.get('content') or {}).get('metadata') or row.get('metadata'),tracker_meta)
    counts={};seen=set();errors=[];fresh_meta=None
    for row in rows:
        source=row['source']
        if source not in ('rutor','exkinoray') or counts.get(source,0)>=(3 if source=='rutor' else 1):continue
        url=row.get('details','')
        if url in seen:continue
        seen.add(url);counts[source]=counts.get(source,0)+1
        p = urlparse(url)
        if p.scheme not in ('http', 'https') or p.port not in (None, 80, 443) or p.username:continue
        try:
            if source == 'rutor' and p.hostname in ('rutor.info', 'rutor.is') and p.path.startswith('/torrent/'):
                url = 'https://'+p.hostname+p.path
                current=parse_tracker(fetch(url, timeout=8, limit=2_000_000).decode('utf-8', 'replace'), card, url)
                # Rows are newest first. Older releases only fill gaps and add fallback artwork.
                fresh_meta=merge_native(current,fresh_meta)
                # No need to revisit this tracker's other releases after finding a reliable cover.
                if fresh_meta.get('description') and current.get('poster') and 'fastpic.org' not in current['poster']:counts[source]=3
            if source == 'exkinoray' and p.hostname in ('exkinoray.ru', 'www.exkinoray.ru') and p.path == '/details.php':
                fid = parse_qs(p.query).get('id', [''])[0]
                if not fid.isdigit():continue
                config = Path('/jackett/Jackett/Indexers/exkinoray.json')
                if not config.is_file():continue
                cookie = next((r.get('value', '') for r in json.loads(config.read_text()) if r.get('id') == 'cookieheader'), '')
                url = 'https://exkinoray.ru/details.php?id='+fid
                with httpx.Client(timeout=8, follow_redirects=False, trust_env=False) as client:
                    with client.stream('GET', url, headers={'Cookie': cookie}) as response:
                        if response.status_code != 200:continue
                        body = bytearray()
                        for part in response.iter_bytes():
                            body.extend(part)
                            if len(body) > 2_000_000: raise ValueError('Page too large')
                fresh_meta=merge_native(parse_tracker(body.decode('utf-8', 'replace'), card, url),fresh_meta)
        except Exception as error:errors.append(error)
    tracker_meta=merge_native(tracker_meta,fresh_meta)
    if errors and not tracker_meta:raise errors[0]
    return tracker_meta
