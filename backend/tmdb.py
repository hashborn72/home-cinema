"""Optional server-only TMDB lookup. Exact, unambiguous matches; never changes IDs."""
import re
import httpx
from catalog import normal

API = 'https://api.themoviedb.org/3'
NOTICE = 'This product uses the TMDB API but is not endorsed or certified by TMDB.'


def match(card, results):
    kind = card['media_type']
    if kind not in ('movie', 'tv') or card.get('confidence') == 'unmatched':
        return None
    names = {normal(n) for n in [card['title']] + card.get('aliases', [])}
    matches = {}
    for item in results:
        if item.get('adult') or type(item.get('id')) is not int or item['id'] <= 0:
            continue
        fields = ('title', 'original_title') if kind == 'movie' else ('name', 'original_name')
        if not names.intersection(normal(item.get(k) or '') for k in fields):
            continue
        # A film release year can disambiguate remakes; a TV season year cannot.
        if kind == 'movie' and (not card.get('year') or str(item.get('release_date', ''))[:4] != str(card['year'])):
            continue
        matches[item['id']] = item
    return next(iter(matches.values())) if len(matches) == 1 else None


class TMDB:
    def __init__(self, data_dir):
        self.token_path = data_dir / 'tmdb-token'
        self.image_base = None

    def enabled(self):
        return self.token_path.is_file()

    def request(self, path, params=None):
        token = self.token_path.read_text().strip()
        if not token:
            raise ValueError('TMDB token missing')
        # Do not put credentials in URL, cache, exceptions or public models.
        with httpx.Client(timeout=8, trust_env=False, follow_redirects=False) as client:
            with client.stream('GET', API + path, params=params,
                               headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}) as r:
                if r.status_code != 200:
                    raise ValueError('TMDB request failed: HTTP ' + str(r.status_code))
                body = bytearray()
                for part in r.iter_bytes():
                    body.extend(part)
                    if len(body) > 2_000_000:
                        raise ValueError('TMDB response too large')
        import json
        return json.loads(body)

    def lookup(self, card):
        if card['media_type'] not in ('movie', 'tv') or card.get('confidence') == 'unmatched':
            return None
        results = []
        for name in list(dict.fromkeys([card['title']] + card.get('aliases', [])))[:3]:
            params = {'query': name, 'language': 'ru-RU', 'include_adult': 'false', 'page': 1}
            if card['media_type'] == 'movie' and card.get('year'):
                params['primary_release_year'] = card['year']
            response = self.request('/search/' + card['media_type'], params)
            # Do not call a result unique when uninspected candidates remain.
            if response.get('total_pages', 1) > 1:
                return None
            results.extend(response.get('results', []))
        item = match(card, results)
        if not item:
            return None
        if self.image_base is None:
            cfg = self.request('/configuration').get('images', {})
            if cfg.get('secure_base_url') != 'https://image.tmdb.org/t/p/' or 'w500' not in cfg.get('poster_sizes', []):
                raise ValueError('Unexpected TMDB image configuration')
            self.image_base = cfg['secure_base_url'] + 'w500'
        path = item.get('poster_path') or ''
        poster = self.image_base + path if re.fullmatch(r'/[A-Za-z0-9]+\.(?:jpg|png)', path) else None
        rating = item.get('vote_average') if item.get('vote_count', 0) > 0 else None
        return {'provider': 'TMDB', 'provider_id': item['id'],
                'url': 'https://www.themoviedb.org/' + card['media_type'] + '/' + str(item['id']),
                'title': item.get('title') or item.get('name'), 'description': (item.get('overview') or '')[:6000],
                'original_title': item.get('original_title') or item.get('original_name'),
                'year': (item.get('release_date') or item.get('first_air_date') or '')[:4],
                'poster': poster, 'rating': rating, 'language': 'ru-RU', 'license': NOTICE,
                'match': 'unique_exact_title_year' if card['media_type'] == 'movie' else 'unique_exact_title'}
