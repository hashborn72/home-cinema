"""Optional server-only TMDB lookup. Exact, unambiguous matches; never changes IDs."""
import re
import json
import httpx
from catalog import normal

API = 'https://api.themoviedb.org/3'
NOTICE = 'This product uses the TMDB API but is not endorsed or certified by TMDB.'


def names(card):
    native = card.get('metadata') or {}
    values = [card['title']] + card.get('aliases', []) + [native.get('title'), card.get('provider_title')]
    return list(dict.fromkeys(value for value in values if isinstance(value, str) and value.strip()))


def eligible(card, item):
    if item.get('adult') or type(item.get('id')) is not int or item['id'] <= 0:
        return False
    if card['media_type'] == 'movie':
        return bool(card.get('year') and str(item.get('release_date', ''))[:4] == str(card['year']))
    # A source's actual premiere date can disambiguate TV remakes; a season year cannot.
    premiere = (card.get('metadata') or {}).get('premiere_year')
    return not premiere or str(item.get('first_air_date', ''))[:4] == str(premiere)


def match(card, results):
    kind = card['media_type']
    if kind not in ('movie', 'tv') or card.get('match', card.get('confidence')) == 'unmatched':
        return None
    wanted = {normal(n) for n in names(card)}
    matches = {}
    for item in results:
        if not eligible(card, item):
            continue
        fields = ('title', 'original_title') if kind == 'movie' else ('name', 'original_name')
        available = [item.get(k) or '' for k in fields] + item.get('_verified_names', [])
        if not wanted.intersection(normal(value) for value in available):
            continue
        matches[item['id']] = item
    return next(iter(matches.values())) if len(matches) == 1 else None


def wikidata_claims(entity_id):
    if not re.fullmatch(r'Q[1-9]\d{0,18}', entity_id or ''):
        raise ValueError('Invalid Wikidata identity')
    url = 'https://www.wikidata.org/wiki/Special:EntityData/' + entity_id + '.json'
    # A separate unauthenticated client never forwards TMDB credentials or tracker cookies.
    with httpx.Client(timeout=8, trust_env=False, follow_redirects=False) as client:
        with client.stream('GET', url, headers={'Accept': 'application/json',
                'User-Agent': 'HomeCinema/0.4 (+https://github.com/hashborn72/home-cinema)'}) as response:
            if response.status_code != 200:
                raise ValueError('Wikidata request failed: HTTP ' + str(response.status_code))
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > 2_000_000:
                    raise ValueError('Wikidata response too large')
    entity = json.loads(body).get('entities', {}).get(entity_id) or {}
    if entity.get('id') != entity_id:
        raise ValueError('Unexpected Wikidata identity')
    claims = entity.get('claims') or {}
    return {key: {claim.get('mainsnak', {}).get('datavalue', {}).get('value')
                  for claim in claims.get(key, [])
                  if claim.get('rank') != 'deprecated'
                  and claim.get('mainsnak', {}).get('snaktype') == 'value'
                  and isinstance(claim.get('mainsnak', {}).get('datavalue', {}).get('value'), str)}
            for key in ('P2603', 'P345')}


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

    def search_match(self, card):
        results = []
        for name in names(card)[:3]:
            params = {'query': name, 'language': 'ru-RU', 'include_adult': 'false', 'page': 1}
            if card['media_type'] == 'movie' and card.get('year'):
                params['primary_release_year'] = card['year']
            if card['media_type']=='tv' and (card.get('metadata') or {}).get('premiere_year'):
                params['first_air_date_year']=card['metadata']['premiere_year']
            response = self.request('/search/' + card['media_type'], params)
            # Do not call a result unique when uninspected candidates remain.
            if response.get('total_pages', 1) > 1:
                return None
            results.extend(response.get('results', []))
        item = match(card, results)
        if item:
            return item
        # Search uses aliases that do not appear in its localized title fields.
        # Confirm those aliases against TMDB itself; a single fuzzy result is not evidence.
        candidates = {entry['id']: entry for entry in results if eligible(card, entry)}
        if not candidates or len(candidates) > 8:
            return None
        verified = []
        for entry in candidates.values():
            detail = self.request('/' + card['media_type'] + '/' + str(entry['id']),
                                  {'language': 'en-US', 'append_to_response': 'alternative_titles'})
            if detail.get('id') != entry['id']:
                return None  # A malformed reply leaves a candidate uninspected.
            if not eligible(card, detail):
                continue
            alternatives = detail.get('alternative_titles') or {}
            aliases = [row.get('title') for row in alternatives.get('titles', alternatives.get('results', []))]
            aliases.extend(detail.get(key) for key in ('title', 'original_title', 'name', 'original_name'))
            verified.append(dict(entry, _verified_names=[value for value in aliases if isinstance(value, str)]))
        return match(card, verified)

    def cross_id_match(self, card):
        native = card.get('metadata') or {}
        kinopoisk = native.get('kinopoisk_id')
        if card['media_type'] != 'movie' or not isinstance(kinopoisk, str) or not re.fullmatch(r'[1-9]\d{0,18}', kinopoisk):
            return None
        wanted = {normal(name) for name in names(card)}
        candidates = {}
        for name in names(card)[:3]:
            response = self.request('/search/movie', {'query': name, 'language': 'ru-RU',
                                    'include_adult': 'false', 'page': 1})
            if response.get('total_pages', 1) > 1:
                return None
            for entry in response.get('results', []):
                if entry.get('adult') or type(entry.get('id')) is not int or entry['id'] <= 0:
                    continue
                if wanted.intersection(normal(entry.get(key) or '') for key in ('title', 'original_title')):
                    candidates[entry['id']] = entry
        if not candidates or len(candidates) > 8:
            return None
        matches = {}
        for entry in candidates.values():
            external = self.request('/movie/' + str(entry['id']) + '/external_ids')
            imdb, entity_id = external.get('imdb_id'), external.get('wikidata_id')
            if external.get('id') != entry['id'] or not re.fullmatch(r'tt\d+', imdb or '') or not re.fullmatch(r'Q[1-9]\d{0,18}', entity_id or ''):
                return None  # Missing identity evidence cannot establish uniqueness.
            claims = wikidata_claims(entity_id)
            if claims['P2603'] == {kinopoisk} and claims['P345'] == {imdb}:
                matches[entry['id']] = entry
        return next(iter(matches.values())) if len(matches) == 1 else None

    def lookup(self, card):
        if card['media_type'] not in ('movie', 'tv') or card.get('match', card.get('confidence')) == 'unmatched':
            return None
        native = card.get('metadata') or {}
        kind = card['media_type']
        item = None
        matched_by = 'unique_exact_title_year' if kind == 'movie' else 'unique_exact_title'
        imdb = native.get('imdb_id')
        # Source-page external IDs are stronger than translated titles and tracker release years.
        # Resolve them before any text-search pagination or year filters can reject the card.
        if re.fullmatch(r'tt\d+', imdb or ''):
            exact = self.request('/find/' + imdb, {'external_source': 'imdb_id', 'language': 'ru-RU'})
            candidates = exact.get(kind + '_results', [])
            if not candidates and kind == 'tv' and card.get('year') and not card.get('season') and not card.get('episode'):
                candidates = exact.get('movie_results', [])
                if candidates:
                    kind = 'movie'
            if len(candidates) != 1 or candidates[0].get('adult') or type(candidates[0].get('id')) is not int or candidates[0]['id'] <= 0:
                return None
            item = candidates[0]
            matched_by = 'exact_imdb_id'
        else:
            item = self.search_match(card)
            # Documentary tracker categories can contain standalone films.
            if not item and kind == 'tv' and card.get('year') and not card.get('season') and not card.get('episode'):
                item = self.search_match(dict(card, media_type='movie'))
                if item:
                    kind = 'movie'
            if not item and card['media_type'] == 'movie':
                item = self.cross_id_match(card)
                if item:
                    matched_by = 'exact_cross_id'
        if not item:
            return None
        return self.output(card, item, kind, matched_by)

    def output(self, card, item, kind, matched_by):
        native = card.get('metadata') or {}
        seasons={}
        if kind=='tv' and not native.get('season_years') and card.get('sources')!=['lostfilm']:
            try:
                details=self.request('/tv/'+str(item['id']),{'language':'ru-RU'})
                seasons={str(s['season_number']):s['air_date'][:4] for s in details.get('seasons',[]) if s.get('air_date')}
            except Exception:
                pass  # Optional season dates must not discard an already identified poster.
        if self.image_base is None:
            cfg = self.request('/configuration').get('images', {})
            if cfg.get('secure_base_url') != 'https://image.tmdb.org/t/p/' or 'w500' not in cfg.get('poster_sizes', []):
                raise ValueError('Unexpected TMDB image configuration')
            self.image_base = cfg['secure_base_url'] + 'w500'
        path = item.get('poster_path') or ''
        poster = self.image_base + path if re.fullmatch(r'/[A-Za-z0-9]+\.(?:jpg|png)', path) else None
        backdrop_path = item.get('backdrop_path') or ''
        backdrop = 'https://image.tmdb.org/t/p/w780' + backdrop_path if re.fullmatch(r'/[A-Za-z0-9]+\.(?:jpg|png)', backdrop_path) else None
        rating = item.get('vote_average') if item.get('vote_count', 0) > 0 else None
        return {'provider': 'TMDB', 'provider_id': item['id'],
                'url': 'https://www.themoviedb.org/' + kind + '/' + str(item['id']),
                'title': item.get('title') or item.get('name'), 'description': (item.get('overview') or '')[:6000],
                'original_title': item.get('original_title') or item.get('original_name'),
                'year': (item.get('release_date') or item.get('first_air_date') or '')[:4],
                'poster': poster, 'backdrop': backdrop, 'episode_still': None,
                'rating': rating, 'language': 'ru-RU', 'license': NOTICE,
                'season_years':seasons,
                'match': matched_by}
