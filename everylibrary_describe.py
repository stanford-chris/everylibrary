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
from collections import Counter
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
from everylibrary_post import (MANIFEST, DATA, USER_AGENT, STATE_FILE,
                               library_id, commons_filepath_url, display_name,
                               typographic)

ALT_PATH = DATA / 'alt_text.json'

CLAUDE_TOKEN_ACCOUNT = 'seoulbot'
CLAUDE_TOKEN_SERVICE = 'claude-oauth-token'
# Sonnet, not Haiku, since 24 August 2026, and the reason is measured rather
# than assumed. An audit of all 25 posts the bot had made found 7 carrying a
# confident specific the photograph does not support, and tightening the prompt
# did NOT fix them: under rules that forbid each error by name, Haiku still
# returned "Red brick single-storey building ... ivy grows up the right wall"
# for a buff-brick frontage with a gable above a glazed ground floor and a
# conifer at the edge of the frame.
#
# Run against the same ten photographs and the same prompt, Sonnet got seven
# right outright, including every one of Haiku's errors: interlocking circles
# where Haiku saw a diamond lattice, hatched markings where it saw a disabled
# bay, a conifer where it saw ivy. It is also about five times FASTER here
# (8-20s a call against 50-145s), because the cost of these calls is dominated
# by the session `claude -p` spins up rather than by the tokens.
#
# Two of the ten are still imperfect (Cosby's roof form, one of Oxgangs' two
# reads), so this is a large improvement and not a fix. Do not read a Sonnet
# description as verified.
MODEL = 'claude-sonnet-5'

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
# Four more rules came from auditing every one of the 25 posts the bot had made,
# each against its own posted image, on 24 August 2026. Seven carried a
# confident specific the photograph does not support, and they cluster:
#
#   storeys    a tall single-storey hall read as two, a two-storey frontage
#              read as one. This list used to ASK for "number of storeys",
#              which is why 73% of the corpus commits to a count.
#   a name it  sash for mullioned casements, protective bars for glazing bars,
#   cannot     diamond-lattice for interlocking circles, curved for pyramidal,
#   check      a disabled bay for keep-clear hatching.
#   the wrong  a noticeboard on the pavement read as posters on the wall, a
#   object     conifer at the frame edge read as ivy climbing the wall behind.
#   the period Stockport's 1913 Edwardian Baroque called Victorian. The old
#              wording said not to guess the age and 102 descriptions do it
#              anyway, so the period words are now named and banned outright.
#
# Bumped whenever the wording changes in a way that should reopen work already
# done. --redescribe reads it: an entry stamped with an older version is
# outstanding, one stamped with this version is finished, so a re-run is
# resumable and a half-finished pass cannot lose the descriptions it has not
# reached yet.
PROMPT_VERSION = 2

PROMPT = (
    'Read the image at {path} and describe it as alt text for a blind reader. '
    'It is usually the exterior of {subject}, but it may be an '
    'interior, a plaque, a memorial, a shopfront or something else entirely.\n\n'
    'Rules:\n'
    '- Describe only what is visible: building material, windows, doors, '
    'signage, surroundings, weather. Indoors, describe the room: shelving, '
    'furniture, lighting, what the space is for.\n'
    '- Describe whatever is in the frame, whatever it turns out to be. Never '
    'say what it is not, never remark on whether it matches this brief, never '
    'address me, and never mention the image, the file or these rules. Someone '
    'who cannot see it is listening to this description, and none of that '
    'tells them anything about what is there.\n'
    '- Do not say how many storeys a building has unless you can see a separate '
    'row of windows for each one. A tall wall, a gable, or a window set into '
    'the roof is not a storey you have counted. Describe how the front is '
    'arranged instead: "a glazed ground floor with a gabled window above" tells '
    'a listener more than a number you have guessed at.\n'
    '- Prefer a safe observation to a precise one. Do not give a number unless '
    'you have counted it: "a row of tall windows" beats "eight sash windows" '
    'when you have not counted eight. Name a material only where you can see it '
    'plainly, and if you cannot tell brick from stone, or red brick from brown, '
    'say less rather than choosing.\n'
    '- Attach every detail to the thing it actually belongs to. Signage is only '
    'the building\'s if it is plainly fixed to it, never a freestanding '
    'noticeboard or sign standing in front. A plant or a tree at the edge of '
    'the frame is not growing on the wall behind it.\n'
    '- If you cannot tell what something is, describe how it looks instead of '
    'naming it: "a cascade of red fabric flowers" rather than "ivy". A wrong '
    'name is worse than a plain description, because the listener cannot see '
    'that it is wrong. This matters most for the things that have precise names '
    'you may not be able to tell apart from a photograph: window types (sash, '
    'casement), what bars across a window are for, roof shapes (pitched, '
    'hipped, curved, flat), the pattern on a facade, and markings painted on a '
    'road or a car park. Say what the shape looks like.\n'
    '- Do not name the library, the town or the photographer.\n'
    '- Do not guess the age, the architect or the history, and never use a '
    'period word: not Victorian, Edwardian, Georgian, Art Deco or mid-century. '
    'A building cannot be dated by looking at it. If a date is carved on the '
    'building and legible, you may state that.\n'
    '- Do not begin with "A photograph of" or "An image of".\n'
    '- One or two sentences, maximum {maxlen} characters.\n'
    # Deliberate exception to the house style's American-spelling default
    # (CLAUDE.md, 28 August 2026): everylibrary's whole subject is UK public
    # libraries, so British spelling is the content-appropriate choice, which
    # is why `spelling` defaults to British below. everycarnegie reuses this
    # describer for a worldwide roster (79% American, 13% UK/Ireland as of
    # 3 September 2026) and passes `subject`/`spelling` per row's own country
    # instead of taking the default — see carnegie_describe.py.
    '- {spelling} English.\n'
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


def posted_ids():
    """The libraries already on the feed.

    --redescribe leaves them alone. Each library posts exactly once, so
    rewriting a description that has already gone out changes nothing a reader
    will ever hear, and the stored text is the nearest thing there is to a
    record of what a screen reader was served.

    A missing or unreadable state file ABORTS rather than returning an empty
    set. Read the lazy way round, "no state" and "nothing posted yet" produce
    the same answer, and the wrong one silently rewrites the record.
    """
    if not STATE_FILE.exists():
        sys.exit(f'--redescribe needs {STATE_FILE}, to know what has posted.')
    state = json.loads(STATE_FILE.read_text())
    return set(state.get('posted', []))


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

# Commons' {{Information}} template renders a file-information table beneath
# the human's description. Its tags strip out fine and its CONTENTS are the
# problem: "Camera location51° 26′ 36.24″ N, 0° 13′ 06.24″ E View this and
# other nearby images on: OpenStreetMap 51.443400; 0.218400" reached two
# stored notes, and a screen reader says a coordinate digit by digit. Removed
# whole, exactly as a stylesheet is, and for the same reason.
#
# NOT added to _JUNK, which discards the entire note: Dartford's human half
# ("Dartford is a town in North Kent, England. The Library and Museum") is
# genuine and worth keeping, and the geo table is appended to it rather than
# replacing it.
_INFOTABLE = re.compile(
    r'<table\b[^>]*\bcommons-file-information-table\b.*?</table\s*>', re.I | re.S)


# A URL is unreadable as alt text: nobody can follow a link they are hearing,
# and a screen reader says "h t t p colon slash slash w w w dot" and then
# spells the path. 36 of 686 stored notes carried one, mostly a trailing
# "Find out more about this library at: <url>" from a council upload.
#
# Removed in place rather than by cutting the note at the URL, because a URL is
# not always at the end: Aylesbury's sits mid-sentence, between the library's
# name and "is in Walton Street", and cutting there would throw away the half
# that describes the building.
#
# The removal can leave a sentence that was only ever a lead-in to the link, so
# any sentence ending in a colon, or left with no letters at all, goes with it.
# The lead-in goes with the link. Fourteen of these notes read "Find out more
# about this library: <url>", and deleting the URL alone leaves the invitation
# pointing at nothing: "Find out more about this library: Photo credit: Leeds
# libraries". A colon-terminated run with no sentence end in it is that
# lead-in, and only when it sits immediately before the link — so "Photo
# credit: Cambridgeshire libraries" a few words earlier is untouched.
_URL = re.compile(r'(?:[^.!?]{0,60}:\s*)?<?\b(?:https?://|www\.)\S+', re.I)

# One note was cut by clip_note in the middle of an anchor tag, leaving
# '<a href="' with no closing bracket for strip_markup ever to match.
_OPEN_TAG_TAIL = re.compile(r'[,;]?\s*(?:and\s+)?<[a-z]+\b[^>]*$', re.I)


def strip_urls(text):
    out = re.sub(r'\s+', ' ', _URL.sub(' ', text or '')).strip()
    out = _OPEN_TAG_TAIL.sub('', out).strip()
    kept = [q for q in re.split(r'(?<=[.!?])\s+', out)
            if q.strip() and not q.rstrip().endswith(':') and re.search(r'[A-Za-z]', q)]
    return ' '.join(kept).strip()


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
        stripped = _INFOTABLE.sub(' ', _BLOCK.sub(' ', text))
        nxt = html.unescape(re.sub(r'<[^>]+>', ' ', stripped))
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
    return (severed or bool(_JUNK.search(note)) or bool(_BOILER.search(note))
            or bool(_URL.search(note)))


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
            desc = strip_urls(strip_boilerplate(
                strip_markup(em.get('ImageDescription', {}).get('value'))))
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


RETRY_NOTE = """

An earlier attempt at this description asserted the following, and a check against the image could not find them:
{bad}

Write it again, leaving out anything you cannot actually resolve. A shorter, safer description is the right answer here."""


def _one_description(path, env, extra='', subject='a UK public library',
                      spelling='British'):
    """A single read of one image file. None on any failure.

    `extra` is appended to the prompt, and is how the verification retry names
    the claims that failed. Empty for the two ordinary reads, so the agreement
    check above still compares two genuinely independent looks.

    `subject`/`spelling` default to this module's own UK-library, British-
    English case; everycarnegie's caller overrides both per row.
    """
    try:
        p = subprocess.run(
            ['claude', '-p', '--model', MODEL,
             PROMPT.format(path=path, maxlen=MAX_VISUAL_CHARS,
                           subject=subject, spelling=spelling) + extra],
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


# The storey rule above costs this guard something, and the replacement is
# here rather than left as a gap. Storeys were one of only two things two reads
# were compared on, and a prompt that tells the model not to count them means
# most pairs will now agree by saying nothing. The sky is the other thing a
# description states outright and cannot be polite about: two honest reads of
# one photograph do not disagree about whether it is blue or grey.
#
# "partly cloudy" and its relatives commit to neither and are read as silence,
# and 'bright' is deliberately NOT a clear-sky word — a bright overcast is the
# commonest British sky there is, and counting it as clear would invent
# disagreements on exactly the photographs where both reads were right.
_SKY_NEAR = re.compile(r'((?:\w+[\s-]){0,3})sk(?:y|ies)\b', re.I)
_SKY_HEDGE = re.compile(r'\b(partly|partial|part|scattered|broken|hazy)\b', re.I)
_SKY_CLEAR = re.compile(r'\b(clear|blue|sunny|cloudless)\b', re.I)
_SKY_DULL = re.compile(r'\b(overcast|grey|gray|cloudy|dull|leaden|stormy)\b', re.I)


def _sky(text):
    """'clear', 'overcast', or None where the description does not commit."""
    words = ' '.join(_SKY_NEAR.findall(text))
    if not words.strip() or _SKY_HEDGE.search(words):
        return None
    clear, dull = bool(_SKY_CLEAR.search(words)), bool(_SKY_DULL.search(words))
    if clear == dull:
        return None
    return 'clear' if clear else 'overcast'


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
    ka, kb = _sky(first), _sky(second)
    if ka and kb and ka != kb:
        return f'{ka} sky vs {kb} sky'
    return None


# The agreement check above catches a description of a photograph the model
# never looked at. It does NOT catch one where the model looked and misread,
# because two reads of the same image misread it the same way and agree
# perfectly — and it tests only three coarse dimensions in any case.
#
# Measured 27 August 2026 over all 118 descriptions the three model-written
# bots had shipped since 16 August: 35 (30%) assert something the photograph
# does not support. This bot was the worst of the three, with 17 wrong-attribute
# errors in 29 images — a black wrought-iron fence called white, a gable called
# a mansard roof, coursed grey stone called brick, a bus shelter's canopy
# credited to the library entrance, and five wrong storey counts.
#
# ⚠️ Those five are the important ones. The prompt above already forbids
# guessing storey counts, at length and with its reasoning. It was still wrong
# five times in twenty-nine images. A prompt rule written at a failure did not
# stop the failure, which is why this is a check and not a sixth rule.
#
# So the surviving description is read back against the image, claim by claim.
# The check asks a DIFFERENT QUESTION from the one that wrote it — locate each
# asserted thing, rather than judge the sentence — because a verifier asked
# "is this good?" mostly agrees with itself.
#
# ⚠️⚠️ IT RETRIES RATHER THAN DROPPING, and that was decided by measurement
# after the first version dropped. Twelve stored descriptions were swept on
# 27 August 2026 and SEVEN were flagged — 58%. Three of the storey findings
# were then checked by eye: Wandsworth Town (three rows of windows, described
# as two storeys) and Redland (one tall storey with a gabled window above,
# described as two) were real, and Nunhead (plainly two storeys) was the
# verifier being wrong. Two in three.
#
# A gate that drops at a 58% flag rate with roughly two-thirds precision would
# take more than half of this bot's descriptions away and be wrong about a
# third of them. ⚠️ THE FALLBACK IS WHAT MAKES THAT A BAD TRADE: it is bare
# identification from the roster, "Photograph of Redland Library, Whiteladies
# Road", which tells a blind listener nothing about the photograph at all.
# Ship a wrong storey count and a listener is mildly misinformed; drop the
# description and they get nothing. The damaging class — an object that is not
# there — was 5% of images, not 58%, and a wholesale drop is far too blunt an
# instrument for it.
#
# So the failed claims are named back to the model and the description is
# written again, which usually just omits them. Only a second failure drops.
#
# ⚠️ The residual drop rate after the retry is NOT YET MEASURED: the sweep that
# would establish it ran into "API Error: Rate limit reached". Do not quote a
# figure for it until someone has run one.
#
# ⚠️ The model is never shown the Commons note, here as everywhere in this
# file. The note asserts things no photograph can show — "the council contact
# centre out the back", "Levan Strice Wood in background" — and a verifier
# holding it reports them as unsupported. The sweep that produced the figures
# above verified the assembled alt text and scored exactly those two human
# sentences as model hallucinations before they were caught by hand.

VERIFY_PROMPT = """Look at the image {path}

A description of that image appears at the end of this message. Your job is to LOCATE things in the image, not to judge the writing.

Take every concrete thing the description asserts is present — each object, structure, material, number or feature — and for each one output exactly one line:

FOUND | <the claim in a few words> | <where it is in the image>
ABSENT | <the claim in a few words> | <what is actually there instead>

Rules:
- Be strict. If you cannot point to it, it is ABSENT. Do not give it the benefit of the doubt.
- Count before you confirm a number. Storeys, windows, doors and entrances are where this goes wrong most often.
- Judge presence only. Never judge wording, style, tone or completeness.
- Output only those lines and nothing else.

Description: {alt}"""

_ABSENT_LINE = re.compile(r'^\s*ABSENT\s*\|\s*(.+?)\s*(?:\||$)')
_FOUND_LINE = re.compile(r'^\s*FOUND\s*\|')


def unsupported(path, text, env):
    """Claims in `text` that cannot be located in the image at `path`.

    Returns (claims, why): ([], None) when every claim checks out,
    ([...], None) when some cannot be found, and (None, reason) when the check
    could not be made at all.

    ⚠️ The reason is carried rather than collapsed, because the first live run
    of --reverify returned 12 of 12 NOT CHECKED and there was no way to tell a
    throttled model from an unfetchable image from a changed reply format. A
    sweep that reports only that it failed is a sweep nobody can act on.

    ⚠️ None is NOT [] and callers must not treat it as one. A failed call, a
    timeout and a model that ignored the format all yield no ABSENT lines,
    which is byte-identical to a clean verification: the dangerous state and
    the healthy one producing the same silence. Same rule as
    portfolio_brief.py refusing to publish a total it could not compute.
    """
    try:
        p = subprocess.run(
            ['claude', '-p', '--model', MODEL,
             VERIFY_PROMPT.format(path=path, alt=text)],
            capture_output=True, text=True, env=env, timeout=CALL_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, exc.__class__.__name__
    if p.returncode != 0:
        err = (p.stderr or p.stdout or '').strip()[:120] or '(no output)'
        return None, f'exit {p.returncode}: {err}'
    lines = (p.stdout or '').strip().splitlines()
    absent = [m.group(1) for m in (_ABSENT_LINE.match(ln) for ln in lines) if m]
    if not absent and not any(_FOUND_LINE.match(ln) for ln in lines):
        return None, 'reply carried no verdict lines'
    return absent, None


def describe(row, session, env, subject='a UK public library', spelling='British'):
    """One image, two independent reads, kept only if they agree.

    Returns the shorter of the two: both passed the same guard, and the shorter
    is the one making fewer claims. Returns None on any failure or on
    disagreement — a missing description is fine, a wrong one is not.

    `subject`/`spelling` default to everylibrary's own case and are unused by
    its own caller (main(), below); everycarnegie's caller passes both, keyed
    off the row's own country, since its roster is mostly not British.
    """
    data = fetch_image(row, session)
    if not data:
        return None

    TMP_DIR.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False, dir=TMP_DIR) as fh:
        fh.write(data)
        path = fh.name
    try:
        first = _one_description(path, env, subject=subject, spelling=spelling)
        if not first:
            return None
        second = _one_description(path, env, subject=subject, spelling=spelling)
        if not second:
            return None
        conflict = disagreement(first, second)
        if conflict:
            log(f'         two reads disagreed ({conflict}), dropped: '
                f'{row.get("name", "?")}')
            return None
        chosen = min(first, second, key=len)

        # ⚠️ A check that could not be MADE keeps the description, and says so.
        # This is not the same as a check that passed, and the log distinguishes
        # them: an unattended monthly pass must not silently throw away every
        # description because the verifier had a bad hour.
        bad, why = unsupported(path, chosen, env)
        if bad is None:
            log(f'         kept UNVERIFIED ({why}): {row.get("name", "?")}')
            return chosen
        if not bad:
            return chosen

        # One retry, naming what could not be found. Not a reroll of the same
        # dice: the model is told which claims failed, and the usual outcome is
        # a description that simply leaves them out.
        log(f'         failed verification ({"; ".join(bad)}), rewriting: '
            f'{row.get("name", "?")}')
        again = _one_description(
            path, env, extra=RETRY_NOTE.format(
                bad='\n'.join(f'- {b}' for b in bad)),
            subject=subject, spelling=spelling)
        if not again:
            return None
        bad2, why2 = unsupported(path, again, env)
        if bad2 is None:
            log(f'         rewrite kept UNVERIFIED ({why2}): '
                f'{row.get("name", "?")}')
            return again
        if bad2:
            log(f'         rewrite failed too ({"; ".join(bad2)}), dropped: '
                f'{row.get("name", "?")}')
            return None
        return again
    except OSError:
        return None
    finally:
        os.unlink(path)


def reverify(row, session, env):
    """Check a description already on file against its image.

    Returns (verdict, claims): 'clean', 'failed' or 'unchecked'. Generates
    nothing — one model call per image instead of the three a redescribe would
    cost, which is what makes sweeping 2,236 stored descriptions affordable at
    all.

    ⚠️ It is the VISUAL half that is checked, never the assembled alt text. The
    Commons note is a human sentence about things no photograph shows, and
    feeding it to the verifier manufactures failures out of the one part of the
    record that was never machine-written.
    """
    text = (row.get('_visual') or '').strip()
    if not text:
        return 'unchecked', ['no stored description']
    data = fetch_image(row, session)
    if not data:
        return 'unchecked', ['image could not be fetched']

    TMP_DIR.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False, dir=TMP_DIR) as fh:
        fh.write(data)
        path = fh.name
    try:
        bad, why = unsupported(path, text, env)
    finally:
        os.unlink(path)

    if bad is None:
        return 'unchecked', [why]
    return ('failed', bad) if bad else ('clean', [])


# --------------------------------------------------------------------- main


def run_reverify(rows, alt, session, args):
    """Sweep stored descriptions against their images and report.

    ⚠️ Reports and changes nothing unless --clear-failed is passed, following
    every other audit in this estate. A verifier is a fallible reader of a
    fallible reader: measured on a 118-image sweep it raised four false
    positives in forty-nine findings, so an automatic delete would throw away
    good descriptions at about that rate with nobody ever seeing what went.
    """
    todo = []
    for r in rows:
        entry = alt.get(library_id(r)) or {}
        if entry.get('visual'):
            r = dict(r, _visual=entry['visual'])
            todo.append(r)
    if args.limit:
        todo = todo[:args.limit]
    log(f'reverify {len(todo)} stored descriptions, {args.workers} at a time')
    if not todo:
        return

    env = claude_env()
    counts = {'clean': 0, 'failed': 0, 'unchecked': 0}
    failures = []
    reasons = Counter()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(reverify, r, session, env): r for r in todo}
        for n, fut in enumerate(as_completed(futures), 1):
            row = futures[fut]
            try:
                verdict, claims = fut.result()
            except Exception:                       # noqa: BLE001
                verdict, claims = 'unchecked', ['worker raised']
            counts[verdict] += 1
            if verdict == 'failed':
                failures.append((row, claims))
            elif verdict == 'unchecked':
                reasons[claims[0] if claims else 'unknown'] += 1
            if n % 25 == 0 or n == len(todo):
                log(f'         {n:>5}/{len(todo)}  clean {counts["clean"]}  '
                    f'failed {counts["failed"]}  unchecked {counts["unchecked"]}')

    log('')
    for row, claims in sorted(failures, key=lambda f: display_name(f[0]['name'])):
        log(f'  {display_name(row["name"])}')
        log(f'    {alt[library_id(row)]["visual"]}')
        for c in claims:
            log(f'    ✗ {c}')

    if reasons:
        log('\n  not checked, by reason:')
        for why, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
            log(f'    {n:>5}  {why}')

    checked = counts['clean'] + counts['failed']
    rate = f'{counts["failed"] / checked:.0%}' if checked else 'n/a'
    log(f'\nclean {counts["clean"]}  failed {counts["failed"]} ({rate})  '
        f'not checked {counts["unchecked"]}')
    # ⚠️ "not checked" is reported beside the rate and never folded into it.
    # A sweep that could not read half the images has not found a low failure
    # rate, it has found nothing, and an average over the half it managed
    # would hide that completely.

    if not args.clear_failed:
        if failures:
            log('reporting only. --clear-failed drops these so the next '
                'ordinary run rewrites them.')
        return
    for row, _ in failures:
        entry = alt[library_id(row)]
        entry.pop('visual', None)
        entry.pop('visual_v', None)
    save_alt(alt)
    log(f'cleared {len(failures)} descriptions; rerun without --reverify '
        f'to rewrite them')


def main():
    ap = argparse.ArgumentParser(description='Write alt text for the library photographs.')
    ap.add_argument('--limit', type=int, help='stop after this many descriptions')
    ap.add_argument('--workers', type=int, default=4,
                    help='concurrent claude calls (default 4)')
    ap.add_argument('--context-only', action='store_true',
                    help='fetch missing Commons notes only, no model calls')
    ap.add_argument('--refetch-suspect', action='store_true',
                    help='also redo notes stored under the old cut/strip rules')
    ap.add_argument('--redescribe', action='store_true',
                    help='rewrite descriptions written under an older prompt '
                         '(never touches a library that has already posted)')
    ap.add_argument('--reverify', action='store_true',
                    help='check descriptions already on file against their '
                         'images; reports only, changes nothing')
    ap.add_argument('--clear-failed', action='store_true',
                    help='with --reverify, drop the descriptions that failed '
                         'so the next ordinary run rewrites them')
    args = ap.parse_args()

    rows = load_rows()
    alt = load_alt()
    session = requests.Session()
    session.headers.update({'User-Agent': USER_AGENT})

    # ⚠️ Ahead of fetch_context, deliberately. Reverification reads the visual
    # half and never the Commons note, so fetching notes would be work it does
    # not use — and fetch_context WRITES alt_text.json, which a read-only audit
    # has no business doing.
    if args.reverify:
        return run_reverify(rows, alt, session, args)

    fetch_context(rows, alt, session, refetch_suspect=args.refetch_suspect)
    if args.context_only:
        ctx = sum(1 for v in alt.values() if v.get('context'))
        log(f'\ncontext notes held: {ctx}')
        return

    if args.redescribe:
        posted = posted_ids()
        todo = [r for r in rows
                if alt.get(library_id(r), {}).get('visual')
                and alt[library_id(r)].get('visual_v') != PROMPT_VERSION
                and library_id(r) not in posted]
        log(f'redescribe  prompt v{PROMPT_VERSION}, '
            f'{len(posted)} posted libraries left alone')
    else:
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
                entry = alt.setdefault(library_id(row), {})
                entry['visual'] = typographic(text)
                # Stamped on the way in, so a redescribe pass is resumable and
                # a run that dies half way through cannot lose the entries it
                # never reached: they are simply still outstanding.
                entry['visual_v'] = PROMPT_VERSION
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
