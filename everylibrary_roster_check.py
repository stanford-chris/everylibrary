#!/usr/bin/env python3
"""
everylibrary_roster_check.py — is the frozen roster still true?

data/uk_libraries_images.csv is a snapshot taken on 16 August 2026 and nothing
re-fetches it. At two or three posts a day the rotation runs for two to three
years, so the bot will eventually post a library that has closed, and will
never post one that opened after the snapshot.

This detects that drift. It does not fix it: nothing here writes to the corpus,
the state file or the manifest. It fetches the live roster, compares, and
reports. Deciding what to do about a hit is a person's job.

Why detect rather than refresh. A refresh was the obvious move and the wrong
first one: library_id used to hash the latitude, so an upstream coordinate
correction minted a new id, the library read as never-posted, and it would have
gone out a second time. That was fixed on 23 August 2026 — library_id and the
match key below are now the same function, norm(), imported from the poster —
and everylibrary_roster_apply.py acts on what this finds. This still only
detects; applying is a separate, deliberate step. This measures it. If a year of reports shows three vanished
libraries you handle them by hand; if it shows sixty, build the refresh then,
knowing which fields actually drift.

Disappearance IS the closure signal. The API carries a "Year closed" field, but
every one of the 3,753 live records has it empty, so it cannot be relied on to
mean anything. It does not matter: if the endpoint stops listing a library,
that is the event worth knowing about, whether or not the field is ever filled.

Report lands in ~/Library/Logs/everylibrary-roster.md with a notification,
following harden_audit.sh: a clean run writes nothing and deletes any previous
report, so the file's existence means there is something to read.

Usage:
    python3 everylibrary_roster_check.py
    python3 everylibrary_roster_check.py --quiet    # no desktop notification
    python3 everylibrary_roster_check.py --json     # machine-readable, no report

Exit codes: 0 clean, 1 drift found, 2 the check itself broke. A broken check is
not a pass, so it is never reported as one.
"""

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from everylibrary_post import (MANIFEST, STATE_FILE, EXCLUDE_NATIONS,
                               USER_AGENT, library_id, norm)

API = 'https://api.librarydata.uk/libraries'
PAGE_SIZE = 1000

# The API paginates with ?page=. It also accepts ?offset= and silently ignores
# it, returning page 1 every time — which is exactly how a sweep produces a
# confident, complete-looking 1,000-row "full roster". Page numbers only, and
# the loop below verifies each page actually moved by checking the ids it got
# back rather than trusting the count.
MAX_PAGES = 20

REPORT = Path.home() / 'Library/Logs/everylibrary-roster.md'


def fetch_live():
    """Every page of the live roster, or an exception. Never a partial answer:
    a short read here would look exactly like a mass closure."""
    session = requests.Session()
    session.headers.update({'User-Agent': USER_AGENT})

    records, seen_pages = {}, []
    for page in range(1, MAX_PAGES + 1):
        r = session.get(API, timeout=90,
                        params={'limit': PAGE_SIZE, 'page': page})
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break

        ids = {rec.get('id') for rec in batch}
        # A page identical to one already seen means the parameter stopped
        # being honoured. Stop rather than loop, and treat it as a broken run.
        if ids in seen_pages:
            raise RuntimeError(
                f'page={page} returned records already seen: the API stopped '
                f'paginating after {len(records)} records. Not a complete roster.')
        seen_pages.append(ids)

        for rec in batch:
            records[rec['id']] = rec
        if len(batch) < PAGE_SIZE:
            break
    else:
        raise RuntimeError(f'still receiving full pages after {MAX_PAGES}; '
                           f'MAX_PAGES is too low for a roster this size.')

    if len(records) < 1000:
        raise RuntimeError(f'only {len(records)} records returned; the roster '
                           f'held 3,753 on 17 August 2026. Refusing to report '
                           f'drift against a partial fetch.')

    # ⚠️ POSTCODE_FIXES corrects a bad postcode AT THE SOURCE, before anything
    # downstream computes a match key from it. Applied here rather than by
    # hand-editing the corpus row: this function is the one place roster_check
    # AND roster_apply (which imports it) both see the live data, so a fix
    # here can never drift into a match-key mismatch the way editing only the
    # corpus would. Confirmed 9 September 2026 by matching the record's own
    # Address 1/2/3 (Welfare Hall, Meinciau Road, Pontyates) against
    # Carmarthenshire County Council's own page for the library, which states
    # the postcode as SA15 5TR — SA15 5SC does not resolve on postcodes.io at
    # all and is presumably a scan/typo somewhere upstream in the roster API.
    # Keyed on the record's own stable `id`, never on name+postcode, since
    # correcting the postcode is exactly what would break a name+postcode key.
    POSTCODE_FIXES = {
        4079: 'SA15 5TR',  # Pontyates Library; API serves SA15 5SC (bad)
    }
    for rec in records.values():
        fix = POSTCODE_FIXES.get(rec.get('id'))
        if fix:
            rec['Postcode'] = fix

    return list(records.values())


def load_snapshot():
    rows = list(csv.DictReader(MANIFEST.open()))
    postable = [r for r in rows
                if r.get('postable') == 'yes'
                and r.get('nation') not in EXCLUDE_NATIONS]
    return rows, postable


def load_posted():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()).get('posted', []))
    return set()


def compare():
    live = fetch_live()
    rows, postable = load_snapshot()
    posted = load_posted()

    live_keys = {norm(r.get('Library name'), r.get('Postcode')) for r in live}
    snap_keys = {norm(r['name'], r['postcode']) for r in rows}

    # Vanished: in our rotation, gone from the roster. Split by whether it has
    # already been posted, because only one of those is still actionable — a
    # posted library cannot be unposted, and Bluesky posts cannot be edited.
    vanished_queued, vanished_done = [], []
    for r in postable:
        if norm(r['name'], r['postcode']) in live_keys:
            continue
        (vanished_done if library_id(r) in posted else vanished_queued).append(r)

    added = [r for r in live
             if norm(r.get('Library name'), r.get('Postcode')) not in snap_keys]

    # Closures the source states outright. Empty for every record as of
    # 17 August 2026, so this is a tripwire for the field starting to be
    # populated, not a check anyone should depend on.
    stated_closed = [r for r in live if (r.get('Year closed') or '').strip()]

    return {
        'checked_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'live_records': len(live),
        'snapshot_rows': len(rows),
        'rotation_size': len(postable),
        'posted_so_far': len(posted),
        'vanished_queued': vanished_queued,
        'vanished_posted': vanished_done,
        'added': added,
        'stated_closed': stated_closed,
    }


def write_report(res):
    def block(title, note, items, render):
        out = [f'\n## {title} ({len(items)})\n', note + '\n']
        for it in items[:200]:
            out.append(f'- {render(it)}')
        if len(items) > 200:
            out.append(f'\n_{len(items) - 200} more not listed._')
        return '\n'.join(out) + '\n'

    parts = [
        f"# everylibrary roster drift — {datetime.now().strftime('%-d %B %Y')}\n",
        f"Live roster {res['live_records']} records against a snapshot of "
        f"{res['snapshot_rows']}. Rotation is {res['rotation_size']}, "
        f"{res['posted_so_far']} posted so far.\n",
        "\nDetection only: nothing was changed. The manifest, the post state "
        "and the alt text are all untouched.\n",
    ]

    if res['vanished_queued']:
        parts.append(block(
            'Queued, but no longer in the roster',
            'Candidate closures, still waiting to post. These are the '
            'actionable ones: add their library_id to a holdback list to pull '
            'them from rotation.',
            res['vanished_queued'],
            lambda r: f"{r['name']} · {r['postcode']} · {r['authority']} "
                      f"· `{library_id(r)}`"))

    if res['vanished_posted']:
        parts.append(block(
            'Already posted, and no longer in the roster',
            'For the record only. The post is out and cannot be edited.',
            res['vanished_posted'],
            lambda r: f"{r['name']} · {r['postcode']} · {r['authority']}"))

    if res['added']:
        parts.append(block(
            'In the roster, not in the snapshot',
            'New or renamed entries. They will never post: the rotation reads '
            'the frozen CSV. Adding them needs an image-pipeline run.',
            res['added'],
            lambda r: f"{r.get('Library name')} · {r.get('Postcode')} "
                      f"· {r.get('Local authority')}"))

    if res['stated_closed']:
        parts.append(block(
            'Roster states a closure year',
            'The "Year closed" field was empty for every record when this '
            'check was written. If entries appear here, the field has started '
            'being populated and is worth trusting.',
            res['stated_closed'],
            lambda r: f"{r.get('Library name')} · closed {r.get('Year closed')}"))

    REPORT.write_text('\n'.join(parts))


def main():
    ap = argparse.ArgumentParser(description='Check the frozen roster against the live one.')
    ap.add_argument('--quiet', action='store_true', help='no desktop notification')
    ap.add_argument('--json', action='store_true', help='print JSON, write no report')
    args = ap.parse_args()

    try:
        res = compare()
    except Exception as exc:
        print(f'FAILED: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 2

    drift = (len(res['vanished_queued']) + len(res['vanished_posted'])
             + len(res['added']) + len(res['stated_closed']))

    if args.json:
        slim = dict(res)
        for k in ('vanished_queued', 'vanished_posted'):
            slim[k] = [{'name': r['name'], 'postcode': r['postcode'],
                        'id': library_id(r)} for r in res[k]]
        slim['added'] = [{'name': r.get('Library name'),
                          'postcode': r.get('Postcode')} for r in res['added']]
        slim['stated_closed'] = [{'name': r.get('Library name'),
                                  'year_closed': r.get('Year closed')}
                                 for r in res['stated_closed']]
        print(json.dumps(slim, indent=2))
        return 1 if drift else 0

    print(f"live {res['live_records']} · snapshot {res['snapshot_rows']} · "
          f"rotation {res['rotation_size']}")
    print(f"vanished (queued) {len(res['vanished_queued'])} · "
          f"vanished (posted) {len(res['vanished_posted'])} · "
          f"added {len(res['added'])} · stated closed {len(res['stated_closed'])}")

    if not drift:
        print('Clean — the frozen roster still matches. No report written.')
        REPORT.unlink(missing_ok=True)
        return 0

    write_report(res)
    print(f'Report written to {REPORT}')
    if not args.quiet:
        subprocess.run(
            ['osascript', '-e',
             f'display notification "{drift} change(s) — see everylibrary-roster.md" '
             f'with title "everylibrary roster"'],
            capture_output=True)
    return 1


if __name__ == '__main__':
    sys.exit(main())
