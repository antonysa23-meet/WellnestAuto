"""
Run ONCE, locally, before deploying. Walks you through every profile in
profiles.csv one at a time: opens it in your browser, then watches your
clipboard - as soon as you select the profile text and Ctrl+C, it's captured
and saved automatically, and the next profile opens.

You do the reading/selecting/copying by hand - this script only watches your
clipboard and does the bookkeeping (which URL you're on, saving to the right
filename, skipping ones you've already done). It never fetches anything from
linkedin.com itself.

Requires: pip install pyperclip

Usage: python collect.py
  - Select the profile text on the page and press Ctrl+C. It's saved automatically
    and the next profile opens - no need to switch back to the terminal.
  - Press 's' + Enter in the terminal to skip a profile that won't load.
  - Ctrl+C in the terminal to stop - safe to re-run later, already-collected
    profiles are skipped.
"""
import csv
import queue
import re
import threading
import time
import webbrowser
from pathlib import Path

import pyperclip

HERE = Path(__file__).parent
PROFILES_CSV = HERE / "profiles.csv"
OUTPUT_DIR = HERE / "output"
SKIPPED_LOG = HERE / "skipped.txt"

MIN_LENGTH = 40  # ignore clipboard changes shorter than this (likely an accidental copy)
POLL_SECONDS = 0.3

# Guards against your terminal's own printed progress lines getting picked up as
# a "new copy" - some terminals (e.g. Windows Terminal) copy to the clipboard
# whenever you select text, and selecting the log to read it can overwrite
# whatever you just copied from the browser.
_OWN_OUTPUT_MARKERS = ("-> captured (", "-> skipped", "profiles collected in output")


def _looks_like_own_output(text):
    return any(marker in text for marker in _OWN_OUTPUT_MARKERS) or bool(
        re.match(r"^\[\d+/\d+\]", text.strip())
    )

# A single background thread reads stdin for the whole run, so 's' + Enter
# always reaches whichever profile is currently being waited on - spawning a
# fresh input() thread per profile would leave old ones blocked on stdin and
# competing for keystrokes.
_stdin_lines = queue.Queue()


def _read_stdin_forever():
    while True:
        try:
            line = input()
        except EOFError:
            return
        _stdin_lines.put(line)


threading.Thread(target=_read_stdin_forever, daemon=True).start()


def wait_for_copy_or_skip(baseline):
    """
    Blocks until the clipboard changes to something substantial, or the user
    types 's' to skip. Returns (captured_text, new_baseline) or (None, new_baseline).

    Always compares/stores the RAW clipboard value (never the .strip()'d one) -
    comparing a stripped value against a freshly-read raw one would almost always
    look "changed" (trailing whitespace browsers add on copy) even when nothing
    new was actually copied, which is what caused the previous run to cascade
    through every remaining profile reusing the first capture.
    """
    last_seen = baseline
    while True:
        try:
            answer = _stdin_lines.get_nowait()
            if answer.strip().lower() == "s":
                return None, last_seen
        except queue.Empty:
            pass
        try:
            current = pyperclip.paste()
        except Exception:
            current = last_seen
        if current != last_seen:
            last_seen = current
            stripped = current.strip()
            if _looks_like_own_output(stripped):
                print("  (that looked like this script's own terminal output, not a profile - ignoring)")
            elif len(stripped) >= MIN_LENGTH:
                return stripped, last_seen
            else:
                print(f"  (copied text looks short - {len(stripped)} chars - still watching, or press s+Enter to skip)")
        time.sleep(POLL_SECONDS)


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    with PROFILES_CSV.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    remaining = [r for r in rows if not (OUTPUT_DIR / r["raw_file"]).exists()]
    print(f"{len(rows)} total profiles, {len(remaining)} remaining.")
    print("For each profile: select the text and press Ctrl+C - it saves automatically.")
    print("Press 's' + Enter to skip a profile.\n")

    skipped = []
    baseline = pyperclip.paste()
    for i, row in enumerate(remaining, 1):
        out_path = OUTPUT_DIR / row["raw_file"]
        print(f"[{i}/{len(remaining)}] {row['slug']}")
        print(f"  {row['linkedin_url']}")
        webbrowser.open(row["linkedin_url"])

        text, baseline = wait_for_copy_or_skip(baseline)
        if text is None:
            print("  -> skipped\n")
            skipped.append(row["linkedin_url"])
            continue

        out_path.write_text(text, encoding="utf-8")
        print(f"  -> captured ({len(text)} chars), saved to output/{row['raw_file']}\n")

    if skipped:
        SKIPPED_LOG.write_text("\n".join(skipped), encoding="utf-8")
        print(f"Skipped {len(skipped)} profile(s), logged to {SKIPPED_LOG.name}")

    done = len(rows) - len([r for r in rows if not (OUTPUT_DIR / r["raw_file"]).exists()])
    print(f"\nDone. {done}/{len(rows)} profiles collected in output/.")


if __name__ == "__main__":
    main()
