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


def load(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as fh:
        return json.load(fh)



def main():
    bad = total = 0
    for catalog, entries in sorted(load_packs().items()):
        validator = Draft202012Validator(load(SCHEMA_FOR[catalog]))
        for entry in entries:
            total += 1
            ident = entry.get("identifier", "<no identifier>")
            errors = sorted(validator.iter_errors(entry), key=lambda e: list(e.path))
            if errors:
                bad += 1
                print(f"::error file=packs/{ident}.json::{ident}: {errors[0].message}")
        print(f"{catalog}: {len(entries)} entr{'y' if len(entries)==1 else 'ies'} checked")
    if bad:
        print(f"\n{bad} invalid entr{'y' if bad==1 else 'ies'} of {total}")
        sys.exit(1)
    print(f"\nall {total} entries valid")


if __name__ == "__main__":
    main()
