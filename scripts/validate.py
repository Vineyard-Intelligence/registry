#!/usr/bin/env python3
"""Validate every submission in `packs/` against the registry-entry JSON Schemas.

Run in CI on every pull request. Exits non-zero if any entry is invalid.

Validates the SUBMISSIONS, not the generated catalogs, so a failure annotates the file the
author actually wrote — `packs/<identifier>.json` — rather than a line in a build artifact
they are not supposed to edit. `build_registry.py` decides which schema applies, by the same
`content_type` routing that decides which catalog the entry lands in.
"""
import json
import os
import sys

from jsonschema import Draft202012Validator

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_registry import ROOT, load_packs  # noqa: E402

SCHEMA_FOR = {
    "registry/community-typepacks.json": "schemas/registry-typepack-entry.schema.json",
    "registry/community-pluginpacks.json": "schemas/registry-plugin-entry.schema.json",
    "registry/community-skillpacks.json": "schemas/registry-skillpack-entry.schema.json",
}


KINDS = ("plugins", "pluginpacks", "typepacks", "skillpacks")


def load(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as fh:
        return json.load(fh)


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


def authorship_error(entry, owners):
    """How the entry misuses `verified` or `author`, or None.

    `verified` and `author` are the two fields that make a pack look trustworthy, and both were
    plain submitter input: the schema promised `verified` was "set by CI, not self-asserted", but
    no file backed it and no code read it. The check runs in both directions on purpose —

      - a namespace someone owns may only be published under by that someone, so a stranger
        cannot list under `run.vineyard.*`;
      - a name someone owns may only be worn inside their own namespaces, so a stranger cannot
        publish `com.evil.pluginpacks.x` as `author: vineyard-run`.

    One direction alone leaves the other half of the impersonation open.
    """
    ident = entry.get("identifier", "")
    author = entry.get("author")
    ns = namespace(ident)
    claimed_by = {n: handle for handle, spec in owners.items() for n in spec.get("namespaces", [])}

    if ns in claimed_by and author != claimed_by[ns]:
        return (
            f"namespace '{ns}' belongs to {claimed_by[ns]}, but this entry is authored by "
            f"'{author}'. Publish under a namespace you control."
        )
    if author in owners and ns not in owners[author].get("namespaces", []):
        return (
            f"author '{author}' is a listed author whose namespaces are "
            f"{owners[author].get('namespaces', [])}, but this entry is '{ns}'. "
            f"An entry may not claim a name it does not publish under."
        )
    if entry.get("verified") is True and author not in owners:
        return (
            f"verified=true but '{author}' is not in verified-authors.json. The badge is set by "
            f"the operator, not by the submission — omit it."
        )
    return None


def main():
    bad = total = 0
    owners = load("verified-authors.json").get("authors", {})
    for catalog, entries in sorted(load_packs().items()):
        validator = Draft202012Validator(load(SCHEMA_FOR[catalog]))
        for entry in entries:
            total += 1
            ident = entry.get("identifier", "<no identifier>")
            errors = sorted(validator.iter_errors(entry), key=lambda e: list(e.path))
            if errors:
                bad += 1
                print(f"::error file=packs/{ident}.json::{ident}: {errors[0].message}")
                continue
            problem = authorship_error(entry, owners)
            if problem:
                bad += 1
                print(f"::error file=packs/{ident}.json::{ident}: {problem}")
        print(f"{catalog}: {len(entries)} entr{'y' if len(entries)==1 else 'ies'} checked")
    if bad:
        print(f"\n{bad} invalid entr{'y' if bad==1 else 'ies'} of {total}")
        sys.exit(1)
    print(f"\nall {total} entries valid, and every verified badge is backed by verified-authors.json")


if __name__ == "__main__":
    main()
