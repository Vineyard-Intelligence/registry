#!/usr/bin/env python3
"""Self-test for the approved-ref lists.

Two of these guard failures that would only ever be discovered in production, in opposite
directions:

  * If a CURRENT catalog row is missing from its approved list, the client refuses the pack every
    project has installed. Loud, immediate, and caused by a build ordering mistake nobody would
    look for.

  * If a ref that was never published IS in the list, the whole check is decoration. The shape
    that matters is not a random string — it is a commit that lives in the right org's repo and
    is served by the CDN, which is what a pull request against a public pack repo produces. That
    case cannot be exercised by the live data, so it is constructed here.

Run: python scripts/test_approved.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from build_approved import APPROVED, OUTPUTS, collect, record  # noqa: E402

FAILURES = []


def check(name, ok):
    print(("  ok  " if ok else "  FAIL") + f"  {name}")
    if not ok:
        FAILURES.append(name)


def load(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as fh:
        return json.load(fh)


def keys_of(rows):
    return {(r["identifier"], r["repo"], r["ref"], r["path"]) for r in rows}


print("every current catalog row is approved")
for content_type, approved_rel in sorted(APPROVED.items()):
    catalog_rel = approved_rel.replace("approved-", "community-")
    approved = keys_of(load(approved_rel))
    for entry in load(catalog_rel):
        key = (entry["identifier"], entry.get("repo"), entry.get("ref"), entry.get("path"))
        check(f"{entry['identifier']} @ {str(entry.get('ref'))[:8]}", key in approved)

print("every current catalog row carries a document digest")
# The digest is what takes the CDN out of the trusted set: the commit pins what GitHub holds, the
# digest pins what a client RECEIVES. Historical rows may lack one (a repo since deleted cannot be
# hashed, and refusing to publish over that would take every other pack down with it) — but a row
# the catalog points at TODAY is fetchable by definition, so a missing digest there is a build
# that silently skipped it.
for content_type, approved_rel in sorted(APPROVED.items()):
    catalog_rel = approved_rel.replace("approved-", "community-")
    digests = {
        (r["repo"], r["ref"], r["path"]): r.get("sha256") for r in load(approved_rel)
    }
    for entry in load(catalog_rel):
        digest = digests.get((entry.get("repo"), entry.get("ref"), entry.get("path")))
        check(f"{entry['identifier']} has a digest", bool(digest))
        check(f"{entry['identifier']} digest is sha256-shaped",
              bool(digest) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest))

print("every current plugin row carries the digests of its code")
# The same argument, for the modules: a pack the catalog points at today has a fetchable bundle,
# so a row without `bundle_sha256` is a build that skipped it, and the app would run that pack's
# code on membership alone (frontend plugins/bundle-pin.ts).
bundles = {
    (r["repo"], r["ref"], r["path"]): r.get("bundle_sha256") for r in load("registry/approved-pluginpacks.json")
}
for entry in load("registry/community-pluginpacks.json"):
    got = bundles.get((entry.get("repo"), entry.get("ref"), entry.get("path")))
    check(f"{entry['identifier']} has code digests", isinstance(got, dict) and bool(got))
    check(
        f"{entry['identifier']} code digests are sha256-shaped",
        isinstance(got, dict)
        and all(len(v) == 64 and all(c in "0123456789abcdef" for c in v) for v in got.values()),
    )

print("a commit that was never published is not approved")
# Shaped exactly like the attack: this registry's org, a real pack repo, a well-formed 40-hex
# ref. Only membership rejects it — the org prefix and the SHA pattern both pass.
approved = keys_of(load("registry/approved-pluginpacks.json"))
real = load("registry/community-pluginpacks.json")[0]
forged = (real["identifier"], real["repo"], "dead" * 10, real["path"])
check("fork-PR-shaped ref is absent", forged not in approved)
check("the real ref it was derived from IS present",
      (real["identifier"], real["repo"], real["ref"], real["path"]) in approved)

print("a record is keyed on everything the client compares")
# Dropping any one of these from the key would merge two distinct documents into one approval.
base = {"identifier": "i", "repo": "o/r", "ref": "a" * 40, "path": "p.json", "version": "1"}
row = record(dict(base), "2026-01-01")
check("record carries repo, ref and path", all(row.get(f) for f in ("repo", "ref", "path")))
check("record carries the approval date", row["approved_at"] == "2026-01-01")


def refused(entry):
    try:
        record(entry, "d")
    except ValueError:
        return True
    return False


check("a revision with no ref is refused", refused({"identifier": "i", "repo": "o/r", "path": "p"}))
check("a revision with no repo is refused", refused({"identifier": "i", "ref": "a" * 40, "path": "p"}))
check("version is optional — it is for the analyst, not the check",
      record({"identifier": "i", "repo": "o/r", "ref": "a" * 40, "path": "p"}, "d") is not None)

print("the build is deterministic")
first = collect()
second = collect()
check("two runs agree", {k: json.dumps(v, sort_keys=True) for k, v in first.items()}
      == {k: json.dumps(v, sort_keys=True) for k, v in second.items()})
for rel in OUTPUTS:
    rows = first[rel]
    check(f"{rel} is sorted", rows == sorted(rows, key=lambda r: (r["identifier"], r["approved_at"], r["ref"])))
    check(f"{rel} has no duplicate keys", len(keys_of(rows)) == len(rows))

print()
if FAILURES:
    print(f"::error::{len(FAILURES)} approved-list check(s) failed: {', '.join(FAILURES)}")
    raise SystemExit(1)
print("approved lists: all checks passed")
