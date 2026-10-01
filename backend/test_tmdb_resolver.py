import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx

from tmdb import TMDB, match, wikidata_claims


class TMDBResolverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.api = TMDB(Path(self.temp.name))
        self.api.image_base = 'https://image.tmdb.org/t/p/w500'
        self.card = {'title': 'Distribution Name', 'aliases': [], 'media_type': 'tv',
                     'sources': ['lostfilm'], 'match': 'source_series_title',
                     'metadata': {'title': 'Название источника', 'premiere_year': 2025}}
        self.show = {'id': 7, 'name': 'Название TMDB', 'original_name': '原題',
                     'first_air_date': '2025-02-01', 'poster_path': '/portrait.jpg',
                     'overview': 'Описание', 'vote_count': 1}

    def tearDown(self):
        self.temp.cleanup()

    def request_with_aliases(self, path, params=None):
        if path.startswith('/search/'):
            self.assertEqual(params['first_air_date_year'], 2025)
            return {'results': [self.show], 'total_pages': 1}
        self.assertEqual(path, '/tv/7')
        self.assertEqual(params['append_to_response'], 'alternative_titles')
        return dict(self.show, name='English Localized Name', alternative_titles={'results': [
            {'iso_3166_1': 'US', 'title': 'Distribution Name'}]})

    def test_official_distribution_alias_restores_localized_poster(self):
        with patch.object(self.api, 'request', side_effect=self.request_with_aliases):
            result = self.api.lookup(self.card)
        self.assertEqual(result['provider_id'], 7)
        self.assertEqual(result['title'], 'Название TMDB')
        self.assertEqual(result['poster'], 'https://image.tmdb.org/t/p/w500/portrait.jpg')

    def test_native_title_and_provider_title_are_matching_evidence(self):
        self.assertEqual(match(self.card, [dict(self.show, name='Название источника')])['id'], 7)
        card = dict(self.card, provider_title='Provider Title')
        self.assertEqual(match(card, [dict(self.show, name='Provider Title')])['id'], 7)

    def test_single_fuzzy_result_does_not_become_exact_match(self):
        def request(path, params=None):
            if path.startswith('/search/'):
                return {'results': [self.show], 'total_pages': 1}
            return dict(self.show, alternative_titles={'results': [{'title': 'A Different Show'}]})
        with patch.object(self.api, 'request', side_effect=request):
            self.assertIsNone(self.api.lookup(self.card))

    def test_official_aliases_preserve_remake_ambiguity(self):
        other = dict(self.show, id=8)
        def request(path, params=None):
            if path.startswith('/search/'):
                return {'results': [self.show, other], 'total_pages': 1}
            return dict(self.show, id=int(path.rsplit('/', 1)[1]), alternative_titles={
                'results': [{'title': 'Distribution Name'}]})
        with patch.object(self.api, 'request', side_effect=request):
            self.assertIsNone(self.api.lookup(self.card))

    def test_aliases_do_not_bypass_known_premiere(self):
        with patch.object(self.api, 'request', return_value={
                'results': [dict(self.show, first_air_date='2024-02-01')], 'total_pages': 1}) as request:
            self.assertIsNone(self.api.lookup(self.card))
        self.assertTrue(all(call.args[0].startswith('/search/') for call in request.call_args_list))

    def test_uninspected_alias_candidates_fail_closed(self):
        shows = [dict(self.show, id=i) for i in range(1, 10)]
        with patch.object(self.api, 'request', return_value={'results': shows, 'total_pages': 1}) as request:
            self.assertIsNone(self.api.lookup(self.card))
        self.assertTrue(all(call.args[0].startswith('/search/') for call in request.call_args_list))

    def test_alias_request_failure_never_selects_from_partial_evidence(self):
        def request(path, params=None):
            if path.startswith('/search/'):
                return {'results': [self.show, dict(self.show, id=8)], 'total_pages': 1}
            if path == '/tv/8':
                raise TimeoutError()
            return dict(self.show, alternative_titles={'results': [{'title': 'Distribution Name'}]})
        with patch.object(self.api, 'request', side_effect=request):
            with self.assertRaises(TimeoutError):
                self.api.lookup(self.card)

    def test_external_identity_precedes_wrong_release_year_and_search_pagination(self):
        card = {'title': 'Common Title', 'media_type': 'movie', 'year': 2025,
                'match': 'title_year', 'metadata': {'imdb_id': 'tt12345', 'match': 'exact_source_page'}}
        film = {'id': 9, 'title': 'Localized Title', 'release_date': '2026-03-01', 'poster_path': '/portrait.jpg'}
        def request(path, params=None):
            self.assertEqual(path, '/find/tt12345')
            self.assertEqual(params['external_source'], 'imdb_id')
            return {'movie_results': [film]}
        with patch.object(self.api, 'request', side_effect=request):
            result = self.api.lookup(card)
        self.assertEqual(result['provider_id'], 9)
        self.assertEqual(result['year'], '2026')
        self.assertEqual(result['match'], 'exact_imdb_id')

    def test_external_ambiguity_and_adult_results_fail_closed(self):
        card = dict(self.card, metadata={'imdb_id': 'tt12345'})
        for results in ([], [self.show, dict(self.show, id=8)], [dict(self.show, adult=True)]):
            with self.subTest(results=results):
                with patch.object(self.api, 'request', return_value={'tv_results': results}) as request:
                    self.assertIsNone(self.api.lookup(card))
                self.assertEqual(request.call_count, 1)

    def test_external_identity_does_not_override_collection_protection(self):
        card = dict(self.card, match='unmatched', metadata={'imdb_id': 'tt12345'})
        with patch.object(self.api, 'request') as request:
            self.assertIsNone(self.api.lookup(card))
        request.assert_not_called()

    def test_native_tv_premiere_narrows_common_name_search_before_page_check(self):
        card = dict(self.card, title='Doc', sources=['anwap'], metadata={'premiere_year': 2025})
        show = dict(self.show, name='Doc')
        def request(path, params=None):
            if path.startswith('/search/'):
                self.assertEqual(params['first_air_date_year'], 2025)
                return {'results': [show], 'total_pages': 1}
            return dict(show, seasons=[])
        with patch.object(self.api, 'request', side_effect=request):
            self.assertEqual(self.api.lookup(card)['provider_id'], 7)

    def test_optional_season_details_failure_preserves_found_poster(self):
        card = dict(self.card, title='Doc', sources=['anwap'])
        show = dict(self.show, name='Doc')
        def request(path, params=None):
            if path.startswith('/search/'):
                return {'results': [show], 'total_pages': 1}
            raise TimeoutError()
        with patch.object(self.api, 'request', side_effect=request):
            result = self.api.lookup(card)
        self.assertEqual(result['poster'], 'https://image.tmdb.org/t/p/w500/portrait.jpg')
        self.assertEqual(result['season_years'], {})


class TMDBCrossIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.api = TMDB(Path(self.temp.name))
        self.api.image_base = 'https://image.tmdb.org/t/p/w500'
        self.card = {'title': 'Shared Film Title', 'aliases': [], 'media_type': 'movie',
                     'year': 1964, 'match': 'title_year', 'metadata': {'kinopoisk_id': '321'}}
        self.film = {'id': 7, 'title': 'Shared Film Title', 'original_title': 'Original Title',
                     'release_date': '1965-02-01', 'poster_path': '/portrait.jpg'}

    def tearDown(self):
        self.temp.cleanup()

    def request(self, path, params=None):
        if path == '/search/movie':
            return {'results': [] if params.get('primary_release_year') else [self.film], 'total_pages': 1}
        self.assertEqual(path, '/movie/7/external_ids')
        return {'id': 7, 'imdb_id': 'tt987', 'wikidata_id': 'Q77'}

    def test_exact_external_identity_resolves_different_release_year(self):
        with patch.object(self.api, 'request', side_effect=self.request), patch('tmdb.wikidata_claims', return_value={
                'P2603': {'321'}, 'P345': {'tt987'}}):
            result = self.api.lookup(self.card)
        self.assertEqual(result['provider_id'], 7)
        self.assertEqual(result['year'], '1965')
        self.assertEqual(result['match'], 'exact_cross_id')

    def test_neither_year_proximity_nor_title_alone_can_establish_identity(self):
        for claims in ({'P2603': {'999'}, 'P345': {'tt987'}}, {'P2603': {'321'}, 'P345': {'tt999'}},
                       {'P2603': {'321', '999'}, 'P345': {'tt987'}}, {'P2603': {'321'}, 'P345': {'tt987', 'tt999'}}):
            with self.subTest(claims=claims):
                with patch.object(self.api, 'request', side_effect=self.request), patch('tmdb.wikidata_claims', return_value=claims):
                    self.assertIsNone(self.api.lookup(self.card))

    def test_nonexact_titles_and_adult_candidates_never_use_cross_ids(self):
        original_film = dict(self.film)
        for change in ({'title': 'Different Title'}, {'adult': True}):
            with self.subTest(change=change):
                self.film = dict(original_film, **change)
                with patch.object(self.api, 'request', side_effect=self.request), patch('tmdb.wikidata_claims') as claims:
                    self.assertIsNone(self.api.lookup(self.card))
                claims.assert_not_called()

    def test_successful_regular_match_never_rechecks_cross_ids(self):
        film = dict(self.film, release_date='1964-02-01')
        with patch.object(self.api, 'request', return_value={'results': [film], 'total_pages': 1}) as request, patch('tmdb.wikidata_claims') as claims:
            self.assertEqual(self.api.lookup(self.card)['provider_id'], 7)
        self.assertEqual(request.call_count, 1)
        claims.assert_not_called()

    def test_multiple_cross_id_matches_fail_closed(self):
        def request(path, params=None):
            if path == '/search/movie':
                return {'results': [] if params.get('primary_release_year') else [self.film, dict(self.film, id=8)], 'total_pages': 1}
            sid = int(path.split('/')[2])
            return {'id': sid, 'imdb_id': 'tt987', 'wikidata_id': 'Q' + str(sid)}
        with patch.object(self.api, 'request', side_effect=request), patch('tmdb.wikidata_claims', return_value={
                'P2603': {'321'}, 'P345': {'tt987'}}):
            self.assertIsNone(self.api.lookup(self.card))

    def test_uninspected_pages_and_candidate_limit_fail_closed(self):
        for pages, count in ((2, 1), (1, 9)):
            def request(path, params=None):
                return {'results': [] if params.get('primary_release_year') else [dict(self.film, id=i) for i in range(1, count+1)],
                        'total_pages': pages}
            with self.subTest(pages=pages, count=count), patch.object(self.api, 'request', side_effect=request) as api, patch('tmdb.wikidata_claims') as claims:
                self.assertIsNone(self.api.lookup(self.card))
                self.assertLessEqual(api.call_count, 2)
                claims.assert_not_called()

    def test_failed_candidate_request_cannot_produce_unique_partial_match(self):
        def request(path, params=None):
            if path == '/search/movie':
                return {'results': [] if params.get('primary_release_year') else [self.film, dict(self.film, id=8)], 'total_pages': 1}
            if path == '/movie/8/external_ids':
                raise TimeoutError()
            return {'id': 7, 'imdb_id': 'tt987', 'wikidata_id': 'Q77'}
        with patch.object(self.api, 'request', side_effect=request), patch('tmdb.wikidata_claims', return_value={
                'P2603': {'321'}, 'P345': {'tt987'}}):
            with self.assertRaises(TimeoutError):
                self.api.lookup(self.card)

    def test_invalid_external_identity_is_rejected_before_wikidata(self):
        for external in ({'id': 7, 'imdb_id': 'tt987', 'wikidata_id': 'https://evil/Q77'},
                         {'id': 8, 'imdb_id': 'tt987', 'wikidata_id': 'Q77'}):
            def request(path, params=None):
                return self.request(path, params) if path == '/search/movie' else external
            with self.subTest(external=external), patch.object(self.api, 'request', side_effect=request), patch('tmdb.wikidata_claims') as claims:
                self.assertIsNone(self.api.lookup(self.card))
                claims.assert_not_called()

    def test_wikidata_client_has_no_credentials_redirects_or_unbounded_body(self):
        original = httpx.Client
        def handle(request):
            self.assertEqual(str(request.url), 'https://www.wikidata.org/wiki/Special:EntityData/Q77.json')
            self.assertNotIn('authorization', request.headers)
            self.assertNotIn('cookie', request.headers)
            self.assertIn('HomeCinema/', request.headers['user-agent'])
            return httpx.Response(200, json={'entities': {'Q77': {'id': 'Q77', 'claims': {
                'P2603': [{'rank': 'normal', 'mainsnak': {'snaktype': 'value', 'datavalue': {'value': '321'}}}],
                'P345': [{'rank': 'normal', 'mainsnak': {'snaktype': 'value', 'datavalue': {'value': 'tt987'}}}]
            }}}})
        def client(**kwargs):
            self.assertEqual(kwargs, {'timeout': 8, 'trust_env': False, 'follow_redirects': False})
            return original(transport=httpx.MockTransport(handle), **kwargs)
        with patch('tmdb.httpx.Client', side_effect=client):
            self.assertEqual(wikidata_claims('Q77'), {'P2603': {'321'}, 'P345': {'tt987'}})
        with patch('tmdb.httpx.Client', side_effect=lambda **kwargs: original(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b'x' * 2_000_001)), **kwargs)):
            with self.assertRaisesRegex(ValueError, 'too large'):
                wikidata_claims('Q77')
        with patch('tmdb.httpx.Client', side_effect=lambda **kwargs: original(transport=httpx.MockTransport(
                lambda request: httpx.Response(302, headers={'Location': 'https://evil/'})), **kwargs)):
            with self.assertRaisesRegex(ValueError, 'HTTP 302'):
                wikidata_claims('Q77')

    def test_deprecated_cross_ids_and_nonvalue_claims_are_ignored(self):
        original = httpx.Client
        def handle(request):
            claims = {key: [{'rank': 'deprecated', 'mainsnak': {'snaktype': 'value', 'datavalue': {'value': value}}},
                            {'rank': 'normal', 'mainsnak': {'snaktype': 'novalue', 'datavalue': {'value': value}}}]
                      for key, value in [('P2603', '321'), ('P345', 'tt987')]}
            return httpx.Response(200, json={'entities': {'Q77': {'id': 'Q77', 'claims': claims}}})
        with patch('tmdb.httpx.Client', side_effect=lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs)):
            self.assertEqual(wikidata_claims('Q77'), {'P2603': set(), 'P345': set()})

    def test_bad_json_and_unexpected_entity_fail_closed(self):
        original = httpx.Client
        for response in (httpx.Response(200, content=b'not-json'), httpx.Response(200, json={'entities': {'Q99': {'id': 'Q99'}}})):
            with self.subTest(response=response), patch('tmdb.httpx.Client', side_effect=lambda **kwargs: original(
                    transport=httpx.MockTransport(lambda request: response), **kwargs)):
                with self.assertRaises(ValueError):
                    wikidata_claims('Q77')


if __name__ == '__main__':
    unittest.main()
