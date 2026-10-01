"""RuTor's two movie-category top tables; no per-torrent requests or arbitrary URLs."""
import base64
from datetime import datetime, timezone
from decimal import Decimal
from html.parser import HTMLParser
import re
from urllib.parse import parse_qs, urljoin, urlparse

from catalog import digest

BASE = 'https://rutor.info'
CATEGORIES = {'/kino': 'kino', '/nashe_kino': 'nashe_kino'}
MONTHS = dict(zip(('янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'), range(1, 13)))


def source_path(href):
    parsed = urlparse(urljoin(BASE, href))
    if (parsed.scheme not in ('http', 'https') or parsed.hostname not in ('rutor.info', 'rutor.is')
            or parsed.username or parsed.password or parsed.port is not None or parsed.query or parsed.fragment):
        return None
    return parsed.path


def magnet(link):
    parsed = urlparse(link)
    if parsed.scheme != 'magnet' or parsed.netloc or parsed.path or parsed.fragment:
        raise ValueError('Invalid RuTor magnet')
    hashes = parse_qs(parsed.query).get('xt', [])
    if len(hashes) != 1 or not re.fullmatch(r'urn:btih:(?:[a-fA-F0-9]{40}|[a-zA-Z2-7]{32})', hashes[0]):
        raise ValueError('Invalid RuTor infohash')
    value = hashes[0][9:]
    if len(value) == 32:
        value = base64.b32decode(value.upper()).hex()
    return 'magnet:?xt=urn:btih:' + value.lower()


def publication(value):
    found = re.fullmatch(r'(\d{1,2})\s+([А-Яа-яЁё]+)\s+(\d{2}|\d{4})', value.strip())
    if not found or found[2].casefold() not in MONTHS:
        raise ValueError('Invalid RuTor publication date')
    year = int(found[3]) + (2000 if len(found[3]) == 2 else 0)
    return int(datetime(year, MONTHS[found[2].casefold()], int(found[1]), tzinfo=timezone.utc).timestamp())


def size_bytes(value):
    found = re.fullmatch(r'(\d+(?:[.,]\d+)?)\s*(B|KB|MB|GB|TB|Б|КБ|МБ|ГБ|ТБ)', value.strip(), re.I)
    if not found:
        return None
    power = {'B': 0, 'Б': 0, 'KB': 1, 'КБ': 1, 'MB': 2, 'МБ': 2, 'GB': 3, 'ГБ': 3, 'TB': 4, 'ТБ': 4}[found[2].upper()]
    return int(Decimal(found[1].replace(',', '.')) * (1024 ** power))


class TopParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.heading = None
        self.heading_links = []
        self.pending = None
        self.active = None
        self.sections = {}
        self.row = None
        self.cell = None
        self.anchor = None
        self.green_depth = 0
        self.rows = []

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag == 'h2':
            if self.active or self.heading is not None:
                raise ValueError('Malformed RuTor category heading')
            self.heading = []
            self.heading_links = []
            self.pending = None
        elif tag == 'a' and self.heading is not None:
            path = source_path(attr.get('href', ''))
            if path in CATEGORIES:
                self.heading_links.append(path)
        if tag == 'table':
            if self.active:
                raise ValueError('Nested RuTor top table')
            if self.pending:
                if self.pending in self.sections:
                    raise ValueError('Duplicate RuTor category section')
                self.active = self.pending
                self.sections[self.active] = 0
                self.pending = None
        if not self.active:
            return
        if tag == 'tr':
            if self.row is not None:
                raise ValueError('Unclosed RuTor row')
            self.row = {'header': 'backgr' in attr.get('class', '').split(), 'cells': [], 'links': [], 'seeders': []}
        elif tag == 'td' and self.row is not None:
            if self.cell is not None:
                raise ValueError('Unclosed RuTor cell')
            self.cell = []
        elif tag == 'a' and self.row is not None:
            if self.anchor is not None:
                raise ValueError('Nested RuTor link')
            self.anchor = {'href': attr.get('href', ''), 'text': []}
        elif tag == 'span' and self.row is not None:
            if self.green_depth:
                self.green_depth += 1
            elif 'green' in attr.get('class', '').split():
                self.green_depth = 1

    def handle_data(self, data):
        if self.heading is not None:
            self.heading.append(data)
        if self.cell is not None:
            self.cell.append(data)
        if self.anchor is not None:
            self.anchor['text'].append(data)
        if self.row is not None and self.green_depth:
            self.row['seeders'].append(data)

    def handle_endtag(self, tag):
        if tag == 'h2' and self.heading is not None:
            text = ' '.join(''.join(self.heading).split()).casefold()
            if text.startswith('самые популярные торренты в категории') and len(self.heading_links) == 1:
                self.pending = CATEGORIES[self.heading_links[0]]
            self.heading = None
        if not self.active:
            return
        if tag == 'a' and self.anchor is not None:
            self.row['links'].append(self.anchor)
            self.anchor = None
        elif tag == 'span' and self.green_depth:
            self.green_depth -= 1
        elif tag == 'td' and self.cell is not None:
            self.row['cells'].append(' '.join(''.join(self.cell).split()))
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            if self.cell is not None or self.anchor is not None or self.green_depth:
                raise ValueError('Malformed RuTor row')
            if not self.row['header']:
                self.rows.append(self.release(self.row))
                self.sections[self.active] += 1
                if len(self.rows) > 200:
                    raise ValueError('RuTor top row limit exceeded')
            self.row = None
        elif tag == 'table':
            if self.row is not None:
                raise ValueError('Unclosed RuTor row')
            self.active = None

    def release(self, row):
        titles = []
        magnets = []
        for link in row['links']:
            href = link['href']
            if href.startswith('magnet:'):
                magnets.append(magnet(href))
                continue
            path = source_path(href)
            found = re.fullmatch(r'/torrent/([1-9]\d*)/([A-Za-z0-9_-]+)', path or '')
            if found:
                title = ' '.join(''.join(link['text']).split())
                if title:
                    titles.append((found[1], BASE + path, title))
        titles = list(dict.fromkeys(titles))
        magnets = list(dict.fromkeys(magnets))
        sizes = [size_bytes(cell) for cell in row['cells']]
        sizes = [value for value in sizes if value is not None]
        seeders = ''.join(row['seeders']).strip().replace('\xa0', '').replace(' ', '')
        if len(titles) != 1 or len(magnets) != 1 or len(sizes) != 1 or not re.fullmatch(r'\d+', seeders) or not row['cells']:
            raise ValueError('Incomplete RuTor movie top row')
        tid, url, title = titles[0]
        if len(title) > 1000:
            raise ValueError('RuTor title too long')
        return {'id': digest('rutor:' + url), 'source': 'rutor', 'raw': title, 'categories': [2000],
                'kind': 'movie', 'seeders': int(seeders), 'size': sizes[0],
                'published_at': publication(row['cells'][0]), 'details': url, 'download_url': magnets[0],
                'home_category': self.active, 'home_rank': self.sections[self.active], 'rutor_id': tid}


def top_rows(body):
    if len(body) > 6_000_000:
        raise ValueError('RuTor top page too large')
    if isinstance(body, bytes):
        body = body.decode('utf-8')
    parser = TopParser()
    parser.feed(body)
    parser.close()
    if parser.active or parser.row or set(parser.sections) != set(CATEGORIES.values()) or not all(parser.sections.values()):
        raise ValueError('Missing or incomplete RuTor movie top sections')
    unique = {}
    for row in parser.rows:
        previous = unique.get(row['rutor_id'])
        if previous and previous['download_url'] != row['download_url']:
            raise ValueError('Conflicting RuTor torrent identities')
        unique.setdefault(row['rutor_id'], row)
    return list(unique.values())


def fetch_top():
    import httpx
    with httpx.Client(timeout=20, follow_redirects=False, trust_env=False,
                      headers={'User-Agent': 'Mozilla/5.0 (compatible; HomeCinema)'}) as client:
        with client.stream('GET', BASE + '/top') as response:
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > 6_000_000:
                    raise ValueError('RuTor top page too large')
    return top_rows(bytes(body))
