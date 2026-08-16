#!/usr/bin/env python3
"""
Post one UK public library to Bluesky, with a freely-licensed photograph.

Reads data/uk_libraries_images.csv (built by everylibrary_images.py), picks the
next library in a fixed shuffled order, and posts its name, place and photo.

Only rows marked postable=yes are eligible. That column is yes only when the row
has both an image and a resolvable photographer: an uncredited CC BY-SA image
breaches the licence, so a missing credit blocks the post rather than degrading it.

State is tracked in data/post_state.json (posted library IDs plus the running
order, so the sequence survives a corpus rebuild).

Requires:
    security add-generic-password -a "<handle>" -s "everylibrary-bluesky" -w

Usage:
    python3 everylibrary_post.py             # post one library
    python3 everylibrary_post.py --dry-run   # print the post without posting
    python3 everylibrary_post.py --count 3   # post three (for catching up)

Note: this uses argparse rather than the `'--dry-run' in sys.argv` test used by
the older bots here, so a mistyped flag is rejected instead of silently posting
live.
"""

import argparse
import csv
import hashlib
import io
import json
import random
import re
import subprocess
import sys
import time
from pathlib import Path

import requests
from atproto import Client, client_utils, models

DATA        = Path(__file__).parent / 'data'
MANIFEST    = DATA / 'uk_libraries_images.csv'
STATE_FILE  = DATA / 'post_state.json'

HANDLE           = 'everyuklibrary.bsky.social'
KEYCHAIN_SERVICE = 'everylibrary-bluesky'

MAX_CHARS       = 290      # 10-char buffer under Bluesky's 300 limit
MAX_IMAGE_BYTES = 950_000  # stay under Bluesky's ~1 MB blob limit
SHUFFLE_SEED    = 20260816 # fixed, so the running order is reproducible

# Nations to hold back from the rotation. The roster's Scottish and Welsh
# records come from Libraries Hacked, which states no data licence; the English
# portion traces to DCMS under the OGL. Set this to {'Scotland', 'Wales'} to
# pause those 384 libraries pending an answer, without rebuilding anything.
EXCLUDE_NATIONS = set()

# First line of the pinned attribution note, and the pattern used to recognise
# our own post before replacing it, so --pin never deletes something else that
# happens to be pinned.
#
# The pattern ignores trailing punctuation deliberately. Matching the heading
# exactly means that editing it orphans the previous note: changing the full
# stop to a colon once left two credits posts in the feed, because the old one
# no longer matched.
CREDITS_HEADING = 'Sources and credits:'
CREDITS_PATTERN = re.compile(r'^\s*Sources and credits\b', re.I)

USER_AGENT = 'everylibrary-bot/0.1 (https://chris-stanford.com; stanfordc+claude@mac.com)'


# ------------------------------------------------------------------- helpers


def keychain_password(account, service):
    result = subprocess.run(
        ['security', 'find-generic-password', '-a', account, '-s', service, '-w'],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(
            f'No Keychain password for account="{account}" service="{service}".\n'
            f'Add it with:\n'
            f'  security add-generic-password -a "{account}" -s "{service}" -w'
        )
    return result.stdout.strip()


def library_id(row):
    """Stable identity, so posted-state survives a corpus rebuild or reordering."""
    key = f"{row['name']}|{row['postcode']}|{row['lat']}"
    return hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {'posted': [], 'order': []}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


# Names that already say what the building is, so appending 'Library' would be
# wrong: 'Idea Store Bow', 'Kinson Hub', 'Whitley Bay Customer First Centre'.
FACILITY_WORD = re.compile(
    r'\b(centre|center|hub|store|lounge|rooms?|institute|archive|'
    r'museum|gallery|college|school|pavilion)\b', re.I)


def display_name(name):
    """The roster names 518 libraries by bare place name alone: 'Torrington',
    'Redland', 'Seaton'. A post reading just 'Torrington' never says what the
    photograph shows, and posts get reshared away from the account's context,
    so each one has to stand alone."""
    s = re.sub(r'\s+', ' ', name or '').strip()
    if 'librar' in s.lower() or FACILITY_WORD.search(s):
        return s
    return f'{s} Library'


def tag_slug(name):
    """Bluesky tags carry no spaces or punctuation, so 'Newcastle upon Tyne'
    becomes NewcastleUponTyne and "King's Lynn" becomes KingsLynn."""
    s = re.sub(r"[’'`]", '', name or '')
    parts = re.split(r'[^0-9A-Za-z]+', s)
    return ''.join(p[:1].upper() + p[1:] for p in parts if p)


def clean_address(address, postcode):
    """Tidy the roster's address strings and append the postcode.

    The source data carries whitespace artefacts from hand entry: 'Soho Road ,
    Birmingham', doubled spaces, stray leading and trailing commas, and the
    occasional empty component from a blank Address 2 field.
    """
    s = re.sub(r'\s+', ' ', address or '').strip()
    s = re.sub(r'\s*,\s*', ', ', s)        # 'Soho Road , Birmingham' and 'Limited,The Brig'
    s = re.sub(r'(?:,\s*){2,}', ', ', s)   # repeated commas from an empty component
    s = s.strip(' ,')

    pc = re.sub(r'\s+', ' ', (postcode or '').strip().upper())
    if not s:
        return pc
    return f'{s} {pc}' if pc else s


TRAILING_ADMIN = {'council', 'borough', 'county', 'metropolitan', 'district',
                  'city', 'unitary', 'authority', 'libraries', 'library'}


def short_place(authority):
    """Trim the bureaucratic wrapper off an authority name, so posts read
    'Wandsworth' rather than 'London Borough of Wandsworth'.

    Strips trailing admin words one at a time rather than matching whole
    phrases: 'Neath Port Talbot County Borough Council' needs three passes, and
    a single-phrase regex catches only the last two, leaving 'Neath Port Talbot
    County'. Never returns an empty string.
    """
    s = re.sub(r'^(London Borough of|Royal Borough of|City and County of|'
               r'City of|County of|The)\s+', '', authority or '', flags=re.I)

    words = s.split()
    while len(words) > 1 and words[-1].lower().strip(',') in TRAILING_ADMIN:
        words.pop()

    s = re.sub(r'\s+', ' ', ' '.join(words)).strip(' ,')
    return s or authority


# ---------------------------------------------------------------- the image


def commons_filepath_url(image_title, width):
    """Special:FilePath renders any width on demand, so we can step down until
    the payload fits rather than downloading a huge original first."""
    fname = image_title.replace('File:', '').replace(' ', '_')
    return (f'https://commons.wikimedia.org/wiki/Special:FilePath/'
            f'{requests.utils.quote(fname)}?width={width}')


def downscale(data, max_bytes=MAX_IMAGE_BYTES):
    """Last resort for a source that gives us no width control (Geograph)."""
    from PIL import Image
    img = Image.open(io.BytesIO(data))
    if img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')
    for scale in (1.0, 0.8, 0.6, 0.45):
        buf = io.BytesIO()
        w, h = int(img.width * scale), int(img.height * scale)
        img.resize((w, h)).save(buf, format='JPEG', quality=86, optimize=True)
        if buf.tell() <= max_bytes:
            return buf.getvalue()
    return buf.getvalue()


def fetch_image(row, retries=3):
    """Fetch the photo. Commons gets stepped widths; Geograph is already small,
    and falls back to a local downscale if it ever isn't.

    Retries on transient network failures: these bots have died before on a
    one-off EHOSTUNREACH at fire time.
    """
    session = requests.Session()
    session.headers.update({'User-Agent': USER_AGENT})

    if row['image_source'] == 'geograph':
        urls = [row['image_url']]
    else:
        urls = [commons_filepath_url(row['image_title'], w)
                for w in (1200, 1000, 800, 640, 500)]

    last_error = None
    for url in urls:
        for attempt in range(retries):
            try:
                r = session.get(url, timeout=45)
                if r.status_code == 200 and len(r.content) > 1000:
                    if len(r.content) <= MAX_IMAGE_BYTES:
                        return r.content
                    return downscale(r.content)
                last_error = f'HTTP {r.status_code}'
            except requests.RequestException as exc:
                last_error = f'{type(exc).__name__}: {exc}'
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Could not fetch image for {row['name']}: {last_error}")


# ----------------------------------------------------------------- the post


def build_post(row):
    """Name, place, then the credit. CC BY-SA wants the creator named, the
    licence named and the licence linked, so the licence itself is the link."""
    tb = client_utils.TextBuilder()
    tb.text(display_name(row['name']) + '\n')

    address = clean_address(row.get('address'), row.get('postcode'))
    if address:
        tb.text(address + '\n')

    place = short_place(row['authority'])
    year = (row.get('year_opened') or '').strip()
    tb.text(f'{place} · opened {year}\n\n' if year.isdigit() else f'{place}\n\n')

    # One link only: the photographer's name, pointing at the file's own page.
    # CC BY-SA 4.0 s3(a)(2) allows the attribution conditions to be satisfied
    # "by providing a URI or hyperlink to a resource that includes the required
    # information", and the Commons file page carries the author, the licence,
    # the deed link and the source. The licence stays named in plain text, so a
    # reader still sees the terms without a second run of blue swallowing the
    # credit line.
    tb.text('📷 ')
    if row.get('credit_page'):
        tb.link(row['photographer'], row['credit_page'])
    else:
        tb.text(row['photographer'])

    tb.text(f" · {row['licence'] or 'CC BY-SA'}")

    # Two tags, no more. #Libraries for the topic, the town so local people can
    # find their own branch. Skipped when the roster has no usable town: a wrong
    # tag is worse than none, and 3% of Address 2 values are streets.
    tags = ['Libraries']
    town = tag_slug(row.get('town'))
    if town and town.lower() != 'libraries':
        tags.append(town)

    tb.text('\n\n')
    for i, tag in enumerate(tags):
        if i:
            tb.text(' ')
        tb.tag(f'#{tag}', tag)

    text = tb.build_text()
    if len(text) > MAX_CHARS:
        raise RuntimeError(f'Post too long ({len(text)} chars): {row["name"]}')
    return tb


def build_credits():
    """The attribution that will not fit in a bio.

    OGL (the DCMS roster) and ODbL (OpenStreetMap) both require attribution, and
    bios carry no link facets, so the working links have to live in a post.
    """
    tb = client_utils.TextBuilder()
    tb.text(CREDITS_HEADING + '\n\n')
    tb.text('Photographs: ')
    tb.link('Wikimedia Commons', 'https://commons.wikimedia.org')
    tb.text(' and ')
    tb.link('Geograph', 'https://www.geograph.org.uk')
    tb.text(' contributors, credited by name on every post.\n\n')
    tb.text('Libraries: ')
    tb.link('DCMS', 'https://www.data.gov.uk/dataset/'
                    'public-libraries-in-england-basic-dataset')
    tb.text(' (OGL) and ')
    tb.link('Libraries Hacked', 'https://www.librarieshacked.org')
    tb.text('.\nBuildings: ')
    tb.link('OpenStreetMap', 'https://www.openstreetmap.org/copyright')
    tb.text(' contributors (ODbL).')
    return tb


def pin_credits(dry_run=False):
    """Post the credits and pin them to the profile.

    Pinning means rewriting the whole profile record, so this is a strict
    read-modify-write: everything else on the record is carried across
    untouched, and swap_record makes the server reject the write if the profile
    changed underneath us.
    """
    tb = build_credits()
    text = tb.build_text()
    print('-' * 60)
    print(text)
    print(f'[{len(text)} chars]')
    if dry_run:
        print('\nDry run: not posted, profile untouched.')
        return

    client = Client()
    client.login(HANDLE, keychain_password(HANDLE, KEYCHAIN_SERVICE))

    # Posts cannot be edited, so re-running this replaces the old note rather
    # than leaving an orphan behind. Only ever deletes a post that is both
    # pinned and recognisably ours.
    existing = client.app.bsky.actor.profile.get(client.me.did, 'self')
    old = existing.value.pinned_post
    if old:
        try:
            rec = client.get_post(old.uri.rsplit('/', 1)[-1], profile_identify=client.me.did)
            if CREDITS_PATTERN.match(rec.value.text):
                client.delete_post(old.uri)
                print(f'Replaced previous credits post: {old.uri.rsplit("/", 1)[-1]}')
            else:
                print('Existing pinned post is not the credits note; leaving it in place.')
        except Exception as exc:
            print(f'Could not inspect the pinned post ({exc}); leaving it alone.')

    posted = client.send_post(text=tb, langs=['en'])
    print(f'Posted: {posted.uri}')

    existing = client.app.bsky.actor.profile.get(client.me.did, 'self')
    profile = existing.value
    profile.pinned_post = models.ComAtprotoRepoStrongRef.Main(
        uri=posted.uri, cid=posted.cid)

    client.com.atproto.repo.put_record(models.ComAtprotoRepoPutRecord.Data(
        repo=client.me.did,
        collection='app.bsky.actor.profile',
        rkey='self',
        record=profile,
        swap_record=existing.cid,
    ))
    print('Pinned to profile.')


def build_alt(row):
    place = short_place(row['authority'])
    address = clean_address(row.get('address'), row.get('postcode'))
    name = display_name(row['name'])
    where = f'{name}, {address}' if address else f'{name}, {place}'
    bits = [f"Photograph of {where}."]
    if row.get('year_opened', '').isdigit():
        bits.append(f"The library opened in {row['year_opened']}.")
    bits.append(f"Photograph by {row['photographer']}, licensed {row['licence']}.")
    return ' '.join(bits)[:1900]  # Bluesky alt-text cap is 2000


# ----------------------------------------------------------------- selection


def load_rows():
    if not MANIFEST.exists():
        sys.exit(f'Manifest not found: {MANIFEST}\nRun everylibrary_images.py first.')
    with MANIFEST.open() as f:
        rows = list(csv.DictReader(f))
    postable = [r for r in rows if r.get('postable') == 'yes'
                and r.get('nation') not in EXCLUDE_NATIONS]
    if not postable:
        sys.exit('No postable rows in the manifest.')
    return rows, postable


def next_library(postable, state, skip=()):
    """Fixed shuffled order, so the feed does not march through one county at a
    time, but the sequence is reproducible and stable across rebuilds."""
    by_id = {library_id(r): r for r in postable}

    order = [i for i in state.get('order', []) if i in by_id]
    known = set(order)
    fresh = sorted(i for i in by_id if i not in known)
    if fresh:
        random.Random(SHUFFLE_SEED + len(order)).shuffle(fresh)
        order += fresh
        state['order'] = order

    posted = set(state.get('posted', [])) | set(skip)
    for lib_id in order:
        if lib_id not in posted:
            return lib_id, by_id[lib_id]
    return None, None


# --------------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser(description='Post one UK library to Bluesky.')
    ap.add_argument('--dry-run', action='store_true',
                    help='print the post without posting')
    ap.add_argument('--count', type=int, default=1,
                    help='how many to post in this run (default 1)')
    ap.add_argument('--pin', action='store_true',
                    help='post the sources-and-credits note and pin it')
    args = ap.parse_args()

    if args.pin:
        pin_credits(dry_run=args.dry_run)
        return

    rows, postable = load_rows()
    state = load_state()
    print(f'{len(postable)} postable of {len(rows)} libraries; '
          f'{len(state.get("posted", []))} already posted')

    client = None
    previewed = []   # dry runs write no state, so advance the preview locally
    for n in range(args.count):
        lib_id, row = next_library(postable, state, skip=previewed)
        if not row:
            print('Nothing left to post: the whole corpus has been through.')
            break

        # Defence in depth: never post an uncredited CC BY-SA image.
        if not row.get('photographer'):
            print(f'SKIP (no credit): {row["name"]}')
            state.setdefault('posted', []).append(lib_id)
            save_state(state)
            continue

        tb = build_post(row)
        alt = build_alt(row)

        print('-' * 60)
        print(tb.build_text())
        print(f'[alt] {alt}')
        print(f'[src] {row["image_source"]} · {row["credit_page"]}')

        if args.dry_run:
            previewed.append(lib_id)
            continue

        image = fetch_image(row)
        if client is None:
            client = Client()
            client.login(HANDLE, keychain_password(HANDLE, KEYCHAIN_SERVICE))

        # Without an aspect ratio Bluesky has to guess, and reflows or crops the
        # image once it loads. The everylot bots all omit this; it costs nothing.
        from PIL import Image
        with Image.open(io.BytesIO(image)) as im:
            ratio = models.AppBskyEmbedDefs.AspectRatio(width=im.width, height=im.height)

        client.send_images(text=tb, images=[image], image_alts=[alt],
                           image_aspect_ratios=[ratio], langs=['en'])
        state.setdefault('posted', []).append(lib_id)
        save_state(state)
        print(f'Posted ({len(state["posted"])}/{len(postable)}).')

        if n + 1 < args.count:
            time.sleep(2)

    if args.dry_run:
        print('\nDry run: nothing posted, no state written.')


if __name__ == '__main__':
    main()
