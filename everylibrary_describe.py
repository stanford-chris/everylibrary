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
    python3 everylibrary_describe.py --context-only   # refresh Commons notes, no model calls
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
                               commons_filepath_url, display_name)

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

# Anything matching this is the model talking about itself rather than the
# photograph, and must never reach a screen reader.
REFUSAL = re.compile(
    r"\b(i (don'?t|do not|cannot|can'?t|am unable)|no tool|not able to (read|view|see)|"
    r"unable to (read|view|see|access)|as an ai|i'?m sorry)\b", re.I)

PROMPT = (
    'Read the image at {path} and describe it as alt text for a blind reader. '
    'It is a photograph of a UK public library building.\n\n'
    'Rules:\n'
    '- Describe only what is visible: building material, number of storeys, '
    'windows, doors, signage, surroundings, weather.\n'
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


def strip_markup(s):
    """Commons descriptions are wikitext rendered to HTML, so they arrive with
    both tags and entities. Stripping tags alone leaves "Warrington Library
    &amp; Museum" for a screen reader to read out as "ampersand a-m-p"."""
    text = html.unescape(re.sub(r'<[^>]+>', ' ', s or ''))
    return re.sub(r'\s+', ' ', text).strip()


def title_of(image_title):
    """The file title with Commons' and Geograph's bookkeeping removed, so it
    can be compared against the description."""
    t = re.sub(r'^File:', '', image_title or '').rsplit('.', 1)[0]
    t = re.sub(r'\s*-\s*geograph\.org\.uk\s*-\s*\d+', '', t)
    t = re.sub(r'\s*\(geograph \d+\)', '', t)
    return t.replace('_', ' ').strip()


def fetch_context(rows, alt, session):
    """Pull ImageDescription from Commons, keeping only the ~third that says
    more than the file title already does."""
    todo = [r for r in rows
            if r['image_source'] != 'geograph'
            and 'context' not in alt.get(library_id(r), {})]
    log(f'context  {len(todo)} files to check on Commons')

    for i in range(0, len(todo), 50):
        chunk = todo[i:i + 50]
        by_title = {r['image_title']: r for r in chunk}
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
            row = by_title.get(requested)
            if not row:
                continue
            em = (page.get('imageinfo') or [{}])[0].get('extmetadata', {})
            desc = strip_markup(em.get('ImageDescription', {}).get('value'))
            title = title_of(requested)

            useful = ''
            if desc and len(desc) - len(title) > 25:
                if difflib.SequenceMatcher(None, desc.lower(), title.lower()).ratio() < 0.85:
                    useful = desc[:400]
            alt.setdefault(library_id(row), {})['context'] = useful

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


def describe(row, session, env):
    """One image, one description. Returns None on any failure: a missing
    description is fine, a wrong one is not."""
    data = fetch_image(row, session)
    if not data:
        return None

    TMP_DIR.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False, dir=TMP_DIR) as fh:
        fh.write(data)
        path = fh.name
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
        if REFUSAL.search(out):
            return None
        return out
    except (subprocess.TimeoutExpired, OSError):
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
                    help='refresh the Commons notes without calling the model')
    args = ap.parse_args()

    rows = load_rows()
    alt = load_alt()
    session = requests.Session()
    session.headers.update({'User-Agent': USER_AGENT})

    fetch_context(rows, alt, session)
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
                alt.setdefault(library_id(row), {})['visual'] = text
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
