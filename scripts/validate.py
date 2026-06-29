#!/usr/bin/env python3
"""Validate the catalog index entries against the registry-entry JSON Schemas.

Run in CI on every pull request. Exits non-zero if any entry is invalid.
"""
import json
import os
import sys

from jsonschema import Draft202012Validator

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load(*parts):
    with open(os.path.join(ROOT, *parts), "r", encoding="utf-8") as fh:
        return json.load(fh)


CHECKS = [
    ("registry/community-typepacks.json", "schemas/registry-typepack-entry.schema.json"),
    ("registry/community-plugins.json", "schemas/registry-plugin-entry.schema.json"),
]


def main():
    bad = 0
    for data_rel, schema_rel in CHECKS:
        entries = load(data_rel)
        validator = Draft202012Validator(load(schema_rel))
        for entry in entries:
            errors = sorted(validator.iter_errors(entry), key=lambda e: list(e.path))
            if errors:
                bad += 1
                ident = entry.get("identifier", "<no identifier>")
                print(f"::error file={data_rel}::{ident}: {errors[0].message}")
        print(f"{data_rel}: {len(entries)} entr{'y' if len(entries)==1 else 'ies'} checked")
    if bad:
        print(f"\n{bad} invalid entr{'y' if bad==1 else 'ies'}")
        sys.exit(1)
    print("\nall entries valid")


if __name__ == "__main__":
    main()
