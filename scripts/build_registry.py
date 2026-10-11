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

# The segment that separates an author's reverse-DNS prefix from the pack's own name.
KINDS = ("plugins", "pluginpacks", "typepacks", "skillpacks")


def namespace(identifier):
    """The author's reverse-DNS prefix — everything before the kind segment — or None.

    Split at the FIRST kind segment, not the last: a typepack name may itself contain dots
    (`typepacks\\.[a-z0-9]+(?:[._-][a-z0-9]+)*`), so the trailing part is not a fixed width.
    """
    parts = identifier.split(".")
    for i, part in enumerate(parts):
        if part in KINDS and i >= 2:
            return ".".join(parts[:i])
    return None


def claimed_namespaces():
    """namespace -> the handle that owns it, from verified-authors.json."""
    with open(os.path.join(ROOT, "verified-authors.json"), "r", encoding="utf-8") as fh:
        owners = json.load(fh).get("authors", {})
    return {ns: handle for handle, spec in owners.items() for ns in spec.get("namespaces", [])}


def render(entries):
    """The exact bytes a catalog file holds — one formatting, so `--check` means something."""
    return json.dumps(entries, indent=2, ensure_ascii=False) + "\n"


def load_packs():
    """Every submission, grouped by output catalog. Raises on anything malformed."""
    if not os.path.isdir(PACKS):
        raise SystemExit(f"no submissions directory at {PACKS}")
    grouped = {out: [] for out in OUTPUTS}
    claimed = claimed_namespaces()
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
        # `verified` is DERIVED here, never read from the submission.
        #
        # All three entry schemas have always described it as "Mirror of verified-authors.json
        # membership; set by CI, not self-asserted" — and nothing set it. It was whatever the
        # submitter typed, which is how 24 packs said true, two identical ones said false, and two
        # skillpacks omitted it entirely, all of them the same author under the same namespace. A
        # badge that means "somebody typed true" is worse than no badge, because it reads as a
        # check that ran.
        #
        # Derived from the NAMESPACE relation rather than `author in owners`, and that ordering
        # matters: this runs BEFORE validate.py (workflow step order), so authorship has not been
        # checked yet. Asking "is this author listed" would hand verified=true to a stranger
        # publishing `com.evil.pluginpacks.x` as `author: VINEYARD` for as long as it took validate
        # to reject them. Asking "does this identifier's namespace belong to whoever signed it"
        # is correct standing alone.
        #
        # Both sides must be present: a missing `author` and an unclaimed namespace are both None,
        # and None == None would badge a malformed entry.
        ns = namespace(ident)
        author = entry.get("author")
        entry["verified"] = bool(ns and author) and claimed.get(ns) == author
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
