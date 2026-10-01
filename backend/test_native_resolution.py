"""Regression cases for provider artwork and mixed-source metadata resolution."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anwap import BASE
from anwap_series import series_card
from catalog import Catalog, merge_native
from metadata import Metadata
from native_metadata import lookup, parse_tracker, tracker_poster
from test_catalog import release


class NativeResolutionTests(unittest.TestCase):
    def series_html(self, image):
        return (
            '<h1>Образец сериала</h1>'
            '<meta property="og:url" content="' + BASE + '/serials/4321">'
            '<meta property="og:title" content="Образец сериала / Sample Show на телефон">'
            '<a href="/serials/god-2025">2025</a>'
            '<img class="filmscreen" src="' + image + '">'
        )

    def test_anwap_source_premiere_and_actual_portrait_url(self):
        for image in ('/serials/screen/4321.jpg', BASE + '/serials/screen/4321.jpg'):
            with self.subTest(image=image):
                card = series_card(self.series_html(image), 4321)
                self.assertEqual(card['year'], 2025)
                self.assertEqual(card['metadata']['premiere_year'], 2025)
                self.assertEqual(card['metadata']['poster'], BASE + '/serials/screen/4321.jpg')

    def test_anwap_untrusted_or_other_series_image_is_not_selected(self):
        invalid = (
            'https://evil.test/serials/screen/4321.jpg',
            '//127.0.0.1/serials/screen/4321.jpg',
            BASE + '.evil.test/serials/screen/4321.jpg',
            '/serials/screen/9999.jpg',
            '/serials/screen/4321.jpg?redirect=http://127.0.0.1/',
        )
        for image in invalid:
            with self.subTest(image=image):
                poster = series_card(self.series_html(image), 4321)['metadata'].get('poster')
                self.assertNotEqual(poster, image)
                self.assertIn(poster, (None, BASE + '/serials/posts/4321.jpg', BASE + '/serials/screen/4321.jpg'))

    def test_exkinoray_relative_cover_is_resolved_against_exact_provider(self):
        html = '<img class="p200" src="torrents/images/671610.jpg"><b>Описание:</b> История.<b>Видео:</b> HD'
        result = parse_tracker(html, {'title': 'Образец'}, 'https://exkinoray.ru/details.php?id=67161')
        self.assertEqual(result['poster'], 'https://exkinoray.ru/torrents/images/671610.jpg')
        self.assertEqual(result['description'], 'История.')

    def test_tracker_keeps_cover_before_description_not_later_screenshot(self):
        cover = 'https://exkinoray.ru/torrents/images/671610.jpg'
        screenshot = 'https://lostpix.com/img/2026-10/01/abcdef.jpg'
        html = '<img class="p200" src="' + cover + '"><b>Описание:</b> Текст.<b>Скриншоты:</b><img src="' + screenshot + '">'
        result = parse_tracker(html, {'title': 'Образец'}, 'https://exkinoray.ru/details.php?id=67161')
        self.assertEqual(result['poster'], cover)

    def test_tracker_allowlist_rejects_host_confusion_credentials_and_path_escape(self):
        valid = 'https://exkinoray.ru/torrents/images/671610.jpg'
        self.assertTrue(tracker_poster(valid))
        invalid = (
            'http://exkinoray.ru/torrents/images/671610.jpg',
            'https://exkinoray.ru.evil.test/torrents/images/671610.jpg',
            'https://exkinoray.ru@127.0.0.1/torrents/images/671610.jpg',
            'https://user:password@exkinoray.ru/torrents/images/671610.jpg',
            'https://exkinoray.ru:8080/torrents/images/671610.jpg',
            valid + '?redirect=http://127.0.0.1/',
            valid + '#fragment',
            'https://exkinoray.ru/torrents/images/../../details.php',
            'https://exkinoray.ru/torrents/images/%2e%2e/671610.jpg',
            'https://exkinoray.ru/other/671610.jpg',
            'https://127.0.0.1/torrents/images/671610.jpg',
        )
        for url in invalid:
            with self.subTest(url=url):
                self.assertFalse(tracker_poster(url))

    def test_hostingkartinok_cover_has_strict_host_and_image_path(self):
        cover = 'https://s1.hostingkartinok.com/uploads/images/2026/10/2d569987ad67df023a0957d9c55251ae.jpg'
        self.assertTrue(tracker_poster(cover))
        result = parse_tracker('<img src="' + cover + '"><b>Описание:</b> Текст.',
                               {'title': 'Образец'}, 'https://rutor.info/torrent/123/sample')
        self.assertEqual(result['poster'], cover)
        invalid = (
            cover.replace('s1.hostingkartinok.com', 's1.hostingkartinok.com.evil.test'),
            cover.replace('s1.hostingkartinok.com', '127.0.0.1'),
            cover.replace('/uploads/images/', '/uploads/private/'),
            cover.replace('/2026/10/', '/2026/10/../'),
            cover.replace('.jpg', '.php'),
            cover + '?download=1',
            cover + '#fragment',
        )
        for url in invalid:
            with self.subTest(url=url):
                self.assertFalse(tracker_poster(url))

    def test_relative_cover_cannot_escape_to_arbitrary_host(self):
        for image in ('//127.0.0.1/private.jpg', 'https://evil.test/cover.jpg', '../private.jpg'):
            with self.subTest(image=image):
                result = parse_tracker('<img class="p200" src="' + image + '"><b>Описание:</b> Текст.',
                                       {'title': 'Образец'}, 'https://exkinoray.ru/details.php?id=67161')
                self.assertIsNone(result.get('poster'))

    def test_imageban_cover_requires_exact_image_host_and_path(self):
        cover = 'https://i6.imageban.ru/out/2026/09/24/d8760ab3d9b818f132005913c77646c1.jpg'
        self.assertTrue(tracker_poster(cover))
        for url in (cover.replace('i6.imageban.ru', 'i6.imageban.ru.evil.test'),
                    cover.replace('i6.imageban.ru', 'imageban.ru'),
                    cover.replace('/out/', '/private/'), cover.replace('/09/24/', '/09/../'),
                    cover.replace('.jpg', '.php'), cover + '?redirect=1', cover + '#fragment'):
            with self.subTest(url=url):
                self.assertFalse(tracker_poster(url))


class NativeCatalogMergeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.catalog = Catalog(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def store_provider_metadata(self, card, metadata):
        with self.catalog.db() as db:
            db.execute('INSERT OR REPLACE INTO catalog_provider_items VALUES (?,?)',
                       (card['id'], json.dumps(dict(card, metadata=metadata))))

    def test_fresh_native_identifiers_override_stale_release_metadata(self):
        old = {'title': 'Старое название', 'description': 'Старое описание', 'imdb_id': 'tt1111111'}
        self.catalog.ingest('anwap', [release('old', 'Образец (2025)', 'anwap', metadata=old)])
        card = self.catalog.search()['results'][0]
        fresh = {'title': 'Уточнённое название', 'description': 'Новое описание', 'imdb_id': 'tt2222222', 'premiere_year': 2025}
        self.store_provider_metadata(card, fresh)
        result = self.catalog.detail(card['id'])['metadata']
        for key, value in fresh.items():
            self.assertEqual(result[key], value)

    def test_empty_fresh_fields_keep_verified_release_artwork_and_alternatives(self):
        old = {'title': 'Образец', 'poster': BASE + '/films/screen/4321.jpg',
               'backdrop': 'https://image.tmdb.org/t/p/w780/backdrop.jpg',
               'poster_alternatives': ['https://lostpix.com/img/2026-10/01/abcdef.jpg']}
        self.catalog.ingest('anwap', [release('old', 'Образец (2025)', 'anwap', metadata=old)])
        card = self.catalog.search()['results'][0]
        self.store_provider_metadata(card, {'imdb_id': 'tt2222222', 'poster': None, 'backdrop': '', 'poster_alternatives': []})
        result = self.catalog.detail(card['id'])['metadata']
        for key in ('poster', 'backdrop', 'poster_alternatives'):
            self.assertEqual(result[key], old[key])
        self.assertEqual(result['imdb_id'], 'tt2222222')

    def test_fresh_poster_preserves_existing_poster_as_fallback(self):
        old_poster = BASE + '/films/screen/4321.jpg'
        older_fallback = 'https://lostpix.com/img/2026-10/01/abcdef.jpg'
        fresh_poster = 'https://exkinoray.ru/torrents/images/671610.jpg'
        old = {'title': 'Образец', 'poster': old_poster, 'poster_alternatives': [older_fallback]}
        self.catalog.ingest('anwap', [release('old', 'Образец (2025)', 'anwap', metadata=old)])
        card = self.catalog.search()['results'][0]
        self.store_provider_metadata(card, {'poster': fresh_poster})
        result = self.catalog.detail(card['id'])['metadata']
        self.assertEqual(result['poster'], fresh_poster)
        self.assertIn(old_poster, result.get('poster_alternatives', []))
        self.assertIn(older_fallback, result.get('poster_alternatives', []))

    def test_different_lostfilm_slug_drops_all_previous_identity_fields(self):
        old = {'provider': 'LostFilm', 'url': 'https://www.lostfilm.tv/series/Sample_2015/',
               'poster': 'https://www.lostfilm.tv/Static/Images/1/Posters/poster.jpg',
               'poster_alternatives': ['https://www.lostfilm.tv/Static/Images/1/Posters/image.jpg'],
               'premiere_year': 2015, 'description': 'Old show', 'imdb_id': 'tt1111111'}
        new = {'provider': 'LostFilm', 'url': 'https://www.lostfilm.tv/series/Sample_2024/',
               'poster': None, 'premiere_year': 2024}
        self.assertEqual(merge_native(old, new), new)
        same = dict(new, url='https://www.lostfilm.tv/series/Sample_2015/')
        self.assertEqual(merge_native(old, same)['poster'], old['poster'])

    def test_lostfilm_detail_and_enrich_ignore_incompatible_directory_and_tmdb_cache(self):
        row = dict(release('current', 'Sample S01E01', 'lostfilm', cats=[5000]),
                   details='https://www.lostfilm.tv/series/Sample_2024/season_1/episode_1/')
        self.catalog.ingest('lostfilm', [row])
        card = self.catalog.search()['results'][0]
        old = {'provider': 'LostFilm', 'url': 'https://www.lostfilm.tv/series/Sample_2015/',
               'title': 'Other remake', 'premiere_year': 2015,
               'poster': 'https://www.lostfilm.tv/Static/Images/1/Posters/poster.jpg'}
        self.store_provider_metadata(dict(card, provider_title='Other remake', aliases=['Wrong alias']), old)
        meta = Metadata(self.catalog)
        cached = {'provider': 'TMDB', 'provider_id': 1, 'title': 'Other remake', 'poster': old['poster']}
        with self.catalog.db() as db:
            db.execute('INSERT INTO metadata_cache VALUES (?,?,?)', (card['id'], json.dumps(cached), 1))
            db.execute('INSERT INTO image_assets VALUES (?,?,?,?,?)', (old['poster'], 200, 300, 'wrong-remake', 1))
        detail = self.catalog.detail(card['id'])
        self.assertEqual(detail['source_slug'], 'Sample_2024')
        self.assertIsNone(detail.get('metadata'))
        self.assertNotIn('Wrong alias', detail.get('aliases', []))
        self.assertNotEqual(detail.get('provider_title'), 'Other remake')
        # An already enriched stale response must not reintroduce its cached metadata either.
        rendered = meta.enrich([dict(card, metadata=cached)])[0]
        self.assertIsNone(rendered['metadata'])
        self.assertEqual(meta._card_art(card['id']), (None, None))

    def test_lostfilm_identity_change_invalidates_only_its_stale_tmdb_cache(self):
        row = dict(release('current', 'Sample S01E01', 'lostfilm', cats=[5000]),
                   details='https://www.lostfilm.tv/series/Sample_2024/season_1/episode_1/')
        self.catalog.ingest('lostfilm', [row])
        card = self.catalog.search()['results'][0]
        old = {'provider': 'LostFilm', 'url': 'https://www.lostfilm.tv/series/Sample_2015/',
               'poster': 'https://www.lostfilm.tv/Static/Images/1/Posters/poster.jpg'}
        self.store_provider_metadata(card, old)
        meta = Metadata(self.catalog)
        with self.catalog.db() as db:
            db.execute('INSERT INTO metadata_cache VALUES (?,?,?)', (card['id'], '{"provider":"TMDB","provider_id":1}', 1))
            db.execute('INSERT INTO metadata_cache VALUES (?,?,?)', ('other-card', '{"provider":"TMDB","provider_id":2}', 1))
        fresh = {'provider': 'LostFilm', 'url': 'https://www.lostfilm.tv/series/Sample_2024/',
                 'title': 'Current remake', 'poster': None}
        with patch('native_metadata.lookup', return_value=fresh):
            meta.refresh_native(self.catalog.detail(card['id']))
        with self.catalog.db() as db:
            self.assertIsNone(db.execute('SELECT payload FROM metadata_cache WHERE content_id=?', (card['id'],)).fetchone())
            self.assertIsNotNone(db.execute('SELECT payload FROM metadata_cache WHERE content_id=?', ('other-card',)).fetchone())
        self.assertEqual(self.catalog.detail(card['id'])['metadata'], fresh)

    def test_older_rutor_release_fills_gaps_without_replacing_newest_metadata(self):
        rows = [dict(release('new', 'Образец (2025)', 'rutor', kind='movie'), published_at=200,
                     details='https://rutor.info/torrent/200/sample'),
                dict(release('old', 'Образец (2025)', 'rutor', kind='movie'), published_at=100,
                     details='https://rutor.info/torrent/100/sample')]
        self.catalog.ingest('rutor', rows)
        card = self.catalog.detail(self.catalog.search()['results'][0]['id'])
        primary = 'https://i128.fastpic.org/big/2026/1001/ab/abcdef.jpg'
        fallback = 'https://s1.hostingkartinok.com/uploads/images/2026/09/abcdef.jpg'
        pages = {
            rows[0]['details']: '<img src="' + primary + '"><b>Описание:</b> Новое описание.<b>Видео:</b> HD',
            rows[1]['details']: '<img src="' + fallback + '"><b>Описание:</b> Старое описание.<b>Видео:</b> HD<a href="https://www.imdb.com/title/tt2222222/">IMDb</a>',
        }
        with patch('native_metadata.fetch', side_effect=lambda url, **kwargs: pages[url].encode()):
            result = lookup(self.catalog, card)
        self.assertEqual(result['url'], rows[0]['details'])
        self.assertEqual(result['description'], 'Новое описание.')
        self.assertEqual(result['poster'], primary)
        self.assertEqual(result['imdb_id'], 'tt2222222')
        self.assertIn(fallback, result.get('poster_alternatives', []))

    def test_mixed_source_lookup_reaches_tracker_regardless_of_source_order(self):
        rows = {
            'anwap': dict(release('a', 'Образец (2025)', 'anwap'), details=BASE + '/films/4321'),
            'rutor': dict(release('r', 'Образец (2025)', 'rutor', kind='movie'),
                          details='https://rutor.info/torrent/123/sample'),
        }
        for order in (('anwap', 'rutor'), ('rutor', 'anwap')):
            with self.subTest(order=order):
                with self.catalog.db() as db:
                    db.execute('DELETE FROM catalog_releases')
                    db.execute('DELETE FROM catalog_provider_items')
                for source in order:
                    self.catalog.ingest(source, [rows[source]])
                card = self.catalog.detail(self.catalog.search()['results'][0]['id'])
                body = b'<b>Description</b><a href="https://www.imdb.com/title/tt2222222/">IMDb</a>'
                with patch('native_metadata.fetch', return_value=body) as fetch:
                    result = lookup(self.catalog, card)
                self.assertIsNotNone(result)
                self.assertEqual(result.get('imdb_id'), 'tt2222222')
                self.assertTrue(any(call.args[0] == 'https://rutor.info/torrent/123/sample' for call in fetch.call_args_list))

    def test_new_cover_does_not_stop_description_recovery_from_older_release(self):
        rows=[dict(release('new','Образец (2025)','rutor',kind='movie'),published_at=200,details='https://rutor.info/torrent/200/sample'),
              dict(release('old','Образец (2025)','rutor',kind='movie'),published_at=100,details='https://rutor.info/torrent/100/sample')]
        self.catalog.ingest('rutor',rows)
        card=self.catalog.detail(self.catalog.search()['results'][0]['id'])
        cover='https://i6.imageban.ru/out/2026/09/24/d8760ab3d9b818f132005913c77646c1.jpg'
        pages={rows[0]['details']:'<img src="'+cover+'">',rows[1]['details']:'<b>Описание:</b> Полное описание.<b>Видео:</b> HD'}
        with patch('native_metadata.fetch',side_effect=lambda url,**kwargs:pages[url].encode()):
            result=lookup(self.catalog,card)
        self.assertEqual(result['poster'],cover)
        self.assertEqual(result['description'],'Полное описание.')


if __name__ == '__main__':
    unittest.main()
