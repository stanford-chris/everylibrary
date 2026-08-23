#!/usr/bin/env python3
"""
Act on the roster drift everylibrary_roster_check.py reports.

Two changes to data/uk_libraries_final.csv, and nothing else:

  ADD     libraries the live roster carries and the corpus does not. They go in
          with blank osm_id, wikidata and match_m: those come from the OSM
          spatial join that built the original corpus, and nothing here re-runs
          it. The image stages key on `osm_id or lat,lon`, so a blank OSM column
          costs nothing, and stage 2b reaches Wikidata photographs by position
          rather than by link, so a blank wikidata column costs little either.

  REMOVE  libraries that have vanished from the roster and have NOT been
          posted. Disappearance is the closure signal — the API's "Year closed"
          is empty on every record — so this is how the bot stops queueing a
          branch that has shut. A posted library is never removed: the post
          exists, and taking the row out would only lose the record of it.

Why append and remove rather than rebuild: an existing row is never rewritten,
so no library_id can change underneath the posted state. The corpus was built
by a spatial join against Overpass that exists in no script here, and a rebuild
would re-derive all 3,750 rows to gain the three that changed.

Nation comes from the ONS code prefix on the local authority (E/S/W/N).
Verified 23 August 2026 against all 3,750 matched records: the prefix predicts
the corpus nation with no exceptions. An unrecognised prefix is refused, not
guessed at.

    python3 everylibrary_roster_apply.py            # report, write nothing
    python3 everylibrary_roster_apply.py --live     # apply
    python3 everylibrary_roster_apply.py --live --max-change 40

Exit 0 nothing to do or applied cleanly, 1 refused a sanity check, 2 could not
run at all.
"""

import argparse
import csv
import os
import sys
from pathlib import Path

from everylibrary_post import DATA, library_id, norm
from everylibrary_roster_check import fetch_live, load_posted

CORPUS = DATA / 'uk_libraries_final.csv'

COLUMNS = ['name', 'authority', 'nation', 'address', 'town', 'postcode',
           'lat', 'lon', 'type', 'statutory', 'year_opened', 'url',
           'osm_type', 'osm_id', 'wikidata', 'match_m']

NATIONS = {'E': 'England', 'S': 'Scotland',
           'W': 'Wales', 'N': 'Northern Ireland'}

# A single month should move this roster by a handful of libraries: the first
# check, on 17 August 2026, found three additions and no closures. Anything
# resembling a mass change is a bad read of the API dressed as data — the
# ?offset= trap produced a confident 1,000-row roster and would have reported
# 1,188 closures — so it is refused rather than applied.
MAX_CHANGE = 40


def log(msg):
    print(msg, flush=True)


def load_corpus():
    with open(CORPUS) as f:
        rows = list(csv.DictReader(f))
        if list(rows[0]) != COLUMNS:
            sys.exit(f'corpus columns are not what this expects:\n'
                     f'  {list(rows[0])}')
    return rows


def to_row(rec):
    """One live API record as a corpus row, or a string saying why not."""
    code = (rec.get('Local authority code') or '').strip()
    nation = NATIONS.get(code[:1])
    if not nation:
        return f'unrecognised local authority code {code!r}'

    lat = (rec.get('Latitude') or '').strip()
    lon = (rec.get('Longitude') or '').strip()
    if not lat or not lon:
        return 'no coordinates'
    try:
        float(lat), float(lon)
    except ValueError:
        return f'unparseable coordinates {lat!r},{lon!r}'

    name = (rec.get('Library name') or '').strip()
    if not name:
        return 'no name'

    # The corpus joins the two address lines and keeps the second as the town,
    # which is what the original build did: "Castle Hill, Torrington" / "Torrington".
    a1 = (rec.get('Address 1') or '').strip()
    a2 = (rec.get('Address 2') or '').strip()

    return {
        'name': name,
        'authority': (rec.get('Local authority') or '').strip(),
        'nation': nation,
        'address': ', '.join(p for p in (a1, a2) if p),
        'town': a2,
        'postcode': (rec.get('Postcode') or '').strip(),
        'lat': lat,
        'lon': lon,
        'type': (rec.get('Type of library') or '').strip(),
        'statutory': (rec.get('Statutory') or '').strip(),
        'year_opened': (rec.get('Year opened') or '').strip(),
        'url': (rec.get('URL') or '').strip(),
        'osm_type': '', 'osm_id': '', 'wikidata': '', 'match_m': '',
    }


def write_corpus(rows):
    tmp = str(CORPUS) + '.tmp'
    with open(tmp, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, CORPUS)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--live', action='store_true',
                    help='write the corpus; without this it only reports')
    ap.add_argument('--max-change', type=int, default=MAX_CHANGE,
                    help=f'refuse a run touching more rows than this (default {MAX_CHANGE})')
    args = ap.parse_args()

    corpus = load_corpus()
    by_key = {norm(r['name'], r['postcode']): r for r in corpus}

    try:
        live = fetch_live()
    except Exception as exc:                       # noqa: BLE001 — reported, not raised
        log(f'!! could not read the live roster: {exc}')
        return 2
    live_by_key = {}
    for rec in live:
        live_by_key.setdefault(norm(rec.get('Library name'), rec.get('Postcode')), rec)

    log(f'live {len(live)} · corpus {len(corpus)}')

    # ---- additions -------------------------------------------------------
    additions, refused = [], []
    for key, rec in live_by_key.items():
        if key in by_key:
            continue
        row = to_row(rec)
        if isinstance(row, str):
            refused.append((rec.get('Library name'), row))
            continue
        additions.append(row)

    # An id already in use would silently merge two libraries into one entry in
    # post_state.json and alt_text.json. Verified unique across the corpus, but
    # a new arrival is exactly where it could stop being true.
    existing_ids = {library_id(r) for r in corpus}
    clean = []
    for row in additions:
        lid = library_id(row)
        if lid in existing_ids:
            refused.append((row['name'], f'library_id {lid} already in the corpus'))
            continue
        existing_ids.add(lid)
        clean.append(row)
    additions = clean

    # ---- removals --------------------------------------------------------
    # Only libraries that have gone AND were never posted. Without the posted
    # state this cannot be judged, so nothing is removed rather than guessed at.
    posted = load_posted()
    removals, kept_posted = [], []
    if not posted:
        log('   NOT CHECKED: no post_state.json here, so nothing is removed')
    else:
        for r in corpus:
            if norm(r['name'], r['postcode']) in live_by_key:
                continue
            (kept_posted if library_id(r) in posted else removals).append(r)

    log(f'   additions {len(additions)} · removals {len(removals)}'
        f' · vanished but already posted {len(kept_posted)}'
        f' · refused {len(refused)}')
    for name, why in refused:
        log(f'     refused: {name} — {why}')
    for row in additions:
        log(f'     + {row["name"]}, {row["town"]} ({row["nation"]})')
    for row in removals:
        log(f'     - {row["name"]}, {row["town"]} ({row["nation"]})')
    for row in kept_posted:
        log(f'     ! {row["name"]} has gone from the roster but was already posted')

    touched = len(additions) + len(removals)
    if not touched:
        return 0
    if touched > args.max_change:
        log(f'!! {touched} rows would change, over the {args.max_change} bound. '
            f'Refusing. Read the roster report before raising it.')
        return 1

    if not args.live:
        log('   dry run: nothing written. Pass --live to apply.')
        return 0

    gone = {id(r) for r in removals}
    write_corpus([r for r in corpus if id(r) not in gone] + additions)
    log(f'   corpus written: {len(corpus) - len(removals) + len(additions)} rows')
    return 0


if __name__ == '__main__':
    sys.exit(main())
