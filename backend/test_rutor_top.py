"""Minimal fixtures retain the public /top page's actual section and row structure."""
from datetime import datetime, timezone
import unittest

from rutor_top import BASE, magnet, top_rows


HASH = '7991b2617731428703a0c4d35cf83e496c7bda02'


def row(tid=1108276, title='Восставший / The Uprising (2026) WEB-DL 1080p | D, Р',
        link=None, infohash=HASH, date='30&nbsp;Сен&nbsp;26', size='9.44&nbsp;GB', seeders='658', comments=True):
    details = link if link is not None else '/torrent/' + str(tid) + '/sample-2026-web-dl'
    return ('<tr class="gai"><td>' + date + '</td><td' + ('>' if comments else ' colspan="2">')
            + '<a class="downgif" href="//d.rutor.info/download/' + str(tid) + '"><img src="//cdnbunny.org/i/d.gif" alt="D"></a>'
            + '<a href="magnet:?xt=urn:btih:' + infohash + '&amp;dn=rutor.info&amp;tr=udp://opentor.net:6969&amp;ws=http://127.0.0.1/private"><img src="//cdnbunny.org/i/m.png" alt="M"></a>'
            + '<a href="' + details + '">' + title + '</a></td>'
            + ('<td align="right">3<img src="//cdnbunny.org/i/com.gif" alt="C"></td>' if comments else '')
            + '<td align="right">' + size + '</td><td align="center"><span class="green"><img src="//cdnbunny.org/t/arrowup.gif" alt="S">&nbsp;'
            + seeders + '</span>&nbsp;<img src="//cdnbunny.org/t/arrowdown.gif" alt="L"><span class="red">&nbsp;587</span></td></tr>')


def section(category, rows, title='Самые популярные торренты в категории'):
    return ('<h2>' + title + ' <a href=' + category + '>Название категории</a></h2><table width="100%">'
            '<tr class="backgr"><td><img src="//cdnbunny.org/i/ickino.gif"></td><td colspan="2">Название</td><td>Размер</td><td>Пиры</td></tr>'
            + rows + '</table>')


def page(kino=None, domestic=None):
    return section('/kino', row() if kino is None else kino) + section('/nashe_kino', row(1108283, 'Наш фильм (2026)', comments=False) if domestic is None else domestic)


class RutorTopTests(unittest.TestCase):
    def test_only_two_named_category_tables_are_selected(self):
        html = (section('/kino', row(10), title='Топ торренты за последние 24 часа')
                + '<a href="/kino">Menu link must not select a table</a><table>' + row(11) + '</table>'
                + page() + section('/seriali', row(12)) + section('/games', row(13)))
        rows = top_rows(html)
        self.assertEqual([item['rutor_id'] for item in rows], ['1108276', '1108283'])
        self.assertEqual([item['home_category'] for item in rows], ['kino', 'nashe_kino'])
        self.assertTrue(all(item['kind'] == 'movie' and item['categories'] == [2000] for item in rows))

    def test_live_row_date_size_seeders_and_tracker_free_magnet(self):
        item = top_rows(page())[0]
        self.assertEqual(item['raw'], 'Восставший / The Uprising (2026) WEB-DL 1080p | D, Р')
        self.assertEqual(item['published_at'], int(datetime(2026, 9, 30, tzinfo=timezone.utc).timestamp()))
        self.assertEqual(item['size'], 10136122818)
        self.assertEqual(item['seeders'], 658)
        self.assertEqual(item['details'], BASE + '/torrent/1108276/sample-2026-web-dl')
        self.assertEqual(item['download_url'], 'magnet:?xt=urn:btih:' + HASH)
        self.assertNotIn('&', item['download_url'])

    def test_russian_size_decimal_comma_four_digit_year_and_no_comment_cell(self):
        item = top_rows(page(kino=row(date='01 Окт 2026', size='1,50 ГБ', seeders='1&nbsp;234', comments=False)))[0]
        self.assertEqual(item['size'], 1610612736)
        self.assertEqual(item['seeders'], 1234)
        self.assertEqual(item['published_at'], int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()))

    def test_all_qualities_remain_distinct_releases_in_source_order(self):
        rows = top_rows(page(kino=row(1, 'Фильм (2026) WEB-DL 1080p') + row(2, 'Фильм (2026) BDRip 720p')))
        self.assertEqual([item['home_rank'] for item in rows[:2]], [0, 1])
        self.assertEqual(len({item['id'] for item in rows}), 3)

    def test_missing_empty_duplicate_or_truncated_sections_fail_closed(self):
        invalid = (section('/kino', row()), page(kino=''), page() + section('/kino', row()),
                   page().rsplit('</table>', 1)[0], '<html>Upstream unavailable</html>',
                   section('https://rutor.info.evil.test/kino', row()) + section('/nashe_kino', row(2)))
        for html in invalid:
            with self.subTest(html=html[:80]):
                with self.assertRaises(ValueError):
                    top_rows(html)

    def test_one_malformed_data_row_rejects_entire_snapshot(self):
        for bad in (row(date='31 Фев 26'), row(size='unknown'), row(seeders='?'),
                    row(infohash='invalid'), row().replace('</td>', '', 1)):
            with self.subTest(row=bad[:80]):
                with self.assertRaises(ValueError):
                    top_rows(page(kino=row(1) + bad))

    def test_unsafe_or_unexpected_torrent_urls_are_rejected(self):
        for link in ('https://evil.test/torrent/1/sample', '//127.0.0.1/torrent/1/sample',
                     'https://rutor.info.evil.test/torrent/1/sample', 'https://user@rutor.info/torrent/1/sample',
                     'https://rutor.info:8080/torrent/1/sample', '/torrent/1/sample?redirect=1',
                     '/torrent/1/../private', '/torrent/1/%2e%2e', '/download/1'):
            with self.subTest(link=link):
                with self.assertRaises(ValueError):
                    top_rows(page(kino=row(link=link)))

    def test_safe_mirror_links_are_canonicalized(self):
        item = top_rows(page(kino=row(link='http://rutor.is/torrent/123/sample')))[0]
        self.assertEqual(item['details'], BASE + '/torrent/123/sample')

    def test_duplicate_torrent_id_is_one_release_but_conflicting_hash_fails(self):
        rows = top_rows(page(kino=row() + row(link='/torrent/1108276/renamed-slug')))
        self.assertEqual(len(rows), 2)
        with self.assertRaises(ValueError):
            top_rows(page(kino=row() + row(infohash='a' * 40)))

    def test_only_one_valid_infohash_is_retained(self):
        self.assertEqual(magnet('magnet:?xt=urn:btih:' + 'A' * 40 + '&tr=http://internal'), 'magnet:?xt=urn:btih:' + 'a' * 40)
        self.assertEqual(magnet('magnet:?xt=urn:btih:' + 'A' * 32), 'magnet:?xt=urn:btih:' + '0' * 40)
        for link in ('magnet:?xt=urn:btih:' + HASH + '&xt=urn:btih:' + 'a' * 40,
                     'magnet:?xt=urn:btih:wrong', 'https://evil.test/' + HASH,
                     'magnet://evil/?xt=urn:btih:' + HASH):
            with self.subTest(link=link):
                with self.assertRaises(ValueError):
                    magnet(link)

    def test_row_and_body_limits_fail_closed(self):
        with self.assertRaises(ValueError):
            top_rows(page(kino=''.join(row(index + 1) for index in range(200))))
        with self.assertRaises(ValueError):
            top_rows('x' * 6_000_001)


if __name__ == '__main__':
    unittest.main()
