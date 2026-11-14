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

THE BASELINE

The repository's history was squashed into one commit on 2026-10-11, so git history alone no
longer holds the refs approved before then. approved-baseline.json does: every row the published
lists carried at that point, per list. It is frozen — its digest is pinned below, and the build
refuses to run if the file changes — so a pull request cannot slip an approval into it any more
than into the generated lists. New approvals still come only from the history of packs/ and the
working tree.

THE TWINS

In November 2026 the history of the pack repositories was rewritten twice, which gave their commits
new SHAs and left every tree as it was. The baseline holds a second row for each approved ref that
moved: a twin that differs from the row before it only in `ref`, and keeps `approved_at` and
`sha256`, because the content it names is the content that was approved. The old row stays. A
project's pointer stores the absolute url and an update is offered on a version change alone, so
removing the row would refuse the pack in every project installed from that commit, with no
update to offer it.

THE CODE, NOT ONLY THE DOCUMENT

A plugin row also records `bundle_sha256`: for every code module the pinned document runs (its
`platforms.web.entry`, and each member's own), the module's path relative to the repo root and the
digest of its bytes. The document digest alone left the code out: the app checked the manifest and
then imported the module it names straight from the CDN, unhashed, so for the code that runs the
CDN was still trusted. The app now fetches the module itself and refuses bytes that do not match
(frontend plugins/bundle-pin.ts), and refuses a pack whose row records digests but not for one of
its modules. So the map is all or nothing: if any module cannot be fetched, the row gets no map and
its code is verified by membership alone, like a row without a document digest.

WHOSE BYTES ARE HASHED

GitHub's, at the pinned commit (raw.githubusercontent.com), never the CDN's. A digest is the claim
"these are the bytes an auditor reads at that commit", and the client receives everything through
jsDelivr, so a digest taken from jsDelivr would only say that the CDN served the same bytes twice:
a wrong object served the first time this build asked (and for a new commit that is often the first
time anyone asked) would be recorded, and from then on every client would accept it and refuse the
genuine bytes. Every new digest, document or module, is therefore the hash of what GitHub holds at
the commit, and the CDN copy is fetched as well and must be the same bytes: a url names a commit, so
there is no legitimate way for the two to differ, and a difference fails the build (::error::)
rather than being published. The digest written is still GitHub's, so a client that is served the
wrong bytes refuses them.

There is no CDN-only fallback. A ref GitHub no longer serves gets no new digest, and is verified by
membership, as it was before any digest existed. MEASURED 2026-11-15 on a sample of 78 approved
plugin rows (the oldest, newest and a middle ref of every repo, renamed repos and the refs the
November rewrites moved away from included): GitHub served all 77 real commits, byte for byte the
document digest already committed, and refused only the all-zero placeholder.

A CURRENT catalog row with no digest, or a current plugin row with no code digests, also fails the
build. Such a row is a pack every client would run on membership alone, and the publish job used to
commit whatever the build wrote; test_approved.py, which refuses such a row, runs only in the
validate job and never on the bytes publish commits.

WHAT IT DOES NOT COVER

The list is regenerated from the baseline and git history, so rewriting that history (a
force-push to this repo) rewrites the list. Preventing that needs a channel this repo does not
control — signatures, or an external transparency log — and is deliberately out of scope here.
Withdrawal is also not this file's job: a pack taken back keeps its historical refs here and is
stopped by `status` in the catalog, which the client applies separately (see `livePointers`).

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
# The same resolution of `platforms.web.entry` that CI's bundle check uses, so the module hashed here
# is the module that check reads and the app runs.
from verify_pinned import bundle_url  # noqa: E402

APPROVED = {ct: out.replace("community-", "approved-") for ct, out in CATALOG.items()}
OUTPUTS = sorted(set(APPROVED.values()))
# The list whose rows run code, and so carry `bundle_sha256`.
CODE_LIST = APPROVED["vineyard:pluginpack"]

# Fields copied onto an approved record. `version` and `approved_at` are for the analyst — "you
# are pinned to 1.0.0, approved on the 10th" — and play no part in the check itself.
FIELDS = ("identifier", "repo", "ref", "path", "version")

# The CDN every client fetches this registry's content through. The approved record is keyed on the
# url the client builds from repo/ref/path, and the CDN copy is fetched to check it against GitHub's.
CDN = "https://cdn.jsdelivr.net/gh/"
# Where the bytes that are hashed come from: GitHub itself, at the pinned commit (see WHOSE BYTES ARE
# HASHED).
GITHUB_RAW = "https://raw.githubusercontent.com/"


BASELINE = os.path.join(ROOT, "approved-baseline.json")
# Changing the baseline is a deliberate act: update the file and this digest in the same commit.
BASELINE_SHA256 = "02794e766e28a74314cdcee8599d4750600f66dfceb75a23a2021ea1b6733c51"


def baseline_rows():
    """list file -> rows approved before the history was squashed (see THE BASELINE)."""
    with open(BASELINE, "rb") as fh:
        data = fh.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != BASELINE_SHA256:
        raise SystemExit(
            f"::error::approved-baseline.json changed (sha256 {digest}); it is frozen — "
            "update BASELINE_SHA256 in scripts/build_approved.py only if the change is intended"
        )
    return json.loads(data)


def cdn_url(row, path):
    return f"{CDN}{row['repo']}@{row['ref']}/{path}"


def github_url(row, path):
    return f"{GITHUB_RAW}{row['repo']}/{row['ref']}/{path}"


def document_url(row):
    return cdn_url(row, row["path"])


def fetch_bytes(url):
    """The bytes at a pinned url, or None if they cannot be fetched (said on stdout)."""
    req = urllib.request.Request(url, headers={"User-Agent": "vineyard-registry-approved"})
    # The workflow's token, to GitHub only: a first build hashes close to a thousand files there,
    # past what an anonymous client is allowed. Never sent to the CDN, and unredirected: urllib
    # copies ordinary headers onto a redirect wherever it points, and in the publish job this token
    # can push to this repository.
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith(GITHUB_RAW):
        req.add_unredirected_header("Authorization", f"token {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except Exception as exc:  # noqa: BLE001 — every failure mode is "no digest", none is fatal
        print(f"  could not fetch {url}: {exc}")
        return None


def pinned_bytes(row, path, fetch, errors):
    """What GitHub holds at `path` in the pinned commit, or None when it cannot be fetched.

    The CDN copy must be the same bytes. When it is not, the problem goes into `errors`, which fails
    the build, and GitHub's bytes are still returned: the digest stays a statement about the commit,
    so a client served the CDN's other bytes refuses them. A CDN copy that cannot be fetched is only
    said: a client cannot load it either, so nothing would run unchecked.
    """
    data = fetch(github_url(row, path))
    if data is None:
        return None
    served = fetch(cdn_url(row, path))
    if served is None:
        print(f"  {cdn_url(row, path)} could not be fetched to compare with GitHub")
    elif served != data:
        errors.append(
            f"{cdn_url(row, path)} serves bytes other than GitHub holds at that commit "
            f"(sha256 {digest_of(served)[:12]}…, GitHub {digest_of(data)[:12]}…). Every client would "
            "refuse it; purge the CDN copy, or find out who changed it, before publishing"
        )
    return data


def digest_of(data):
    """SHA-256 of a pinned file's BYTES, or None when there are none (the fetch failed).

    WHY A HASH WHEN THE URL ALREADY CARRIES A COMMIT

    The commit pins what GitHub holds. It does not pin what a consumer RECEIVES: every client
    fetches through jsDelivr, and nothing on the client side checks that the bytes coming back are
    the bytes that commit contains. Recording the digest of GitHub's bytes here (pinned_bytes) takes
    the CDN out of the trusted set — a consumer can verify what it got rather than trust who handed
    it over. Taken from the CDN instead, it would only prove the CDN consistent with itself.

    None is not a failure. A ref whose repo has since been deleted or made private cannot be
    hashed, and refusing to publish the list over it would take every OTHER pack down with it.
    A row without a digest is verified by membership alone, exactly as before this existed.
    """
    return hashlib.sha256(data).hexdigest() if data is not None else None


def bundle_digests(row, doc_digest, doc_bytes, fetch=None, errors=None):
    """entry path -> SHA-256 of every code module the pinned document runs, or None.

    `doc_bytes` must hash to `doc_digest`, the digest this row publishes for the document: the
    modules are read out of the document, so a map built from any other bytes would vouch for code
    the approved document does not name. A document that names no module (every entry `inline`)
    gets an empty map, which is true: it runs nothing.

    Each module is hashed as GitHub holds it at the pin, and its CDN copy must match (pinned_bytes;
    a mismatch goes into `errors`).

    None — no map — when the document bytes are missing or do not match, when they are not a JSON
    object, when an entry cannot be resolved inside the pin, and when ANY module cannot be fetched
    from GitHub. All or nothing, because the app refuses a pack whose map leaves one of its modules
    out: a partial map would take a pack down over one miss at build time, where no map keeps it
    loading on membership, as before (and fails the build if the row is current).
    """
    fetch = fetch or fetch_bytes
    errors = [] if errors is None else errors
    if digest_of(doc_bytes) != doc_digest:
        return None
    try:
        doc = json.loads(doc_bytes)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(doc, dict):
        return None
    # Resolved against the CDN root, which is the url the app resolves the entry against; the
    # relative path that comes out is what is hashed, and what the app looks the module up by.
    root = cdn_url(row, "")
    members = doc.get("plugins") if isinstance(doc.get("plugins"), list) else []
    paths = set()
    for manifest in [doc] + [m for m in members if isinstance(m, dict)]:
        web = ((manifest.get("platforms") or {}).get("web")) or ((doc.get("platforms") or {}).get("web")) or {}
        if web.get("entry") == "inline":
            continue  # names no module: the app drops it and the plugin is not runnable
        try:
            url = bundle_url(manifest, doc, root)
        except ValueError:
            # An entry outside the pin. The app refuses the pack for that (entry-pin.ts); a map
            # here would claim to be complete while leaving it out.
            return None
        if url:
            paths.add(url[len(root):])
    out = {}
    for path in sorted(paths):
        data = pinned_bytes(row, path, fetch, errors)
        if data is None:
            print(f"  no code digests for {document_url(row)}: GitHub did not serve {path}")
            return None
        out[path] = digest_of(data)
    return out


def existing_digests():
    """url -> sha256 already committed, so a build re-hashes only what is new.

    Regenerating every digest on every build would mean a CDN fetch per approved document and
    another per code module, close to a thousand per CI run, and a build that fails when the CDN
    has a bad minute. It would also make the digest a statement about
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


def existing_bundles():
    """url -> `bundle_sha256` already committed, for the same reason as existing_digests."""
    out = {}
    for rel in OUTPUTS:
        target = os.path.join(ROOT, rel)
        if not os.path.exists(target):
            continue
        with open(target, "r", encoding="utf-8") as fh:
            for row in json.load(fh):
                if isinstance(row.get("bundle_sha256"), dict) and row.get("repo") and row.get("ref") and row.get("path"):
                    out[document_url(row)] = row["bundle_sha256"]
    return out


def add_digests(seen, known, known_bundles, fetch=None):
    """Write `sha256` onto every row whose document can be hashed, and `bundle_sha256` onto every
    plugin row whose modules can be. Committed values are reused; only what is new is fetched.

    Returns the problems that must fail the build: a CDN copy that differs from GitHub's, and a
    committed document digest that GitHub's bytes do not match."""
    fetch = fetch or fetch_bytes
    errors = []
    for out, rows in seen.items():
        for row in rows.values():
            url = document_url(row)
            doc_bytes = None
            digest = known.get(url)
            if not digest:
                doc_bytes = pinned_bytes(row, row["path"], fetch, errors)
                digest = digest_of(doc_bytes)
            if not digest:
                print(f"  no digest for {url}")
                continue
            row["sha256"] = digest
            if out != CODE_LIST:
                continue
            bundles = known_bundles.get(url)
            if bundles is None:
                if doc_bytes is None:
                    doc_bytes = pinned_bytes(row, row["path"], fetch, errors)
                    # A committed digest was taken from the CDN before this build hashed GitHub's
                    # bytes. This is the one time it is read against GitHub; a difference means
                    # clients have been accepting a document the commit does not hold.
                    if doc_bytes is not None and digest_of(doc_bytes) != digest:
                        errors.append(
                            f"{url}: the committed sha256 {digest[:12]}… is not the digest of what GitHub "
                            f"holds at that commit ({digest_of(doc_bytes)[:12]}…)"
                        )
                bundles = bundle_digests(row, digest, doc_bytes, fetch, errors)
            if bundles is not None:
                row["bundle_sha256"] = dict(sorted(bundles.items()))
    return errors


def unverified_current(seen, current):
    """The current catalog rows the app would load on membership alone, as messages.

    `current` is the (list, key) of every row packs/ points at today. A row the catalog lists now was
    fetchable when it was approved (verify_pinned.py), so one without a document digest, or a plugin
    row without code digests, is a build that skipped it, or a GitHub that would not answer.
    Publishing it would run that pack's code unhashed in every client until some later build.
    """
    out = []
    for list_name, key in sorted(current):
        row = seen[list_name][key]
        if not row.get("sha256"):
            out.append(f"{row['identifier']} @ {row['ref'][:12]}: no document digest")
        elif list_name == CODE_LIST and not isinstance(row.get("bundle_sha256"), dict):
            out.append(f"{row['identifier']} @ {row['ref'][:12]}: no code digests")
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
    """One approved record.

    Raises ValueError when the revision lacks identifier, repo, ref or path: the entry schema
    requires all four, so a revision without them cannot be a published approval.
    """
    missing = [f for f in FIELDS if f != "version" and not entry.get(f)]
    if missing:
        raise ValueError(f"{entry.get('identifier', '<no identifier>')}: missing {', '.join(missing)}")
    row = {f: entry.get(f) for f in FIELDS}
    row["approved_at"] = approved_at
    return row


def collect(problems=None):
    """kind file -> approved records, deduplicated on the tuple that identifies the content.

    Whatever must fail the build (see add_digests and unverified_current) is appended to
    `problems` when one is given."""
    seen = {out: {} for out in OUTPUTS}
    current = set()
    # The baseline first, so an approval keeps the date it was first approved on.
    for out, rows in baseline_rows().items():
        for row in rows:
            seen[out].setdefault((row["identifier"], row["repo"], row["ref"], row["path"]), dict(row))
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
            try:
                row = record(entry, date)
            except ValueError as e:
                raise SystemExit(f"::error file={path}::revision {sha[:12]}: {e}") from None
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
        if out is None:
            continue
        try:
            row = record(entry, git("log", "-1", "--format=%ad", "--date=short").strip())
        except ValueError as e:
            raise SystemExit(f"::error file=packs/{name}::{e}") from None
        key = (row["identifier"], row["repo"], row["ref"], row["path"])
        seen[out].setdefault(key, row)
        current.add((out, key))
    errors = add_digests(seen, existing_digests(), existing_bundles())
    errors += unverified_current(seen, current)
    if problems is not None:
        problems.extend(errors)
    return {
        out: sorted(rows.values(), key=lambda r: (r["identifier"], r["approved_at"], r["ref"]))
        for out, rows in seen.items()
    }


def main():
    check = "--check" in sys.argv
    problems = []
    collected = collect(problems)
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
    # After writing, so a local run leaves the lists to look at; the publish job stops on the exit
    # status and commits nothing.
    for problem in problems:
        print(f"::error::{problem}")
    if stale:
        print(
            "::error::approved lists are stale — run `python scripts/build_approved.py`: "
            + ", ".join(stale)
        )
    return 1 if stale or problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
