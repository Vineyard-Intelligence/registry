#!/usr/bin/env python3
"""Build the three published catalog files from the one-file-per-pack submissions in `packs/`.

WHY THE SOURCE IS NOT THE PUBLISHED FILE

Consumers want ONE fetch per kind, so `registry/community-*.json` stays a single array each.
Submitters must not have to edit those arrays. Appending to a shared array means every open
submission conflicts with every other one, a reviewer reads a diff that could have touched any
line of the file (including somebody else's pinned `ref`), and identifier uniqueness is a check
somebody has to remember to run.

Splitting the source resolves all three by construction: a submission is ONE new file at
`packs/<identifier>.json`, so two submissions never touch the same path, a diff that adds a file
cannot alter an existing entry, and a duplicate identifier is a git conflict on the filename
before any script looks at it.

The published arrays are generated — `.github/workflows/validate.yml` rebuilds them on merge and
commits the result, because GitHub Pages serves this repo's branch directly (`build_type:
legacy`), so the bytes consumers fetch have to exist in the tree.

Usage:
    python scripts/build_registry.py            # write the catalogs
    python scripts/build_registry.py --check    # exit 1 if the committed catalogs are stale
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PACKS = os.path.join(ROOT, "packs")

# `content_type` decides which catalog an entry lands in, so a submitter never picks a directory
# and cannot file a pack under the wrong kind. `vineyard:plugin` and `vineyard:pluginpack` share a
# catalog and a schema (registry-plugin-entry) — a single plugin is a pack of one.
CATALOG = {
    "vineyard:typepack": "registry/community-typepacks.json",
    "vineyard:plugin": "registry/community-pluginpacks.json",
    "vineyard:pluginpack": "registry/community-pluginpacks.json",
    "vineyard:skillpack": "registry/community-skillpacks.json",
}
OUTPUTS = sorted(set(CATALOG.values()))


def render(entries):
    """The exact bytes a catalog file holds — one formatting, so `--check` means something."""
    return json.dumps(entries, indent=2, ensure_ascii=False) + "\n"


def load_packs():
    """Every submission, grouped by output catalog. Raises on anything malformed."""
    if not os.path.isdir(PACKS):
        raise SystemExit(f"no submissions directory at {PACKS}")
    grouped = {out: [] for out in OUTPUTS}
    for name in sorted(os.listdir(PACKS)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(PACKS, name)
        with open(path, "r", encoding="utf-8") as fh:
            entry = json.load(fh)
        if not isinstance(entry, dict):
            raise SystemExit(f"::error file=packs/{name}::must be a single JSON object, not a list")
        ident = entry.get("identifier")
        # The filename IS the identifier. That is what makes uniqueness free: a second submission
        # of the same pack collides on the path instead of quietly becoming a duplicate row that
        # only a script would catch.
        if name != f"{ident}.json":
            raise SystemExit(
                f"::error file=packs/{name}::filename must be '<identifier>.json' — "
                f"identifier is '{ident}', so this file should be named '{ident}.json'"
            )
        out = CATALOG.get(entry.get("content_type"))
        if out is None:
            raise SystemExit(
                f"::error file=packs/{name}::unknown content_type "
                f"{entry.get('content_type')!r} (expected one of {', '.join(sorted(CATALOG))})"
            )
        grouped[out].append(entry)
    for entries in grouped.values():
        entries.sort(key=lambda e: e["identifier"])
    return grouped


def main():
    check = "--check" in sys.argv
    grouped = load_packs()
    stale = []
    for rel in OUTPUTS:
        text = render(grouped[rel])
        target = os.path.join(ROOT, rel)
        current = None
        if os.path.exists(target):
            with open(target, "r", encoding="utf-8") as fh:
                current = fh.read()
        n = len(grouped[rel])
        if check:
            if current != text:
                stale.append(rel)
                print(f"::error file={rel}::stale — run `python scripts/build_registry.py`")
            else:
                print(f"  ok  {rel} ({n} entries)")
            continue
        if current == text:
            print(f"  --  {rel} unchanged ({n} entries)")
        else:
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(text)
            print(f"  ->  {rel} written ({n} entries)")
    if stale:
        sys.exit(1)
    print(f"\n{sum(len(v) for v in grouped.values())} submission(s) in packs/")


if __name__ == "__main__":
    main()
