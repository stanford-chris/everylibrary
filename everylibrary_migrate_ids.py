#!/usr/bin/env python3
"""
One-off: re-key library_id from sha1(name|postcode|lat) to sha1(name|postcode).

Why: the latitude in the old key made identity depend on a number the upstream
roster corrects. A coordinate refined by a metre minted a new id, the library
read as never-posted, and it would have gone out twice. everylibrary_roster_
check.py already avoided library_id for exactly this reason and matched on
name and postcode instead; this makes the real id agree with the match key.

Run once, on each machine holding state. It rewrites, in place and after
writing a .bak beside each:

    data/alt_text.json    keys are library_id            (in the repo)
    data/post_state.json  "posted" and "order" entries   (per-machine, ignored)

Idempotent: a file already carrying new-format ids is reported and left alone.
Mixed old and new ids in one file is an error, not something to guess at.

    python3 everylibrary_migrate_ids.py --dry-run
    python3 everylibrary_migrate_ids.py
"""

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

DATA = Path(__file__).parent / 'data'
CORPUS = DATA / 'uk_libraries_final.csv'
ALT = DATA / 'alt_text.json'
POST_STATE = DATA / 'post_state.json'


def old_id(row):
    key = f"{row['name']}|{row['postcode']}|{row['lat']}"
    return hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]


def new_id(row):
    """Must stay identical to everylibrary_post.library_id. Copied rather than
    imported because that module pulls in atproto, which is not installed on
    every machine this has to run on."""
    n = re.sub(r'\s+', ' ', (row['name'] or '')).strip().lower()
    p = re.sub(r'\s+', '', (row['postcode'] or '')).upper()
    return hashlib.sha1(f'{n}|{p}'.encode('utf-8')).hexdigest()[:12]


def build_map():
    """old id -> new id, over the whole corpus, not just the postable rows."""
    with open(CORPUS) as f:
        rows = list(csv.DictReader(f))

    mapping, seen = {}, {}
    for r in rows:
        n = new_id(r)
        if n in seen:
            sys.exit(f"collision: {r['name']!r} and {seen[n]!r} share "
                     f"name+postcode. Migration refused.")
        seen[n] = r['name']
        mapping[old_id(r)] = n

    if len(mapping) != len(rows):
        sys.exit(f"the old ids are not unique either ({len(mapping)} for "
                 f"{len(rows)} rows). Migration refused.")
    return mapping, len(rows)


def classify(ids, mapping):
    """'old', 'new', 'empty', or a list of ids belonging to neither set."""
    ids = list(ids)
    if not ids:
        return 'empty'
    news = set(mapping.values())
    old = [i for i in ids if i in mapping]
    new = [i for i in ids if i in news]
    if len(old) == len(ids):
        return 'old'
    if len(new) == len(ids):
        return 'new'
    return [i for i in ids if i not in mapping and i not in news] or 'mixed'


def migrate_alt(mapping, dry_run):
    if not ALT.exists():
        print(f'alt_text.json     not present, skipped')
        return
    alt = json.loads(ALT.read_text())
    state = classify(alt, mapping)
    if state == 'new':
        print(f'alt_text.json     already migrated ({len(alt)} entries)')
        return
    if state != 'old':
        sys.exit(f'alt_text.json: expected all-old ids, got {state}')

    out = {mapping[k]: v for k, v in alt.items()}
    assert len(out) == len(alt), 'alt_text.json lost entries in the re-key'
    print(f'alt_text.json     {len(alt)} entries re-keyed'
          + ('  (dry run)' if dry_run else ''))
    if not dry_run:
        shutil.copy2(ALT, str(ALT) + '.bak')
        ALT.write_text(json.dumps(out, ensure_ascii=False, indent=1))


def migrate_post_state(mapping, dry_run):
    if not POST_STATE.exists():
        print('post_state.json   not present on this machine, skipped')
        return
    st = json.loads(POST_STATE.read_text())
    posted, order = st.get('posted', []), st.get('order', [])

    for label, ids in (('posted', posted), ('order', order)):
        state = classify(ids, mapping)
        if state == 'new':
            print(f'post_state.json   {label} already migrated ({len(ids)})')
            continue
        if state == 'empty':
            continue
        if state != 'old':
            sys.exit(f'post_state.json {label}: expected all-old ids, got {state}')

        st[label] = [mapping[i] for i in ids]
        assert len(st[label]) == len(ids), f'{label} changed length'
        assert len(set(st[label])) == len(set(ids)), f'{label} ids collapsed'
        print(f'post_state.json   {label}: {len(ids)} re-keyed'
              + ('  (dry run)' if dry_run else ''))

    if not dry_run:
        shutil.copy2(POST_STATE, str(POST_STATE) + '.bak')
        POST_STATE.write_text(json.dumps(st))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dry-run', action='store_true',
                    help='report what would change, write nothing')
    args = ap.parse_args()

    mapping, n = build_map()
    print(f'corpus            {n} rows, {len(mapping)} ids, no collisions')
    migrate_alt(mapping, args.dry_run)
    migrate_post_state(mapping, args.dry_run)


if __name__ == '__main__':
    main()
