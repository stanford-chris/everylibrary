#!/usr/bin/env python3
"""Tests for the verification pass in everylibrary_describe.

Run from this directory (this file is NOT reachable from root discovery, which
does not descend into everylibrary/ — that directory is not an importable
package):

    python3 test_everylibrary_describe.py

Stdlib only. `requests` is stubbed before the import, so this runs on either
machine and makes no network call and no model call.

The cases that matter here are the ones where the check CANNOT answer. A
failed call, a timeout and a reply in the wrong format all produce no ABSENT
lines, which is byte-identical to a clean verification — and the first live
sweep on 27 August 2026 hit exactly that, returning 12 of 12 NOT CHECKED
because the model was rate-limited. If those read as passes, a rate-limited
run would silently certify every description in the corpus as verified.
"""

import subprocess
import sys
import types
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.modules.setdefault('requests', types.SimpleNamespace(
    Session=object, RequestException=Exception, exceptions=types.SimpleNamespace()))

import everylibrary_describe as eld


def reply(rc, out):
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr='')


GOOD = 'Red brick library with tall arched windows and a slate roof.'
ALL_FOUND = ('FOUND | red brick | full width\n'
             'FOUND | tall arched windows | ground floor')
ONE_ABSENT = ('FOUND | red brick | full width\n'
              'ABSENT | slate roof | the roof is not visible in this frame')


class Unsupported(unittest.TestCase):

    def call(self, r):
        with unittest.mock.patch.object(eld.subprocess, 'run',
                                        side_effect=[r] if not isinstance(r, Exception)
                                        else r):
            return eld.unsupported('/tmp/x.jpg', GOOD, env=None)

    def test_all_found_is_clean(self):
        self.assertEqual(self.call(reply(0, ALL_FOUND)), ([], None))

    def test_absent_lines_are_returned(self):
        claims, why = self.call(reply(0, ONE_ABSENT))
        self.assertEqual(claims, ['slate roof'])
        self.assertIsNone(why)

    def test_a_reply_with_no_verdicts_is_not_a_pass(self):
        claims, why = self.call(reply(0, 'Looks accurate to me.'))
        self.assertIsNone(claims)
        self.assertIn('verdict', why)

    def test_a_rate_limit_is_not_a_pass(self):
        # The exact failure of the first live sweep, 27 August 2026.
        claims, why = self.call(reply(1, 'API Error: Rate limit reached'))
        self.assertIsNone(claims)
        self.assertIn('Rate limit', why)

    def test_a_timeout_is_not_a_pass(self):
        claims, why = self.call(subprocess.TimeoutExpired(cmd='claude', timeout=1))
        self.assertIsNone(claims)
        self.assertEqual(why, 'TimeoutExpired')

    def test_the_reason_is_carried_not_collapsed(self):
        # A sweep that reports only that it failed is a sweep nobody can act on.
        _, a = self.call(reply(1, 'API Error: Rate limit reached'))
        _, b = self.call(reply(0, 'Looks accurate to me.'))
        self.assertNotEqual(a, b)


class DescribeRetry(unittest.TestCase):
    """describe() around the check: ship, rewrite, keep-unverified, or drop."""

    def run_describe(self, reads, verdicts):
        """`reads` are what _one_description returns in order; `verdicts` what
        unsupported returns in order."""
        calls = {'extra': []}

        def fake_read(path, env, extra='', **kwargs):
            calls['extra'].append(extra)
            return reads.pop(0)

        with unittest.mock.patch.object(eld, 'fetch_image', return_value=b'jpeg'), \
             unittest.mock.patch.object(eld, '_one_description', fake_read), \
             unittest.mock.patch.object(eld, 'unsupported',
                                        side_effect=list(verdicts)), \
             unittest.mock.patch.object(eld, 'log', lambda *_: None):
            out = eld.describe({'name': 'X', 'image_source': 'commons',
                                'image_title': 't'}, session=None, env=None)
        return out, calls

    def test_a_verified_description_ships(self):
        out, calls = self.run_describe([GOOD, GOOD], [([], None)])
        self.assertEqual(out, GOOD)
        self.assertEqual(calls['extra'], ['', ''])   # no retry read

    def test_a_failed_check_rewrites_and_ships_the_rewrite(self):
        out, calls = self.run_describe(
            [GOOD, GOOD, 'Red brick library with tall arched windows.'],
            [(['slate roof'], None), ([], None)])
        self.assertEqual(out, 'Red brick library with tall arched windows.')

    def test_the_rewrite_names_what_failed(self):
        _, calls = self.run_describe(
            [GOOD, GOOD, 'shorter'], [(['slate roof'], None), ([], None)])
        self.assertIn('slate roof', calls['extra'][2])

    def test_the_two_ordinary_reads_are_independent(self):
        # The agreement check only means something if both reads are blind.
        _, calls = self.run_describe([GOOD, GOOD], [([], None)])
        self.assertEqual(calls['extra'][0], '')
        self.assertEqual(calls['extra'][1], '')

    def test_a_second_failure_drops(self):
        out, _ = self.run_describe(
            [GOOD, GOOD, 'still wrong'],
            [(['slate roof'], None), (['slate roof'], None)])
        self.assertIsNone(out)

    def test_an_unmakeable_check_keeps_the_description(self):
        # The fallback is bare identification from the roster, which tells a
        # blind listener nothing. A rate-limited hour must not strip the corpus.
        out, _ = self.run_describe([GOOD, GOOD], [(None, 'exit 1: Rate limit')])
        self.assertEqual(out, GOOD)

    def test_an_unmakeable_check_on_the_rewrite_keeps_the_rewrite(self):
        out, _ = self.run_describe(
            [GOOD, GOOD, 'shorter'],
            [(['slate roof'], None), (None, 'exit 1: Rate limit')])
        self.assertEqual(out, 'shorter')

    def test_disagreeing_reads_never_reach_the_check(self):
        out, _ = self.run_describe(
            ['A single-storey brick library.', 'A three-storey brick library.'],
            [])
        self.assertIsNone(out)


class SubjectAndSpelling(unittest.TestCase):
    """The prompt's subject/spelling default to everylibrary's own case
    (a UK public library, British) and everycarnegie overrides both per row
    — see everycarnegie/carnegie_describe.py. Checked at the subprocess
    boundary, since _one_description is what actually builds the prompt."""

    def run_one(self, **kwargs):
        with unittest.mock.patch.object(
                eld.subprocess, 'run',
                return_value=reply(0, GOOD)) as mock_run:
            eld._one_description('/tmp/x.jpg', env=None, **kwargs)
        return mock_run.call_args[0][0][-1]   # the prompt string argument

    def test_default_is_everylibrarys_own_case(self):
        prompt = self.run_one()
        self.assertIn('a UK public library', prompt)
        self.assertIn('British English', prompt)

    def test_carnegie_can_override_both(self):
        prompt = self.run_one(subject='a Carnegie library building',
                               spelling='American')
        self.assertIn('a Carnegie library building', prompt)
        self.assertIn('American English', prompt)
        self.assertNotIn('UK public library', prompt)
        self.assertNotIn('British English', prompt)

    def test_describe_threads_subject_and_spelling_through_every_read(self):
        # Not mocking _one_description this time: three real calls (two
        # reads + a retry) must each carry the override, not just the first.
        prompts = []

        def fake_run(cmd, **_):
            prompts.append(cmd[-1])
            return reply(0, GOOD)

        with unittest.mock.patch.object(eld, 'fetch_image', return_value=b'jpeg'), \
             unittest.mock.patch.object(eld.subprocess, 'run', side_effect=fake_run), \
             unittest.mock.patch.object(eld, 'unsupported',
                                        side_effect=[(['slate roof'], None), ([], None)]), \
             unittest.mock.patch.object(eld, 'log', lambda *_: None):
            eld.describe({'name': 'X', 'image_source': 'commons', 'image_title': 't'},
                         session=None, env=None,
                         subject='a Carnegie library building', spelling='American')
        self.assertEqual(len(prompts), 3)   # two reads + one retry
        for p in prompts:
            self.assertIn('a Carnegie library building', p)
            self.assertIn('American English', p)


class BilingualAgreementChecks(unittest.TestCase):
    """_storeys()/OUTDOOR/INDOOR only ever carried British vocabulary
    (storey, pavement, car park, kerb, issue desk), because everylibrary's
    own descriptions only ever get asked for British English. Now that
    everycarnegie can ask for American on 79% of its roster, a check that
    still only recognises the British half loses sensitivity exactly where
    it matters most: an American two-storey description agreeing with
    itself on storey count, or on being outdoors, would silently stop
    being checked at all."""

    def test_storeys_matches_british_singular_and_plural(self):
        self.assertEqual(eld._storeys('a single-storey building'), {1})
        self.assertEqual(eld._storeys('three storeys of red brick'), {3})

    def test_storeys_matches_american_singular_and_plural(self):
        self.assertEqual(eld._storeys('a single-story building'), {1})
        self.assertEqual(eld._storeys('three stories of red brick'), {3})

    def test_a_british_and_an_american_read_still_disagree(self):
        # The actual failure mode this exists to catch: two reads that truly
        # disagree must still be caught regardless of which register either
        # one happens to be written in.
        conflict = eld.disagreement('a single-storey library',
                                    'a three-story library')
        self.assertIn('storeys', conflict)

    def test_outdoor_matches_british_and_american_terms(self):
        for word in ('pavement', 'sidewalk', 'car park', 'parking lot',
                    'kerb', 'curb'):
            self.assertTrue(eld.OUTDOOR.search(f'a {word} out front'), word)

    def test_indoor_matches_british_and_american_terms(self):
        for word in ('issue desk', 'circulation desk'):
            self.assertTrue(eld.INDOOR.search(f'a librarian at the {word}'), word)


if __name__ == '__main__':
    unittest.main()
