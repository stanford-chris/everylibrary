#!/usr/bin/env python3
"""
Tests for the roster applier and the image-sweep memo expiry.

**The refusal tests are the point.** Nothing here crashes when it goes wrong.
A library given the wrong position is attached to a photograph of somewhere
else and reads as a perfectly ordinary post; a memo left in place means the
monthly sweep finds nothing and reports a clean run for ever; a rewritten
existing row re-keys a library that has already been posted and posts it a
second time. Every one of those is silent, so each has a test asserting the
refusal rather than the happy path.

Stdlib only. `everylibrary_post` pulls in atproto and `everylibrary_images`
pulls in requests, neither of which is installed on every machine here, so both
are stubbed at import time exactly as test_nyt_lead_tracker.py does.

    python3 test_everylibrary_roster_apply.py
"""

import csv
import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))


def _stub(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules.setdefault(name, mod)
    return mod


# atproto, for everylibrary_post
_stub('atproto', Client=object, client_utils=types.SimpleNamespace(),
      exceptions=types.SimpleNamespace(), models=types.SimpleNamespace())
# requests, for everylibrary_images and the applier. Session needs real
# headers: everylibrary_images builds one at import time and updates them.
class _FakeSession:
    def __init__(self):
        self.headers = {}

    def get(self, *a, **k):
        raise AssertionError('no test here may reach the network')

    def post(self, *a, **k):
        raise AssertionError('no test here may reach the network')


_stub('requests', utils=types.SimpleNamespace(unquote=lambda s: s),
      RequestException=Exception, Session=_FakeSession,
      post=None, get=None, exceptions=types.SimpleNamespace(RequestException=Exception))

roster_apply = importlib.import_module('everylibrary_roster_apply')
images = importlib.import_module('everylibrary_images')
post = importlib.import_module('everylibrary_post')


def rec(**over):
    """A live API record, in the shape api.librarydata.uk actually returns."""
    base = {
        'Library name': 'Torrington',
        'Local authority': 'Devon County Council',
        'Local authority code': 'E10000008',
        'Address 1': 'Castle Hill',
        'Address 2': 'Torrington',
        'Postcode': 'EX38 8AA',
        'Latitude': '50.95134',
        'Longitude': '-4.143787',
        'Type of library': 'CL',
        'Statutory': 'Yes',
        'Year opened': '1820',
        'URL': 'https://example.invalid/torrington',
    }
    base.update(over)
    return base


class ToRow(unittest.TestCase):
    def test_maps_a_complete_record(self):
        row = roster_apply.to_row(rec())
        self.assertEqual(list(row), roster_apply.COLUMNS)
        self.assertEqual(row['nation'], 'England')
        self.assertEqual(row['address'], 'Castle Hill, Torrington')
        self.assertEqual(row['town'], 'Torrington')
        self.assertEqual(row['lat'], '50.95134')

    def test_osm_and_wikidata_columns_are_blank(self):
        # They come from the Overpass join that built the corpus, and nothing
        # re-runs it. Inventing a value here would be a fabricated match.
        row = roster_apply.to_row(rec())
        for col in ('osm_type', 'osm_id', 'wikidata', 'match_m'):
            self.assertEqual(row[col], '', col)

    def test_nation_from_each_code_prefix(self):
        for code, nation in (('E10000008', 'England'), ('S12000036', 'Scotland'),
                             ('W06000010', 'Wales'), ('N09000003', 'Northern Ireland')):
            self.assertEqual(roster_apply.to_row(rec(**{'Local authority code': code}))['nation'],
                             nation, code)

    def test_unknown_code_prefix_is_refused(self):
        out = roster_apply.to_row(rec(**{'Local authority code': 'X99999999'}))
        self.assertIsInstance(out, str)
        self.assertIn('unrecognised', out)

    def test_missing_coordinates_are_refused_without_a_postcode_fix(self):
        out = roster_apply.to_row(rec(Latitude=None, Longitude=None))
        self.assertIsInstance(out, str)
        self.assertIn('postcode did not resolve', out)

    def test_missing_coordinates_take_the_geocoded_pair(self):
        row = roster_apply.to_row(rec(Latitude=None, Longitude=None),
                                  coords=(52.193664, -1.707534))
        self.assertEqual((row['lat'], row['lon']), ('52.193664', '-1.707534'))

    def test_unparseable_coordinates_are_refused(self):
        # Refused rather than dropped to the postcode: a value that is present
        # and wrong is a different fault from one that is absent.
        out = roster_apply.to_row(rec(Latitude='north a bit'))
        self.assertIsInstance(out, str)
        self.assertIn('unparseable', out)

    def test_nameless_record_is_refused(self):
        self.assertIsInstance(roster_apply.to_row(rec(**{'Library name': '  '})), str)

    def test_a_row_it_builds_is_addressable_by_library_id(self):
        # to_row's output must be a valid input to library_id, or an addition
        # lands in the corpus and can never be keyed to alt text or post state.
        row = roster_apply.to_row(rec())
        self.assertEqual(post.library_id(row), post.library_id(
            {'name': 'torrington ', 'postcode': 'ex38  8aa'}))


class WriteCorpus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'corpus.csv'
        self._real = roster_apply.CORPUS
        roster_apply.CORPUS = self.path

    def tearDown(self):
        roster_apply.CORPUS = self._real
        self.tmp.cleanup()

    def test_round_trips_every_column(self):
        rows = [roster_apply.to_row(rec()), roster_apply.to_row(rec(**{
            'Library name': 'Warrington Library', 'Postcode': 'WA1 1JG'}))]
        roster_apply.write_corpus(rows)
        with open(self.path) as f:
            back = list(csv.DictReader(f))
        self.assertEqual(back, rows)

    def test_leaves_no_temp_file_behind(self):
        roster_apply.write_corpus([roster_apply.to_row(rec())])
        self.assertEqual(sorted(os.listdir(self.tmp.name)), ['corpus.csv'])


class Geocoder(unittest.TestCase):
    """The bulk lookup, with the HTTP call stubbed."""

    def _run(self, payload=None, raiser=None):
        calls = []

        class Resp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return payload

        def fake_post(url, json=None, headers=None, timeout=None):
            calls.append(json['postcodes'])
            if raiser:
                raise raiser
            return Resp()

        real = roster_apply.requests.post
        roster_apply.requests.post = fake_post
        try:
            return roster_apply.geocode_postcodes(self.codes, 'ua'), calls
        finally:
            roster_apply.requests.post = real

    def test_returns_only_the_resolved_ones(self):
        self.codes = ['CV37 6PZ', 'SA15 5SC']
        out, _ = self._run({'result': [
            {'query': 'CV37 6PZ', 'result': {'latitude': 52.19, 'longitude': -1.70}},
            {'query': 'SA15 5SC', 'result': None},
        ]})
        self.assertEqual(out, {'CV37 6PZ': (52.19, -1.70)})

    def test_one_request_for_many_postcodes(self):
        self.codes = [f'AA{n} 1AA' for n in range(90)]
        _, calls = self._run({'result': []})
        self.assertEqual(len(calls), 1)

    def test_batches_above_the_hundred_cap(self):
        self.codes = [f'AA{n} 1AA' for n in range(150)]
        _, calls = self._run({'result': []})
        self.assertEqual([len(c) for c in calls], [100, 50])

    def test_a_failed_lookup_resolves_nothing_rather_than_raising(self):
        # The caller refuses a library it cannot place. A raise here would take
        # the whole monthly pass down over two bad postcodes.
        self.codes = ['CV37 6PZ']
        out, _ = self._run(raiser=roster_apply.requests.RequestException('boom'))
        self.assertEqual(out, {})

    def test_no_request_at_all_when_nothing_needs_geocoding(self):
        self.codes = []
        _, calls = self._run({'result': []})
        self.assertEqual(calls, [])


class ExpireMisses(unittest.TestCase):
    """The memo expiry. Every assertion here is about what must SURVIVE."""

    def state(self):
        return {
            'p18': {'Q1': 'A.jpg', 'Q2': None, 'Q3': None},
            'geo': {'k1': {'title': 'File:B.jpg'}},
            'geosearch_done': ['k1', 'k2', 'k3'],
            'wdnear': {'k4': {'qid': 'Q9'}},
            'geograph': {'k5': {'id': 7}},
            'imageinfo': {'File:B.jpg': {'user': 'someone'}},
            'wd_items': [{'qid': 'Q9'}],
        }

    def setUp(self):
        self.saved = []
        self._real_save = images.save_state
        self._real_log = images.log
        images.save_state = self.saved.append
        images.log = lambda *a, **k: None

    def tearDown(self):
        images.save_state = self._real_save
        images.log = self._real_log

    def test_forgets_only_the_wikidata_misses(self):
        st = self.state()
        images.expire_misses(st, [])
        self.assertEqual(st['p18'], {'Q1': 'A.jpg'})

    def test_forgets_only_swept_libraries_with_no_match(self):
        st = self.state()
        images.expire_misses(st, [])
        self.assertEqual(st['geosearch_done'], ['k1'])

    def test_drops_the_cached_wikidata_item_list(self):
        # Left in place, stage 2b re-matches against a frozen set of items and
        # cannot see a photograph added to Wikidata since. That is the exact
        # reason a plain re-run of the pipeline found nothing.
        st = self.state()
        images.expire_misses(st, [])
        self.assertNotIn('wd_items', st)

    def test_keeps_every_hit(self):
        st = self.state()
        images.expire_misses(st, [])
        self.assertEqual(st['geo'], {'k1': {'title': 'File:B.jpg'}})
        self.assertEqual(st['wdnear'], {'k4': {'qid': 'Q9'}})
        self.assertEqual(st['geograph'], {'k5': {'id': 7}})

    def test_keeps_the_commons_metadata_cache(self):
        # Keyed by file title, and a title's metadata does not go stale. Ditching
        # it would re-fetch imageinfo for all 2,238 every month for nothing.
        st = self.state()
        images.expire_misses(st, [])
        self.assertIn('File:B.jpg', st['imageinfo'])

    def test_saves_once(self):
        st = self.state()
        images.expire_misses(st, [])
        self.assertEqual(len(self.saved), 1)

    def test_is_idempotent(self):
        st = self.state()
        images.expire_misses(st, [])
        first = {k: v for k, v in st.items()}
        images.expire_misses(st, [])
        self.assertEqual(st, first)

    def test_survives_a_state_file_with_no_geosearch_key(self):
        st = {'p18': {}, 'geo': {}, 'wdnear': {}, 'imageinfo': {}}
        images.expire_misses(st, [])
        self.assertEqual(st['geosearch_done'], [])


class LibraryIdIdentity(unittest.TestCase):
    """The key both alt_text.json and post_state.json are stored under."""

    def test_ignores_case_and_whitespace(self):
        a = post.library_id({'name': 'Torrington', 'postcode': 'EX38 8AA'})
        b = post.library_id({'name': ' torrington ', 'postcode': 'ex388aa'})
        self.assertEqual(a, b)

    def test_ignores_the_latitude(self):
        # The whole reason for the 23 August 2026 migration: a coordinate the
        # roster refines by a metre must not mint a new id and re-post a library.
        a = post.library_id({'name': 'Torrington', 'postcode': 'EX38 8AA', 'lat': '50.95134'})
        b = post.library_id({'name': 'Torrington', 'postcode': 'EX38 8AA', 'lat': '50.95135'})
        self.assertEqual(a, b)

    def test_distinguishes_two_libraries(self):
        a = post.library_id({'name': 'Torrington', 'postcode': 'EX38 8AA'})
        b = post.library_id({'name': 'Warrington Library', 'postcode': 'WA1 1JG'})
        self.assertNotEqual(a, b)

    def test_agrees_with_the_roster_check_match_key(self):
        # They are one function now. If they ever diverge again, the drift check
        # and the posted state stop meaning the same thing by "same library".
        import everylibrary_roster_check as rc
        self.assertIs(rc.norm, post.norm)


class CorpusIsStillUnique(unittest.TestCase):
    """Against the real corpus, not a fixture: the id has no lat in it any more,
    so uniqueness is a property of the live data and can stop being true."""

    def test_no_two_libraries_share_a_library_id(self):
        path = HERE / 'data' / 'uk_libraries_final.csv'
        if not path.exists():
            self.skipTest('corpus not present')
        with open(path) as f:
            rows = list(csv.DictReader(f))
        ids = {post.library_id(r) for r in rows}
        self.assertEqual(len(ids), len(rows))


if __name__ == '__main__':
    unittest.main()
