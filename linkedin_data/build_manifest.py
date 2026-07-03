"""
Run once to turn a raw list of LinkedIn URLs into profiles.csv - a manifest
mapping each profile to the exact filename collect.py will save it as.

Usage: python build_manifest.py urls.txt
(urls.txt = one LinkedIn URL per line, in any of the messy formats people paste)
"""
import csv
import re
import sys
from pathlib import Path
from urllib.parse import urlparse, unquote

HERE = Path(__file__).parent


def normalize_and_slug(raw_url):
    url = raw_url.strip()
    if not url:
        return None, None
    if not url.startswith("http"):
        url = "https://" + url
    parsed = urlparse(url)
    path = unquote(parsed.path).strip("/")
    # path looks like "in/some-slug"
    slug = path.split("/")[-1] if "/" in path else path
    slug = re.sub(r"[^a-zA-Z0-9\-]", "-", slug).strip("-").lower()
    canonical = f"https://www.linkedin.com/in/{slug}/"
    return canonical, slug


def main():
    if len(sys.argv) != 2:
        print("Usage: python build_manifest.py <urls.txt>")
        sys.exit(1)

    lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()

    seen_slugs = {}
    rows = []
    duplicates = []
    for line in lines:
        canonical, slug = normalize_and_slug(line)
        if not slug:
            continue
        if slug in seen_slugs:
            duplicates.append((line.strip(), seen_slugs[slug]))
            continue
        seen_slugs[slug] = line.strip()
        rows.append({"slug": slug, "linkedin_url": canonical, "raw_file": f"{slug}.txt"})

    out_path = HERE / "profiles.csv"
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["slug", "linkedin_url", "raw_file"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} unique profiles to {out_path}")
    if duplicates:
        print(f"\nSkipped {len(duplicates)} duplicate(s):")
        for dup, original in duplicates:
            print(f"  {dup}  (same as {original})")

    (HERE / "output").mkdir(exist_ok=True)
    print(f"\nNext: run collect.py to walk through profiles.csv and gather each profile's text.")


if __name__ == "__main__":
    main()
