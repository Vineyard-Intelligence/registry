#!/usr/bin/env python3
"""Submitter helper: resolve a content repo + ref to the IMMUTABLE commit SHA you must
put in a registry entry's `ref` field.

The registry pins entries to a commit SHA (not a tag/branch) so the catalog can never
serve different code than what was reviewed. Tags and branches are re-pointable; a SHA
is not. Run this against the release you are submitting and paste the printed SHA.

Usage:
  python scripts/resolve_ref.py owner/repo            # default-branch HEAD (latest)
  python scripts/resolve_ref.py owner/repo v1.2.0     # a tag
  python scripts/resolve_ref.py owner/repo main       # a branch tip

Public repos need no token. Prints the 40-char SHA on success.
"""
import subprocess
import sys


def resolve(repo, ref):
    url = f"https://github.com/{repo}"
    # Tags first. An ANNOTATED tag must be peeled (^{}) to its commit - jsDelivr and the
    # registry need the commit SHA, not the tag-object SHA. A lightweight tag is already a
    # commit. Then branch tip, then a bare ref (e.g. "HEAD"). ls-remote lists refs only, so
    # an already-known SHA does not need this helper.
    res = subprocess.run(
        ["git", "ls-remote", url, f"refs/tags/{ref}", f"refs/tags/{ref}^{{}}"],
        capture_output=True, text=True,
    )
    rows = [line.split("\t") for line in res.stdout.strip().splitlines() if "\t" in line]
    peeled = [sha for sha, name in rows if name.endswith("^{}")]
    if peeled:
        return peeled[0]  # annotated tag -> underlying commit
    plain = [sha for sha, name in rows if name == f"refs/tags/{ref}"]
    if plain:
        return plain[0]  # lightweight tag -> already a commit
    for spec in (f"refs/heads/{ref}", ref):
        res = subprocess.run(["git", "ls-remote", url, spec], capture_output=True, text=True)
        lines = res.stdout.strip().splitlines()
        if lines:
            return lines[0].split("\t")[0]
    return None


def main():
    if len(sys.argv) < 2:
        sys.stderr.write(__doc__)
        sys.exit(2)
    repo = sys.argv[1]
    ref = sys.argv[2] if len(sys.argv) > 2 else "HEAD"
    sha = resolve(repo, ref)
    if not sha:
        sys.stderr.write(f"could not resolve {repo}@{ref}\n")
        sys.exit(1)
    print(sha)


if __name__ == "__main__":
    main()
