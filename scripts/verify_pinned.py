#!/usr/bin/env python3
"""Online supply-chain check: every catalog entry must pin an IMMUTABLE commit SHA,
and the document fetched at that pinned commit must match the entry's claims.

Runs in CI after schema validation. For each entry it fetches the content repo's
document at the pinned ref via the jsDelivr CDN and asserts:
  - the ref is a commit SHA (40-hex SHA-1 or 64-hex SHA-256) - never a tag/branch,
  - the document is reachable (HTTP 200) and valid JSON,
  - its `identifier` equals the entry's identifier,
  - its `content_type` equals the entry's content_type.

Fails closed: any mismatch or fetch error is an error. Because the ref is a commit
SHA (also enforced by the entry schema), the bytes a consumer runs can never change
under them - a later force-push/tag-move in the content repo cannot affect this pin.
"""
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

CATALOGS = ["registry/community-typepacks.json", "registry/community-plugins.json"]
HEX = set("0123456789abcdef")


def load(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as fh:
        return json.load(fh)


def is_sha(ref):
    return len(ref) in (40, 64) and all(c in HEX for c in ref)


def fetch(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "vineyard-registry-verify", "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    bad = 0
    ok = 0
    for cat in CATALOGS:
        for entry in load(cat):
            ident = entry.get("identifier", "<no id>")
            ref, repo, path = entry.get("ref", ""), entry.get("repo", ""), entry.get("path", "")
            if not is_sha(ref):
                print(f"::error file={cat}::{ident}: ref '{ref}' is not an immutable commit SHA (tags/branches are mutable)")
                bad += 1
                continue
            url = f"https://cdn.jsdelivr.net/gh/{repo}@{ref}/{path}"
            try:
                doc = fetch(url)
            except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as e:
                print(f"::error file={cat}::{ident}: cannot fetch pinned doc {url}: {e}")
                bad += 1
                continue
            if doc.get("identifier") != ident:
                print(f"::error file={cat}::{ident}: pinned doc identifier '{doc.get('identifier')}' != entry identifier")
                bad += 1
            elif doc.get("content_type") != entry.get("content_type"):
                print(f"::error file={cat}::{ident}: pinned doc content_type '{doc.get('content_type')}' != entry '{entry.get('content_type')}'")
                bad += 1
            else:
                ok += 1
                print(f"  ok  {ident} @ {ref[:12]}… ({entry.get('content_type')})")
    if bad:
        print(f"\n{bad} pin verification failure(s)")
        sys.exit(1)
    print(f"\nall {ok} pinned entries verified (immutable commit SHA + document matches)")


if __name__ == "__main__":
    main()
