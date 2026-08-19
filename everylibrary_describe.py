#!/usr/bin/env python3
"""
everylibrary_describe.py — write alt text for the library photographs.

The bot's original alt text repeated the post: name, address, photographer,
licence. All of that is already visible above the image, so a screen-reader
user heard the caption twice and learned nothing about the photograph. This
builds real descriptions instead.

Two sources, kept deliberately separate so provenance stays honest:

  visual   What is actually in the frame, generated from the image by
           `claude -p`. Building material, storeys, windows, signage, setting.
  context  The human-written note on the Commons file page, used only when it
           adds something beyond the file title. It knows things no model can
           see from a photograph: that the building was a Mechanics Institute,
           that a Carnegie endowment paid for it, that the brick building on
           the left is the police station.

The model is never shown the context, so it cannot launder a human's claim into
something it appears to have observed.

Output is data/alt_text.json, keyed by the same library id the poster uses, so
it survives a corpus rebuild. Resumable: interrupt it freely.

Requires the Claude OAuth token used by the other bots here:
    security add-generic-password -a "seoulbot" -s "claude-oauth-token" -w

Usage:
    python3 everylibrary_describe.py             # describe everything outstanding
    python3 everylibrary_describe.py --limit 20  # a small batch, to sample quality
    python3 everylibrary_describe.py --workers 6
    python3 everylibrary_describe.py --context-only   # fetch missing Commons notes, no model calls
    python3 everylibrary_describe.py --context-only --refetch-suspect
"""

import argparse
import csv
import difflib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from everylibrary_post import (MANIFEST, DATA, USER_AGENT, library_id,
                               commons_filepath_url, display_name,
                               typographic)

ALT_PATH = DATA / 'alt_text.json'

CLAUDE_TOKEN_ACCOUNT = 'seoulbot'
CLAUDE_TOKEN_SERVICE = 'claude-oauth-token'
MODEL = 'claude-haiku-4-5-20251001'

MAX_VISUAL_CHARS = 240
FETCH_WIDTH = 900          # enough detail to describe, small enough to move fast
CALL_TIMEOUT = 240

# Claude Code's Read tool refuses paths outside its working directory, so the
# images it is asked to look at have to live inside the project. A tempfile in
# /var/folders comes back not as an error but as exit code 0 and a courteous
# "I don't have a tool available to read image files", which is exactly the
# shape of a sentence that could be stored as a description by mistake.
TMP_DIR = DATA / '_tmp'

# Two ways the model returns a sentence that is not a description, both seen in
# the wild, both worse for a screen-reader user than no alt text at all:
#
#   refusal     "I don't have a tool available to read image files."
#   commentary  "This image doesn't show a library building. Could you verify
#                the file path? The image at /Users/.../tmpqv20hp53.jpg appears
#                to be a historical marker rather than a library building."
#
# The first was guarded from the start. The second is what happens when the
# photograph is not what the prompt promised — an interior, a plaque, a parade
# of shops — and the model corrects the brief instead of describing what it can
# actually see. It is fluent, confident prose, so only a filter catches it, and
# the second example would have published a path on this Mac to Bluesky.
#
# These are not a list of the sentences seen. They are four things a description
# of a photograph never does: ask a question, speak in the first or second
# person, mention the image as an object rather than its contents, or carry
# markdown. 'Grade I listed' is the one legitimate standalone I in the corpus.
NOT_A_DESCRIPTION = (
    re.compile(r'\?'),
    re.compile(r"\b(i|i'?m|i'?ve|my|we|us|you|your)\b", re.I),
    re.compile(r'\b(image|photograph|photo)\b'
               r'|\bpicture\b(?!\s+(books?|windows?|frames?|rails?))', re.I),
    re.compile(r'\*\*|/Users/|\.jpg\b'),
    re.compile(r'\b(no tool|not able to (read|view|see)|as an ai)\b', re.I),
)


def not_a_description(text):
    """True when the text is the model talking rather than describing.

    Two exemptions, both found by running the rules over the descriptions
    already held. A library is full of picture books and picture windows, and
    signage gets quoted: "Libraries gave us power" on a wall is a description of
    what is written there, not the model addressing anybody. Quoted spans are
    therefore exempt from the pronoun rule, and 'Grade I listed' from all of it.
    """
    probe = re.sub(r'\bGrade I{1,3}\b', '', text)
    probe = re.sub(r'["“][^"“”]{0,120}["”]', '', probe)
    return any(rx.search(probe) for rx in NOT_A_DESCRIPTION)


# The brief used to assert 'It is a photograph of a UK public library building',
# which is what invited the correction: told the subject and shown something
# else, the model argued with the prompt. It is now told the subject varies, and
# told explicitly that saying what the image is *not* helps nobody listening.
#
# The two rules about precision come from auditing 20 descriptions against their
# photographs on 17 August 2026. None was invented, but five misled, and all
# five in the same direction: the model over-specifies. It counted eight windows
# where there are six, called buff brick red, called a timber fence metal rails,
# put a library's sign on the building next door, and described a cascade of
# knitted remembrance poppies down a memorial cross as burgundy ivy. Every one
# is a confident specific the frame does not support, and a listener has no way
# to hear that it is wrong.
PROMPT = (
    'Read the image at {path} and describe it as alt text for a blind reader. '
    'It is usually the exterior of a UK public library, but it may be an '
    'interior, a plaque, a memorial, a shopfront or something else entirely.\n\n'
    'Rules:\n'
    '- Describe only what is visible: building material, number of storeys, '
    'windows, doors, signage, surroundings, weather. Indoors, describe the '
    'room: shelving, furniture, lighting, what the space is for.\n'
    '- Describe whatever is in the frame, whatever it turns out to be. Never '
    'say what it is not, never remark on whether it matches this brief, never '
    'address me, and never mention the image, the file or these rules. Someone '
    'who cannot see it is listening to this description, and none of that '
    'tells them anything about what is there.\n'
    '- Prefer a safe observation to a precise one. Do not give a number unless '
    'you have counted it: "a row of tall windows" beats "eight sash windows" '
    'when you have not counted eight. Name a material only where you can see it '
    'plainly, and if you cannot tell brick from stone, or red brick from brown, '
    'say less rather than choosing. Attribute signage only to the building it is '
    'plainly fixed to.\n'
    '- If you cannot tell what something is, describe how it looks instead of '
    'naming it: "a cascade of red fabric flowers" rather than "ivy". A wrong '
    'name is worse than a plain description, because the listener cannot see '
    'that it is wrong.\n'
    '- Do not name the library, the town or the photographer.\n'
    '- Do not guess the age, architect or history. If a date is carved on the '
    'building and legible, you may state it.\n'
    '- Do not begin with "A photograph of" or "An image of".\n'
    '- One or two sentences, maximum {maxlen} characters.\n'
    '- British English.\n'
    '- Return the description only, with no preamble or quotation marks.'
)

_print_lock = threading.Lock()


def log(msg):
    with _print_lock:
        print(msg, flush=True)


def claude_env():
    """Inject the long-lived OAuth token so this does not depend on an
    interactive login's short-lived token, matching the other bots here."""
    env = os.environ.copy()
    result = subprocess.run(
        ['security', 'find-generic-password',
         '-a', CLAUDE_TOKEN_ACCOUNT, '-s', CLAUDE_TOKEN_SERVICE, '-w'],
        capture_output=True, text=True
    )
    if result.returncode == 0 and result.stdout.strip():
        env['CLAUDE_CODE_OAUTH_TOKEN'] = result.stdout.strip()
    return env


def load_alt():
    if ALT_PATH.exists():
        return json.loads(ALT_PATH.read_text())
    return {}


def save_alt(alt):
    tmp = ALT_PATH.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(alt, indent=1, ensure_ascii=False))
    os.replace(tmp, ALT_PATH)


def load_rows():
    with MANIFEST.open() as f:
        return [r for r in csv.DictReader(f) if r.get('postable') == 'yes']


# --------------------------------------------------------------- the context


# A tag stripper deletes the tags and keeps what is between them, which is the
# right behaviour everywhere except here: the contents of <style> ARE the
# output. Commons renders TemplateStyles into the description HTML, so 17 of
# the 703 stored notes shipped a stylesheet to screen readers —
#
#   "Purley Library .mw-parser-output .messagebox{margin:4px 0;width:auto;
#    border-collapse:collapse;border:2px solid var(--border-color-progressive..."
#
# read out as "dot m w hyphen parser hyphen output dot messagebox open brace".
# These blocks are removed whole, before the tags around them go.
_BLOCK = re.compile(r'<(style|script)\b[^>]*>.*?</\1\s*>', re.I | re.S)


def strip_markup(s):
    """Commons descriptions are wikitext rendered to HTML, so they arrive with
    both tags and entities. Stripping tags alone leaves "Warrington Library
    &amp; Museum" for a screen reader to read out as "ampersand a-m-p".

    Stripping once and unescaping once is not enough, which is what shipped and
    what left entities in 37 of the stored notes. Some Commons descriptions are
    escaped twice, so one unescape turns "&amp;amp;" into "&amp;" rather than
    "&"; and where the source escapes its own markup, unescaping after the
    stripper has run re-creates "<br>" as literal text it will never see. So
    strip and unescape alternately until the string stops changing.

    The style-block removal sits inside that loop for the same reason: an
    escaped <style> only becomes recognisable as one after an unescape pass.
    """
    text = s or ''
    for _ in range(4):
        nxt = html.unescape(re.sub(r'<[^>]+>', ' ', _BLOCK.sub(' ', text)))
        if nxt == text:
            break
        text = nxt
    return re.sub(r'\s+', ' ', text).strip()


# Commons' messagebox templates leave a sentence of their own behind once the
# stylesheet is gone: "Wikidata has entry Purley Library (Q27086972) with data
# related to this item." That is the template talking, not a human describing a
# photograph, and a screen reader spells the Q-number out digit by digit.
#
# It is REMOVED rather than used to discard the note, because it is not always
# the whole note: one file pairs it with a genuine description of a Grade II
# listed building. Strip the sentence, keep whatever a person actually wrote,
# and let the usefulness test below decide whether what remains is worth
# serving.
_BOILER = re.compile(
    r'\s*Wikidata has entry\b.*?(?:\(Q\d+\))?\s*with data related to this item\.?',
    re.I | re.S)


def strip_boilerplate(text):
    return re.sub(r'\s+', ' ', _BOILER.sub(' ', text or '')).strip()


def title_of(image_title):
    """The file title with Commons' and Geograph's bookkeeping removed, so it
    can be compared against the description."""
    t = re.sub(r'^File:', '', image_title or '').rsplit('.', 1)[0]
    t = re.sub(r'\s*-\s*geograph\.org\.uk\s*-\s*\d+', '', t)
    t = re.sub(r'\s*\(geograph \d+\)', '', t)
    return t.replace('_', ' ').strip()


# How much of a Commons note is kept. Long notes wander off the photograph and
# into the institution's history, which is interesting and not what alt text is
# for; 400 characters holds the part that describes the thing in the frame.
CONTEXT_MAX = 400


def clip_note(text, limit=CONTEXT_MAX):
    """Cut a Commons note to `limit` characters without ending mid-word.

    The cut used to be a bare `desc[:400]`, which landed wherever it landed: 34
    of the 703 stored notes ended on a severed word, so a screen reader
    announced a stray syllable and stopped — "a Roman actor's mask (the only
    one of its kind in Britain), bot". Sighted readers never saw it, because
    the alt text is the one part of a post only a screen reader reaches.

    A sentence end is preferred, so the note reads as though it simply
    finished. That is only taken if it leaves at least half the budget: some of
    these notes open with an abbreviation or a date, and honouring a full stop
    20 characters in would throw away the description to respect a boundary.

    Otherwise it backs off to the last space and marks the cut with an
    ellipsis, which is honest about there being more and is not announced as a
    word. Trailing punctuation left dangling by the backup is dropped, so the
    result is never "…in Britain), …".
    """
    text = (text or '').strip()
    if len(text) <= limit:
        return text

    head = text[:limit]
    sentence = re.search(r'^.*[.!?](?=\s|$)', head, re.S)
    if sentence and len(sentence.group().strip()) >= limit // 2:
        return sentence.group().strip()

    cut = head.rsplit(' ', 1)[0].rstrip(' ,;:-–—')
    # Two ways the backup fails, both ending in a hard cut instead.
    #
    # It can give back almost nothing, when the only space in the head is an
    # early one: "Est." in front of a long unbroken run collapsed the whole
    # note to "Est.…". That trades the entire description for a clean boundary,
    # which is the same trade the sentence branch above refuses, so it is
    # refused here on the same threshold.
    #
    # It can also give back the whole head, when there is no space at all, and
    # appending the ellipsis to that would exceed the limit.
    if len(cut) < limit // 2 or len(cut) >= limit:
        cut = head[:limit - 1].rstrip(' ,;:-–—')
    return cut + '…'


# Stored notes that predate the fixes above, identified from the stored string
# alone. Being at exactly the cap means the old bare desc[:400] did the cutting;
# a CSS declaration or a mw-parser-output class means a stylesheet came through
# the stripper. Neither can be repaired in place, so both are refetch triggers.
_JUNK = re.compile(r'mw-parser-output|\.mbox|font-size\s*:|line-height\s*:'
                   r'|border-collapse\s*:|box-sizing\s*:|border-spacing\s*:'
                   r'|\{\|')          # a raw wikitext table, never rendered


def is_suspect(note):
    # At the cap AND ending mid-word is the old bare desc[:400]'s signature.
    # The cap alone is not: clip_note can land on exactly the cap legitimately,
    # and flagging that would make --refetch-suspect re-fetch the same handful
    # of files on every run without ever settling.
    severed = len(note) == CONTEXT_MAX and note[-1:].isalnum()
    return severed or bool(_JUNK.search(note)) or bool(_BOILER.search(note))


def fetch_context(rows, alt, session, refetch_suspect=False):
    """Pull ImageDescription from Commons, keeping only the ~third that says
    more than the file title already does.

    Normally this only fills gaps: an entry that already has a `context` key is
    left alone, so a rerun costs nothing and a Commons edit does not silently
    rewrite alt text nobody asked to change.

    `refetch_suspect` reopens the notes the current rules would have stored
    differently: those sitting at exactly `CONTEXT_MAX`, which is the signature
    of the old hard character cut, and those carrying stylesheet text that used
    to survive `strip_markup`. Both need the original description back from
    Commons to redo, which is why this is a refetch and not a local pass — the
    stored string has already lost what the fix needs.
    """
    def outstanding(row):
        entry = alt.get(library_id(row), {})
        if 'context' not in entry:
            return True
        return refetch_suspect and is_suspect(entry.get('context') or '')

    todo = [r for r in rows
            if r['image_source'] != 'geograph' and outstanding(r)]
    log(f'context  {len(todo)} files to check on Commons'
        + ('  (including notes stored under the old rules)' if refetch_suspect else ''))

    for i in range(0, len(todo), 50):
        chunk = todo[i:i + 50]
        # A title maps to a LIST of rows, not one row. Five photographs in the
        # manifest serve two libraries each — a branch and the town it is in —
        # and keying by title alone silently kept whichever came last in the
        # chunk. Gloucester sat on a stale note through two refetches for this
        # reason while Longlevens, sharing the same photograph, updated fine:
        # no error, no log line, just a row the loop never reached.
        by_title = {}
        for r in chunk:
            by_title.setdefault(r['image_title'], []).append(r)
        try:
            d = session.get('https://commons.wikimedia.org/w/api.php', timeout=60, params={
                'action': 'query', 'format': 'json', 'prop': 'imageinfo',
                'iiprop': 'extmetadata', 'titles': '|'.join(by_title),
            }).json()
        except (requests.RequestException, ValueError):
            continue

        norm = {n['to']: n['from'] for n in d.get('query', {}).get('normalized', [])}
        for page in d.get('query', {}).get('pages', {}).values():
            requested = norm.get(page.get('title'), page.get('title'))
            matched = by_title.get(requested)
            if not matched:
                continue
            em = (page.get('imageinfo') or [{}])[0].get('extmetadata', {})
            desc = strip_boilerplate(strip_markup(em.get('ImageDescription', {}).get('value')))
            title = title_of(requested)

            useful = ''
            if desc and len(desc) - len(title) > 25:
                if difflib.SequenceMatcher(None, desc.lower(), title.lower()).ratio() < 0.85:
                    useful = clip_note(desc)
            # Last gate, and deliberately a discard rather than another
            # stripper. Commons descriptions arrive in whatever state an editor
            # left them: rendered HTML, escaped HTML, raw wikitext tables. Each
            # form needs its own cleaner and there will be another one, so the
            # backstop is a shape test on the OUTPUT — if what survived still
            # looks like markup, no note is served at all. The alt text loses a
            # sentence of context; the alternative is a screen reader reading
            # out a stylesheet, which is what shipped for 17 libraries.
            if _JUNK.search(useful):
                log(f'         markup survived, note dropped: '
                    f'{matched[0].get("name", "?")}')
                useful = ''
            for row in matched:
                # Curled on the way in, so the store matches house style at
                # rest. The poster curls again on the way out, which is a
                # no-op on already-curled text and covers anything written
                # before 20 August 2026 by another route.
                alt.setdefault(library_id(row), {})['context'] = typographic(useful)

        save_alt(alt)
        log(f'         {min(i + 50, len(todo)):>5}/{len(todo)}')
        time.sleep(0.15)


# ---------------------------------------------------------------- the visual


def fetch_image(row, session):
    urls = ([row['image_url']] if row['image_source'] == 'geograph'
            else [commons_filepath_url(row['image_title'], FETCH_WIDTH)])
    for url in urls:
        try:
            r = session.get(url, timeout=60)
            if r.status_code == 200 and len(r.content) > 1000:
                return r.content
        except requests.RequestException:
            pass
    return None


def _one_description(path, env):
    """A single read of one image file. None on any failure."""
    try:
        p = subprocess.run(
            ['claude', '-p', '--model', MODEL,
             PROMPT.format(path=path, maxlen=MAX_VISUAL_CHARS)],
            capture_output=True, text=True, env=env, timeout=CALL_TIMEOUT)
        if p.returncode != 0:
            return None
        out = (p.stdout or '').strip().strip('"').strip()
        # Guard against the model ignoring the instruction to skip preamble.
        out = re.sub(r'^(a |an )?(photograph|image|picture) of\s+', '', out, flags=re.I)
        out = re.sub(r'\s+', ' ', out).strip()
        if len(out) < 25 or len(out) > MAX_VISUAL_CHARS * 2:
            return None
        if not_a_description(out):
            return None
        return out
    except (subprocess.TimeoutExpired, OSError):
        return None


# A description can be fluent, guard-clean and simply invented. On 17 August
# 2026 one run returned "A stone-built library, three to four storeys, with
# regularly spaced windows" for a photograph of a low modern library with a
# cascade of remembrance poppies down a memorial cross. The fetch was a valid
# JPEG, the same 169,809 bytes on three consecutive pulls, and three other runs
# on that same file described the poppies correctly. Nothing was wrong with the
# image: the model did not look at it. One fabrication in eleven calls.
#
# Nothing in the text of a fabrication marks it out — that is what makes it
# worse than the refusals and the commentary, both of which announce themselves.
# What does mark it out is a second opinion. A description of a photograph that
# was actually looked at agrees with another one on the plain facts; an invented
# one agrees with nothing, because there was nothing generating it.
#
# So two independent reads per image, kept only if they agree. The comparison is
# deliberately coarse. Two honest descriptions of the same building differ all
# the time on wording, on which details they choose, on whether the brick is
# 'red' or 'warm red', and a strict test would throw away good work. It asks
# only whether they saw the same kind of thing.
NUMBER_WORDS = {'single': 1, 'one': 1, 'two': 2, 'three': 3, 'four': 4,
                'five': 5, 'six': 6, 'seven': 7, 'eight': 8}
OUTDOOR = re.compile(r'\b(sky|skies|forecourt|pavement|street|roofline|roof|'
                     r'fa[çc]ade|car park|hedge|kerb|road|chimney|chimneys|'
                     r'gable|gables|courtyard|garden|railings|pathway)\b', re.I)
INDOOR = re.compile(r'\b(shelving|bookshel\w+|carpet\w*|ceiling|reading room|'
                    r'interior|indoor|furniture|armchair\w*|study space|'
                    r'issue desk|counter)\b', re.I)


def _storeys(text):
    """The storey counts a description commits to, as a set.

    'three to four storeys' yields {4}: one number is enough to disagree with
    'single-storey', and the range itself is not the interesting part.
    """
    words = re.findall(r'([a-z]+)[\s-]stor(?:ey|ie)s?\b', text, re.I)
    return {NUMBER_WORDS[w.lower()] for w in words if w.lower() in NUMBER_WORDS}


def _setting(text):
    """'outdoor', 'indoor', or None where the description does not say."""
    out, ins = len(OUTDOOR.findall(text)), len(INDOOR.findall(text))
    if out == ins:
        return None
    return 'outdoor' if out > ins else 'indoor'


def disagreement(first, second):
    """Why two descriptions cannot be of the same photograph, or None.

    Only two tests, both chosen because they are things a description states
    outright rather than implies. Material was tried as a third and dropped: a
    brick building with stone dressings is honestly 'brick' to one read and
    'stone' to another, and Overton's knapped flint came back as brick, cream
    brick and stone across three runs of a photograph nobody was inventing.
    That rule rejected real work; these two did not.
    """
    a, b = _storeys(first), _storeys(second)
    if a and b and not (a & b):
        return f'storeys {sorted(a)} vs {sorted(b)}'
    sa, sb = _setting(first), _setting(second)
    if sa and sb and sa != sb:
        return f'{sa} vs {sb}'
    return None


def describe(row, session, env):
    """One image, two independent reads, kept only if they agree.

    Returns the shorter of the two: both passed the same guard, and the shorter
    is the one making fewer claims. Returns None on any failure or on
    disagreement — a missing description is fine, a wrong one is not.
    """
    data = fetch_image(row, session)
    if not data:
        return None

    TMP_DIR.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False, dir=TMP_DIR) as fh:
        fh.write(data)
        path = fh.name
    try:
        first = _one_description(path, env)
        if not first:
            return None
        second = _one_description(path, env)
        if not second:
            return None
        conflict = disagreement(first, second)
        if conflict:
            log(f'         two reads disagreed ({conflict}), dropped: '
                f'{row.get("name", "?")}')
            return None
        return min(first, second, key=len)
    except OSError:
        return None
    finally:
        os.unlink(path)


# --------------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser(description='Write alt text for the library photographs.')
    ap.add_argument('--limit', type=int, help='stop after this many descriptions')
    ap.add_argument('--workers', type=int, default=4,
                    help='concurrent claude calls (default 4)')
    ap.add_argument('--context-only', action='store_true',
                    help='fetch missing Commons notes only, no model calls')
    ap.add_argument('--refetch-suspect', action='store_true',
                    help='also redo notes stored under the old cut/strip rules')
    args = ap.parse_args()

    rows = load_rows()
    alt = load_alt()
    session = requests.Session()
    session.headers.update({'User-Agent': USER_AGENT})

    fetch_context(rows, alt, session, refetch_suspect=args.refetch_suspect)
    if args.context_only:
        ctx = sum(1 for v in alt.values() if v.get('context'))
        log(f'\ncontext notes held: {ctx}')
        return

    todo = [r for r in rows if not alt.get(library_id(r), {}).get('visual')]
    if args.limit:
        todo = todo[:args.limit]
    log(f'visual   {len(todo)} to describe, {args.workers} at a time')
    if not todo:
        return

    env = claude_env()
    done = failed = 0
    started = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(describe, r, session, env): r for r in todo}
        for fut in as_completed(futures):
            row = futures[fut]
            try:
                text = fut.result()
            except Exception:
                text = None
            if text:
                alt.setdefault(library_id(row), {})['visual'] = typographic(text)
                done += 1
            else:
                failed += 1

            n = done + failed
            if n % 20 == 0 or n == len(todo):
                save_alt(alt)
                rate = n / max(time.time() - started, 1)
                left = (len(todo) - n) / rate / 60 if rate else 0
                log(f'         {n:>5}/{len(todo)}  described {done}  failed {failed}  '
                    f'~{left:.0f} min left')

    save_alt(alt)
    log(f'\nwritten to {ALT_PATH}')
    log(f'  descriptions {done}, failures {failed}')


if __name__ == '__main__':
    main()
