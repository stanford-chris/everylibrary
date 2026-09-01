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
from atproto import Client, client_utils, exceptions, models

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
CREDITS_HEADING = 'Sources and credits 📚'
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


def login_client(retries=4):
    """Log in, retrying transient network failures at fire time.

    The 17 August 01:00 run died here rather than posting: atproto's login()
    creates a session and then calls getProfile to populate client.me, and the
    getProfile leg timed out. It is the same sub-ten-second network blip that
    has cost the other launchd bots posts, so it gets the same linear backoff
    as fetch_image.

    Only the login retries. A failed send_images is left to fail, because a
    timeout there cannot distinguish a post that never landed from one that
    landed with the response lost, and retrying the second case double-posts.
    """
    password = keychain_password(HANDLE, KEYCHAIN_SERVICE)  # outside the loop: a
    last_error = None                                       # missing key is not transient
    for attempt in range(retries):
        try:
            client = Client()
            client.login(HANDLE, password)
            return client
        except exceptions.NetworkError as exc:
            last_error = f'{type(exc).__name__}: {exc}'
            print(f'Login attempt {attempt + 1}/{retries} failed ({last_error})')
            if attempt + 1 < retries:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f'Could not log in to Bluesky after {retries} attempts: {last_error}')


def norm(name, postcode):
    """The identity of a library: its name and postcode, loosely matched.

    Deliberately tolerant. Upstream tidies its data — trailing spaces, a
    doubled space, a postcode written in lower case — and none of that makes
    it a different library. everylibrary_roster_check.py imports this rather
    than keeping its own copy, so the drift check and the posted-state key can
    never disagree about what counts as the same library.
    """
    n = re.sub(r'\s+', ' ', (name or '')).strip().lower()
    p = re.sub(r'\s+', '', (postcode or '')).upper()
    return (n, p)


def library_id(row):
    """Stable identity, so posted-state survives a corpus rebuild or reordering.

    The latitude used to be in this key, which made identity depend on a number
    the upstream roster corrects: a coordinate refined by a metre minted a new
    id, the library read as never-posted, and it would have gone out twice.
    Verified against the 3,750-row corpus: name and postcode alone are unique,
    normalised or not.

    Both alt_text.json and post_state.json are keyed on this. Changing it again
    means running everylibrary_migrate_ids.py against both, on every machine.
    """
    n, p = norm(row['name'], row['postcode'])
    return hashlib.sha1(f'{n}|{p}'.encode('utf-8')).hexdigest()[:12]


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {'posted': [], 'order': []}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def typographic(s):
    """Curly quotes and apostrophes, which is the house style.

    Applied to the leaf helpers rather than to the finished post, for two
    reasons. TextBuilder records each facet as a byte range into the text
    already emitted, and a straight apostrophe is one byte where a curly one is
    three, so curling the assembled string silently slides every link off the
    words it belongs to. And `image_title` is a Commons filename that reaches a
    URL: "File:Airedale Centre - Queen's Park Drive..." must keep its straight
    apostrophe or Special:FilePath returns a 404.

    23 library names, 77 addresses, 7 towns and 4 photographers in the roster
    carry a straight apostrophe — St. John's Wood, Bishop's Stortford, Dai
    O'Nysius — and every one of them went out uncurled until 20 August 2026.
    """
    out, prev = [], ' '
    for i, ch in enumerate(s):
        if ch == '"':
            out.append('\u201c' if prev in ' ([{\n' else '\u201d')
        elif ch == "'":
            # Opening position gets a left single quote, exactly as a double
            # quote does. The model labels signage in single quotes constantly
            # ('Public Library', 'Parks & Recreation', 'LIBRARY') 111 times
            # across the two alt-text stores, and closing both ends reads as a
            # typo. There is not one elision ("'90s", "'tis") in either corpus,
            # so the only exception needed is the one below.
            #
            # WARNING: the "'s" guard is not hypothetical. Wikipedia's Carnegie
            # lists carry two notes with a space before the possessive, "Thomas
            # Jefferson 's Monticello" and "Pacific University 's first
            # library", and without it those read "Jefferson \u2018s", which is
            # worse than the straight apostrophe this whole change is fixing.
            rest = s[i + 1:i + 3]
            opening = prev in ' ([{\n' and not (
                rest[:1] == 's' and not rest[1:2].isalpha())
            out.append('\u2018' if opening else '\u2019')
        else:
            out.append(ch)
        prev = ch
    return ''.join(out)


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
    s = typographic(re.sub(r'\s+', ' ', name or '').strip())
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
    return typographic(f'{s} {pc}' if pc else s)


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
    return typographic(s or authority)


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

    # Link the name to the library's own page where one survived checking.
    # A facet annotates text that is already there, so the link costs nothing
    # against the 300-character limit. Getting on for two in five postable
    # libraries have no url that survived checking, and go out as plain text.
    name = display_name(row['name'])
    url = (row.get('url') or '').strip()
    if url:
        tb.link(name, url)
        tb.text(' 📚\n')
    else:
        tb.text(name + ' 📚\n')

    address = clean_address(row.get('address'), row.get('postcode'))
    if address:
        tb.text(address + '\n')

    lat, lon = (row.get('lat') or '').strip(), (row.get('lon') or '').strip()
    if lat and lon:
        tb.link('📍 Map', f'https://www.google.com/maps?q={lat},{lon}')
        tb.text('\n')

    place = short_place(row['authority'])

    # Name the nation. "Medway" places nothing for a reader outside the UK, and
    # the feed is not a UK-only room. The guard matters for Northern Ireland,
    # where the roster's authority IS "Northern Ireland" and the naive version
    # gives "Northern Ireland, Northern Ireland".
    nation = (row.get('nation') or '').strip()
    if nation and nation.lower() != place.lower():
        place = f'{place}, {nation}'
    year = (row.get('year_opened') or '').strip()
    # "library since", not "opened", because the roster's Year opened records
    # when the library began there and not when the building went up. Checked
    # against the photographs: 32 of the 191 entries dated 2000 or later are
    # described in plainly period terms — Idea Store Bow says 2002 beside a
    # three-storey red-brick Victorian building with sash windows, Littleport
    # says 2010 beside an ornate Victorian corner building. "Opened 2002" over
    # a photograph of a Victorian building is simply wrong to anyone looking at
    # it. "Library since" is true either way, which matters because the field
    # carries no definition and both readings stay possible.
    tb.text(f'{place} · library since {year}\n\n' if year.isdigit() else f'{place}\n\n')

    # One link only: the photographer's name, pointing at the file's own page.
    # CC BY-SA 4.0 s3(a)(2) allows the attribution conditions to be satisfied
    # "by providing a URI or hyperlink to a resource that includes the required
    # information", and the Commons file page carries the author, the licence,
    # the deed link and the source. The licence stays named in plain text, so a
    # reader still sees the terms without a second run of blue swallowing the
    # credit line.
    tb.text('📷 ')
    who = typographic(row['photographer'])
    if row.get('credit_page'):
        tb.link(who, row['credit_page'])
    else:
        tb.text(who)

    tb.text(typographic(f" · {row['licence'] or 'CC BY-SA'}"))

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
    tb.text('📷 Photographs: ')
    tb.link('Wikimedia Commons', 'https://commons.wikimedia.org')
    tb.text(' and ')
    tb.link('Geograph', 'https://www.geograph.org.uk')
    tb.text(' contributors, credited by name on every post\n')
    tb.text('🏛️ Libraries: ')
    tb.link('DCMS', 'https://www.data.gov.uk/dataset/'
                    'public-libraries-in-england-basic-dataset')
    tb.text(' (OGL) and ')
    tb.link('Libraries Hacked', 'https://www.librarieshacked.org')
    tb.text('\n🗺️ Buildings: ')
    tb.link('OpenStreetMap', 'https://www.openstreetmap.org/copyright')
    tb.text(' contributors (ODbL)')
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

    client = login_client()

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


ALT_PATH = DATA / 'alt_text.json'
_alt_cache = None

ALT_MAX = 1900

# Provenance, disclosed in the alt text itself rather than only in the bio and
# the pinned post. The README's reason for naming the photographer on every
# post applies here unchanged: it is the only credit that survives a reshare,
# and someone meeting a single reposted photograph otherwise has no signal that
# no human ever looked at it.
#
# Each source labels itself, at the head of its own section. A single note at
# the end reading "A.I.-written description; note from Wikimedia Commons" says
# both sources are present but never where one stops and the other starts, so a
# listener hears a machine's guess and a human's research as one undivided
# paragraph. That is the exact confusion these two sources are kept separate to
# prevent. Labelling in place is the only version that actually attributes.
#
# The fallback description below gets no label at all — it is assembled from
# the roster, and no model ever saw the photograph.
AI_PREFIX = 'A.I.-written description:'
COMMONS_PREFIX = 'Note from Wikimedia Commons:'


def alt_text_store():
    global _alt_cache
    if _alt_cache is None:
        _alt_cache = json.loads(ALT_PATH.read_text()) if ALT_PATH.exists() else {}
    return _alt_cache


def build_alt(row):
    """Describe the photograph, not the post.

    The name, address, photographer and licence are all visible immediately
    above the image, so repeating them here spends a screen-reader user's time
    on what they already have and never tells them what the building looks
    like. Written by everylibrary_describe.py.

    Falls back to bare identification when no description has been generated
    yet: worse than a description, but better than nothing at all.
    """
    entry = alt_text_store().get(library_id(row), {})
    visual, context = entry.get('visual'), entry.get('context')

    if visual:
        # Labels lead their sections, so each survives truncation with the text
        # it introduces rather than being stranded at the end of it.
        # Curled here rather than in the store: the descriptions already written
        # carry straight quotes, and a listener gets the same house style as a
        # reader without a migration.
        parts = [f'{AI_PREFIX} {typographic(visual)}']
        if context:
            parts.append(f'{COMMONS_PREFIX} {typographic(context)}')
        return ' '.join(parts)[:ALT_MAX]

    place = short_place(row['authority'])
    address = clean_address(row.get('address'), row.get('postcode'))
    name = display_name(row['name'])
    return f"Photograph of {name}, {address or place}."[:1900]


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
            client = login_client()

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
