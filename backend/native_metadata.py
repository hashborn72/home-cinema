"""Metadata from the exact release page; fixed hosts and no credential forwarding."""
import html
import json
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import httpx
from anwap import Page
from catalog import fetch, lostfilm_slug


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
    return {'provider': 'ExKinoRay' if 'exkinoray' in urlparse(url).hostname else 'RuTor',
            'url': url, 'title': card['title'], 'description': plain(description[1])[:6000] if description else '',
            'imdb_id': imdb[1] if imdb else None, 'language': 'ru', 'match': 'exact_source_page'}


def lookup(catalog, card):
    with catalog.db() as db:
        rows = [json.loads(r[0]) for r in db.execute('SELECT payload FROM catalog_releases WHERE content_id=?', (card['id'],))]
    rows.sort(key=lambda r: (r.get('seen_at', 0), r.get('published_at', 0)), reverse=True)
    source = card.get('sources', [None])[0]
    if source == 'lostfilm':
        slug = next((lostfilm_slug(r.get('details', '')) for r in rows if lostfilm_slug(r.get('details', ''))), None)
        slug = slug or lostfilm_slug((card.get('metadata') or {}).get('url', ''))
        if not slug: return None
        url = 'https://www.lostfilm.tv/series/'+slug+'/'
        meta = parse_lostfilm(fetch(url, timeout=8, limit=2_000_000).decode('utf-8', 'replace'), url)
        try:
            meta['season_years'] = season_years(fetch(url+'seasons/', timeout=8, limit=2_000_000).decode('utf-8', 'replace'))
        except Exception: pass
        return meta
    for row in rows[:1]:
        p = urlparse(row.get('details', ''))
        if p.scheme not in ('http', 'https') or p.port not in (None, 80, 443) or p.username: return None
        if source == 'rutor' and p.hostname in ('rutor.info', 'rutor.is') and p.path.startswith('/torrent/'):
            url = 'https://'+p.hostname+p.path
            return parse_tracker(fetch(url, timeout=8, limit=2_000_000).decode('utf-8', 'replace'), card, url)
        if source == 'exkinoray' and p.hostname in ('exkinoray.ru', 'www.exkinoray.ru') and p.path == '/details.php':
            fid = parse_qs(p.query).get('id', [''])[0]
            if not fid.isdigit(): return None
            config = Path('/jackett/Jackett/Indexers/exkinoray.json')
            if not config.is_file(): return None
            cookie = next((r.get('value', '') for r in json.loads(config.read_text()) if r.get('id') == 'cookieheader'), '')
            url = 'https://exkinoray.ru/details.php?id='+fid
            with httpx.Client(timeout=8, follow_redirects=False, trust_env=False) as client:
                with client.stream('GET', url, headers={'Cookie': cookie}) as response:
                    if response.status_code != 200: return None
                    body = bytearray()
                    for part in response.iter_bytes():
                        body.extend(part)
                        if len(body) > 2_000_000: raise ValueError('Page too large')
            return parse_tracker(body.decode('utf-8', 'replace'), card, url)
    return None
