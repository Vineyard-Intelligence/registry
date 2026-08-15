#!/usr/bin/env python3
"""Publish every (identifier, repo, ref, path) this registry has EVER approved.

WHY THIS EXISTS, AND WHY THE CATALOG IS NOT ENOUGH

The catalog holds one row per pack: the ref that is current. That is the right answer for
"what should I install", and the wrong answer for "may this project load what it already
installed", because an install PINS the commit it approved and the catalog moves on without it.
A client that checked pointers against the catalog alone would refuse every correctly-pinned
older install the moment a pack was republished.

The check itself is not optional, and the reason is not obvious. A pointer's url carries the
repo and the commit, so it is tempting to believe that checking its SHAPE — right org, 40-hex
ref — is enough. MEASURED, and it is not:

    https://cdn.jsdelivr.net/gh/facebook/react@<a fork PR's head commit>/package.json  ->  200

GitHub keeps the head commit of every pull request in the BASE repository's object store
(`refs/pull/N/head`), forever, whether or not the PR was merged or even closed. jsDelivr and
raw.githubusercontent will both serve it under the base repo's path. So anyone who can open a
pull request against a public pack repo — no write access, no review, no merge — can produce a
url that is inside this registry's org, carries a real 40-hex commit, satisfies the app's CSP,
and serves code they wrote. Nothing short of "this exact (identifier, repo, ref, path) was
published by the registry" rejects it.

WHAT IT DOES NOT COVER

The list is regenerated from git history, so rewriting that history (a force-push to this repo)
rewrites the list. Preventing that needs a channel this repo does not control — signatures, or
an external transparency log — and is deliberately out of scope here. Withdrawal is also not
this file's job: a pack taken back keeps its historical refs here and is stopped by `status` in
the catalog, which the client applies separately (see `livePointers`).

Entries are never removed. A pack file deleted from `packs/` keeps the refs it was approved at,
because an install pinned to one of them is not retroactively illegitimate — it was reviewed.

Usage:
    python scripts/build_approved.py            # write the lists
    python scripts/build_approved.py --check    # exit 1 if the committed lists are stale
"""
import hashlib
import json
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Same split as the catalogs, so a client fetches the approved list for the kind it is checking
# without having to know that plugins and pluginpacks share one.
from build_registry import CATALOG, render  # noqa: E402  (same directory, same source of truth)

APPROVED = {ct: out.replace("community-", "approved-") for ct, out in CATALOG.items()}
OUTPUTS = sorted(set(APPROVED.values()))

# Fields copied onto an approved record. `version` and `approved_at` are for the analyst — "you
# are pinned to 1.0.0, approved on the 10th" — and play no part in the check itself.
FIELDS = ("identifier", "repo", "ref", "path", "version")

# The CDN this registry's content is served from. Only used to fetch a document for hashing; the
# approved record stores repo/ref/path and the client rebuilds the url the same way it always has.
CDN = "https://cdn.jsdelivr.net/gh/"


def document_url(row):
    return f"{CDN}{row['repo']}@{row['ref']}/{row['path']}"


def sha256_of(url):
    """SHA-256 of the pinned document's BYTES, or None if it cannot be fetched.

    WHY A HASH WHEN THE URL ALREADY CARRIES A COMMIT

    The commit pins what GitHub holds. It does not pin what a consumer RECEIVES: every client
    fetches through jsDelivr, and nothing on the client side checks that the bytes coming back are
    the bytes that commit contains. Recording the digest here takes the CDN out of the trusted set
    — a consumer can verify what it got rather than trust who handed it over.

    None is not a failure. A ref whose repo has since been deleted or made private cannot be
    hashed, and refusing to publish the list over it would take every OTHER pack down with it.
    A row without a digest is verified by membership alone, exactly as before this existed.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "vineyard-registry-approved"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return hashlib.sha256(resp.read()).hexdigest()
    except Exception as exc:  # noqa: BLE001 — every failure mode is "no digest", none is fatal
        print(f"  no digest for {url}: {exc}")
        return None


def existing_digests():
    """url -> sha256 already committed, so a build re-hashes only what is new.

    Regenerating every digest on every build would mean ~80 CDN fetches per CI run and a build
    that fails when the CDN has a bad minute. It would also make the digest a statement about
    today rather than about the commit, which is the opposite of the point.
    """
    out = {}
    for rel in OUTPUTS:
        target = os.path.join(ROOT, rel)
        if not os.path.exists(target):
            continue
        with open(target, "r", encoding="utf-8") as fh:
            for row in json.load(fh):
                if row.get("sha256") and row.get("repo") and row.get("ref") and row.get("path"):
                    out[document_url(row)] = row["sha256"]
    return out


def git(*args):
    return subprocess.run(
        ["git", "-C", ROOT, *args], capture_output=True, text=True, check=True
    ).stdout


def pack_paths_ever():
    """Every path that has existed under packs/, including ones since deleted."""
    # HEAD's history, not --all. "Approved" means "merged to the published branch"; --all
    # would also count any other ref that happens to be in the clone, which on a CI runner
    # with a full fetch is every branch in the repo.
    out = git("log", "--pretty=format:", "--name-only", "--diff-filter=AMR", "--", "packs")
    return sorted({line.strip() for line in out.splitlines() if line.strip().endswith(".json")})


def revisions_of(path):
    """(commit, YYYY-MM-DD) for every revision that touched `path`, oldest first."""
    out = git("log", "--follow", "--format=%H %ad", "--date=short", "--", path)
    rows = [line.split(" ", 1) for line in out.splitlines() if line.strip()]
    return [(sha, date) for sha, date in reversed(rows)]


def record(entry, approved_at):
    """One approved record, or None when the revision predates the pinning fields."""
    if not all(entry.get(f) for f in FIELDS if f != "version"):
        return None
    row = {f: entry.get(f) for f in FIELDS}
    row["approved_at"] = approved_at
    return row


def collect():
    """kind file -> approved records, deduplicated on the tuple that identifies the content."""
    seen = {out: {} for out in OUTPUTS}
    for path in pack_paths_ever():
        for sha, date in revisions_of(path):
            try:
                blob = git("show", f"{sha}:{path}")
            except subprocess.CalledProcessError:
                continue  # the revision that deleted it
            try:
                entry = json.loads(blob)
            except json.JSONDecodeError:
                continue  # a revision that was never valid cannot have been published
            if not isinstance(entry, dict):
                continue
            out = APPROVED.get(entry.get("content_type"))
            if out is None:
                continue
            row = record(entry, date)
            if row is None:
                continue
            # Keyed on what the client actually compares. The FIRST date wins: re-approving the
            # same commit later does not move when it was approved.
            key = (row["identifier"], row["repo"], row["ref"], row["path"])
            seen[out].setdefault(key, row)
    # The working tree too, unconditionally. On a pull request the new ref is not in history
    # yet, so without this `--check` would call a correctly-updated list stale.
    packs = os.path.join(ROOT, "packs")
    for name in sorted(os.listdir(packs)) if os.path.isdir(packs) else []:
        if not name.endswith(".json"):
            continue
        with open(os.path.join(packs, name), "r", encoding="utf-8") as fh:
            entry = json.load(fh)
        out = APPROVED.get(entry.get("content_type"))
        row = record(entry, git("log", "-1", "--format=%ad", "--date=short").strip()) if out else None
        if row is None:
            continue
        seen[out].setdefault((row["identifier"], row["repo"], row["ref"], row["path"]), row)
    known = existing_digests()
    for rows in seen.values():
        for row in rows.values():
            url = document_url(row)
            digest = known.get(url) or sha256_of(url)
            if digest:
                row["sha256"] = digest
    return {
        out: sorted(rows.values(), key=lambda r: (r["identifier"], r["approved_at"], r["ref"]))
        for out, rows in seen.items()
    }


def main():
    check = "--check" in sys.argv
    collected = collect()
    stale = []
    for rel in OUTPUTS:
        text = render(collected[rel])
        target = os.path.join(ROOT, rel)
        current = None
        if os.path.exists(target):
            with open(target, "r", encoding="utf-8") as fh:
                current = fh.read()
        n = len(collected[rel])
        if check:
            if current != text:
                stale.append(rel)
            else:
                print(f"{rel}: up to date ({n} approved)")
            continue
        if current == text:
            print(f"{rel}: unchanged ({n} approved)")
            continue
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"{rel}: wrote {n} approved")
    if stale:
        print(
            "::error::approved lists are stale — run `python scripts/build_approved.py`: "
            + ", ".join(stale)
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
