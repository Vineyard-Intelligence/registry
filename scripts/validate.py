#!/usr/bin/env python3
"""Validate every submission in `packs/` against the registry-entry JSON Schemas.

Run in CI on every pull request. Exits non-zero if any entry is invalid.

Validates the SUBMISSIONS, not the generated catalogs, so a failure annotates the file the
author actually wrote — `packs/<identifier>.json` — rather than a line in a build artifact
they are not supposed to edit. `build_registry.py` decides which schema applies, by the same
`content_type` routing that decides which catalog the entry lands in.

Needs the network: the repository-owner check asks the GitHub API once per verified author's
repository, and the pinned-commit check once more per pin it asks about (see `settled_pins` for
which those are). CI passes REGISTRY_BASE_SHA, so a pull request asks only about the pins it adds
or moves. Without it every pin is asked — a full audit — which is more than the unauthenticated
60 calls/hour, so run that locally with a token:
`GH_TOKEN="$(gh auth token)" python scripts/validate.py`.
"""
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from functools import lru_cache
from urllib.parse import quote

from jsonschema import Draft202012Validator

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_registry import ROOT, load_packs, namespace  # noqa: E402

SCHEMA_FOR = {
    "registry/community-typepacks.json": "schemas/registry-typepack-entry.schema.json",
    "registry/community-pluginpacks.json": "schemas/registry-plugin-entry.schema.json",
    "registry/community-skillpacks.json": "schemas/registry-skillpack-entry.schema.json",
}


def load(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as fh:
        return json.load(fh)


class GitHubError(Exception):
    """A non-2xx GitHub API answer, keeping the status and GitHub's own `message`.

    Kept apart from a bare exception because exactly one 404 is an ANSWER rather than a failure —
    see `containing_ref` — and telling them apart needs the message text, which urllib's
    HTTPError only exposes by reading the body.
    """

    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


@lru_cache(maxsize=None)
def github_api(path):
    """GET `path` from the GitHub REST API and return the parsed body. Raises on any failure.

    Cached per path for the length of one run, and that is what keeps this file inside the rate
    limit: the repository lookup is shared between the owner check and the reachability check,
    and the branch and tag listings of a repository that several entries pin (`typepack-basic`
    backs most of the Type Packs) are fetched once. A failure is not cached, so a second entry
    retries — still fail-closed, just not wrongly sticky.

    GITHUB_TOKEN (CI) or GH_TOKEN (a local `gh` user) is sent when set, to api.github.com only.
    """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "vineyard-registry-validate"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"https://api.github.com{path}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            message = json.load(exc).get("message", "")
        except Exception:  # noqa: BLE001 — a body that is not JSON still has a status worth naming
            message = str(exc.reason)
        raise GitHubError(exc.code, message) from None


def repo_api(repo):
    """`/repos/{owner}/{name}` with both segments escaped.

    The entry schema only promises `^[^/]+/[^/]+$`, so a `?` or `#` in submitter text would
    otherwise reshape the request — a query string glued onto a compare is not the compare that
    was asked for, and the answer would be trusted all the same.
    """
    owner, _, name = repo.partition("/")
    return f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"


def repo_owner_id(repo):
    """The numeric GitHub owner id of `repo`, from the API (cached through `github_api`)."""
    return int(github_api(repo_api(repo))["owner"]["id"])


def repo_owner_error(entry, owners, owner_id):
    """How the entry's repository owner contradicts the verified author, or None.

    `handle` is display text. The only comparison is the numeric id: logins are renameable and
    copyable; the account id is not. A listed author's pack must live in a repository owned by
    that id, otherwise a stranger can publish `run.vineyard.*` as `author: VINEYARD` from their
    own repository and both namespace/author strings would still match.
    """
    author = entry.get("author")
    spec = owners.get(author)
    if not spec:
        return None
    github = spec.get("github") or {}
    expected = github.get("id")
    if not isinstance(expected, int):
        return f"verified author '{author}' has no numeric github.id"
    repo = entry.get("repo", "")
    try:
        actual = owner_id(repo)
    except Exception as exc:  # noqa: BLE001 — every failure means "not proven"
        return f"could not verify the owner of repo '{repo}' for author '{author}': {exc}"
    if actual != expected:
        return (
            f"repo '{repo}' is owned by GitHub id {actual}, but verified author '{author}' is "
            f"GitHub id {expected} — publish from an account that id controls"
        )
    return None


# Compare statuses meaning "head's history contains base". The call is always
# `compare/{ref}...{candidate}`, so `ahead` is "the candidate is ref plus later commits" and
# `identical` is "the candidate IS ref". `behind` and `diverged` both mean the candidate does not
# contain it — measured both ways against a real pack repo before this was written, because a
# reversed reading would accept exactly the commits this check exists to refuse.
CONTAINS = ("ahead", "identical")

# GitHub calls one (repo, ref) may spend AFTER the repository lookup and the default-branch
# compare, which answer every ordinary submission. The search past them runs a compare per
# branch and per tag, so a submission naming a large third-party repository would otherwise spend
# the hourly API allowance this workflow shares with every other pull request. A pack repository
# has a handful of branches and tags; a commit this search cannot find inside the budget is
# refused with a message saying how to pin one it can.
SEARCH_BUDGET = 40
PAGE = 100


class SearchBudgetSpent(Exception):
    pass


def containing_ref(repo, ref, api):
    """The branch or tag of `repo` whose history contains commit `ref`, or None if none does.

    Returned as text for the log (`branch main`, `tag v1.2.0`). Raises when GitHub cannot answer.

    WHY `ref` BEING A REAL COMMIT IS NOT ENOUGH

    `repo_owner_error` proves who owns `repo`. It says nothing about `ref`, and GitHub resolves a
    commit through any repository of the fork network it lives in: a commit pushed only to a fork
    is returned by the PARENT's commits and compare API, and jsDelivr serves it under the parent's
    `gh/{repo}@{ref}` path (measured, with a public fork-only commit of octocat/Hello-World). So a
    pin naming the right repository, owned by the right id, can still carry bytes nobody with
    access to that repository wrote. What the repository itself vouches for is its branches and
    tags, so membership in one of those is the test — `refs/pull/*` and fork refs are neither.

    THE ORDER

      1. the default branch, by name — one compare, and where every live pin sits today;
      2. every branch and tag listed, matched against `ref` exactly — a pin to a tag or branch
         tip, the other common case, costs only the listing;
      3. a compare against each other branch, then each tag, stopping at the first that holds it.

    Listing everything before comparing anything is what makes step 2 cheap: matching tip-by-tip
    while comparing would spend the budget on page one's compares before reaching the tag on page
    three that IS the pin.

    404 "No common ancestor" is an answer, not a failure: an orphan branch (`gh-pages` built by a
    docs tool) shares no history with anything, and refusing over it would refuse every pin in a
    repository that publishes its docs that way. Every other error stops the search and raises —
    a rate limit or an outage is "not proven", never "not found", and the message has to say which.
    """
    spent = 0

    def call(path):
        nonlocal spent
        if spent >= SEARCH_BUDGET:
            raise SearchBudgetSpent()
        spent += 1
        return api(path)

    def contains(candidate, metered=True):
        path = f"{repo_api(repo)}/compare/{ref}...{candidate}?per_page=1"
        try:
            body = call(path) if metered else api(path)
        except GitHubError as exc:
            if exc.status == 404 and exc.message.startswith("No common ancestor"):
                return False
            raise
        return body.get("status") in CONTAINS

    default = api(repo_api(repo))["default_branch"]
    if contains(quote(default, safe=""), metered=False):
        return f"branch {default}"

    listed = {}
    for kind, listing in (("branch", "branches"), ("tag", "tags")):
        rows, page = [], 1
        while True:
            batch = call(f"{repo_api(repo)}/{listing}?per_page={PAGE}&page={page}")
            rows.extend(batch)
            if len(batch) < PAGE:
                break
            page += 1
        if kind == "branch":
            rows = [r for r in rows if r.get("name") != default]
        listed[kind] = rows
        for r in rows:
            if (r.get("commit") or {}).get("sha") == ref:
                return f"{kind} {r['name']}"

    for kind in ("branch", "tag"):
        for r in listed[kind]:
            if contains(r["commit"]["sha"]):
                return f"{kind} {r['name']}"
    return None


# The branch a base commit has to be on before the pins it holds count as settled. Present on a CI
# runner because the checkout fetches every branch (`fetch-depth: 0`); absent, nothing is settled.
PUBLISHED = "refs/remotes/origin/main"
COMMIT_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def settled_pins(base, root=ROOT, published=PUBLISHED):
    """Every (repo, ref) that `packs/` pinned at commit `base`, or None when `base` is not usable.

    WHY A PIN THAT IS ALREADY LISTED IS NOT ASKED AGAIN

    What `containing_ref` proves — somebody with access to `repo` put this commit on one of its
    branches or tags — is a fact about the moment the pin is approved, and the bytes behind a
    commit SHA cannot change afterwards. Asking again on every run proves nothing new, and it does
    harm: a pin stops being on any branch the day its repository's history is squashed or its
    release branch deleted — history rewrites in this org's pack repos have already orphaned older
    approved refs (pluginpack-telegram@304511c3 shares no history with that repo's main, measured
    2026-10-29). From then on EVERY pull request, and the `publish` job on main, would go red over
    an entry none of them touched, and the way out — a re-pin — would be a pull request that every
    other orphaned pin turns red in turn.

    So the check runs on what a change brings in. An entry whose (repo, ref) the base did not pin —
    a new entry, a re-pin, a move to another repository — is asked; one the base already pinned is
    not. Withdrawn entries follow the same rule instead of being skipped outright: withdrawing a
    pack in place keeps its pin, so it is not asked (its content is allowed to be gone, as in
    verify_pinned.py), but a withdrawn row that brings in a NEW ref is asked like any other,
    because build_approved.py puts that ref in the approved lists all the same and the client
    decides what to load from those lists.

    `base` is the commit the change is measured against: the pull request's base, or main's
    previous tip on a push. It only counts if `published` contains it. On a pull request
    `github.event.before` is the PR's own previous head, which its author wrote; requiring the
    commit to be on the published branch means only pins main already accepted can be exempt,
    whatever expression ends up feeding this. Anything unusable — empty, not a commit SHA (which
    also keeps an option-shaped value away from git), not fetched, not on main — returns None, and
    the caller then asks about every pin. The failure mode is spending API calls, never checking
    less.
    """
    if not base or not COMMIT_SHA.fullmatch(base):
        return None

    def git(*args):
        return subprocess.run(
            ["git", "-C", root, *args], capture_output=True, text=True, check=True
        ).stdout

    try:
        git("merge-base", "--is-ancestor", base, published)
        names = git("ls-tree", "--name-only", "-z", base, "--", "packs/").split("\0")
    except (OSError, subprocess.CalledProcessError):
        return None
    pins = set()
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            entry = json.loads(git("show", f"{base}:{name}"))
        except (subprocess.CalledProcessError, ValueError):
            continue  # unreadable at base: not settled, so it is asked — the strict direction
        if isinstance(entry, dict) and entry.get("repo") and entry.get("ref"):
            pins.add((entry["repo"], entry["ref"]))
    return pins


def ref_reachable_error(entry, home, settled=frozenset()):
    """How the entry's `ref` fails to belong to its `repo`, or None.

    `home(repo, ref)` is `containing_ref` bound to an API (and cached per pair in `main`). Applied
    to EVERY entry, verified or not: an unverified entry pointing at somebody else's repository
    borrows that repository's name exactly as much as a verified one does, and the analyst reads
    the repository on the card, not the badge, to decide whose code it is.

    `settled` is the (repo, ref) pairs the base already pinned (`settled_pins`); those were asked
    when they were approved and are not asked again. Withdrawn entries get no exemption of their
    own — see `settled_pins` for why.
    """
    repo, ref = entry.get("repo", ""), entry.get("ref", "")
    if (repo, ref) in settled:
        return None
    try:
        found = home(repo, ref)
    except SearchBudgetSpent:
        return (
            f"commit {ref} is not on the default branch of '{repo}', and no branch or tag holding it "
            f"was found within {SEARCH_BUDGET} GitHub API calls — pin a commit on the default "
            f"branch, or the commit a tag points at"
        )
    except Exception as exc:  # noqa: BLE001 — every failure means "not proven"
        return f"could not confirm that commit {ref} belongs to repo '{repo}': {exc}"
    if found is None:
        return (
            f"commit {ref} is not on any branch or tag of '{repo}' — a commit that exists only in "
            f"a fork or a pull request is refused; push it to '{repo}' (or tag it there) and pin that"
        )
    return None


def authorship_error(entry, owners):
    """How the entry misuses `author`, or None.

    `author` is the field that makes a pack look trustworthy, and it is plain submitter input. The
    check runs in both directions on purpose —

      - a namespace someone owns may only be published under by that someone, so a stranger
        cannot list under `run.vineyard.*`;
      - a name someone owns may only be worn inside their own namespaces, so a stranger cannot
        publish `com.evil.pluginpacks.x` as `author: vineyard-run`.

    One direction alone leaves the other half of the impersonation open.

    `verified` used to be checked here too — a third branch rejecting a self-asserted `true` from
    an unlisted author. It is gone because the field is no longer submitter input at all:
    `build_registry.load_packs` derives it from exactly the relation these two branches enforce,
    so the branch could never fire again. A check that cannot fail is worse than no check, because
    the next reader takes it as evidence something is still guarded.
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
    return None


DEPENDENCY_FIELDS = {
    # field -> what the install flow does with it, for the error message
    "requires": "a Skill Pack's steps call these Plugin Packs; install offers them alongside",
    "typepacks": "a Plugin Pack writes these types; install offers the Type Packs alongside",
}


def status_error(entry, by_id):
    """How the entry's `status` block is unusable, or None.

    Only `replacement` is checkable — `state`/`reason`/`since` are the schema's business. But a
    replacement that does not resolve is the delisting notice failing at the one job it has: the
    analyst is told to move to something, and the something is not there. Pointing at another
    delisted pack is the same failure one hop out.
    """
    status = entry.get("status")
    if not status:
        return None
    dest = status.get("replacement")
    if dest is None:
        return None
    if dest == entry.get("identifier"):
        return "`status.replacement` points at this entry itself."
    target = by_id.get(dest)
    if target is None:
        return (
            f"`status.replacement` names '{dest}', which is not in this catalog. Point at a live "
            f"pack, or drop the field — a dead pointer reads worse than no advice."
        )
    if target.get("status"):
        return (
            f"`status.replacement` names '{dest}', which is itself "
            f"{target['status'].get('state')}. Send users somewhere live."
        )
    return None


def dependency_error(entry, by_id):
    """A declared dependency that is not in this catalog, or is no longer live, or None.

    Both fields feed the same place — `marketplace/registry.ts` builds the co-install offer from
    them — so an identifier that resolves to nothing is not a cosmetic error: the analyst installs
    the pack, the dependency is silently not offered, and the pack runs against types or plugins
    that were never installed. Nothing checked this, in either field.

    Existence and liveness are the whole check. The KIND is already pinned by the entry schemas'
    patterns (`typepacks` items must match `…typepacks\\.…`, `requires` items `…plugins|pluginpacks…`)
    and identifiers are globally unique, so an id of the right shape can only have been registered by
    an entry of the right kind. Catalog-only, for the same reason as typeRefs: the install flow can
    offer nothing it cannot resolve.
    """
    for field, why in DEPENDENCY_FIELDS.items():
        for dep in entry.get(field) or []:
            target = by_id.get(dep)
            if target is None:
                return (
                    f"`{field}` lists '{dep}', which is not in this catalog — {why}, so it would "
                    f"install without one. Publish that pack first, or drop the dependency."
                )
            # A live pack may not depend on a delisted one. The co-install offer is built from these
            # fields, so leaving the edge in place means installing this pack hands the analyst a
            # pack the registry has already said not to use — and for `withdrawn`, one the client
            # refuses to load, which puts them straight back in the failure the field exists to
            # prevent. Delisting a dependency is therefore a two-step: fix the dependants first.
            if target.get("status") and not entry.get("status"):
                state = target["status"].get("state")
                return (
                    f"`{field}` lists '{dep}', which is {state} — {why}, so this pack would offer "
                    f"a delisted one alongside itself. Move to a live pack, or delist this one too."
                )
    return None


def main():
    bad = total = deps = delisted = 0
    owners = load("verified-authors.json").get("authors", {})
    grouped = load_packs()
    # Every entry in the catalog by identifier, gathered before validating, so a dependency (or a
    # `status.replacement`) may point at a pack listed in the same pull request rather than forcing
    # two round trips to publish a pair. Entries, not bare identifiers: resolving a reference now
    # also means asking whether the target is still live.
    by_id = {e["identifier"]: e for entries in grouped.values() for e in entries}
    # Cached per (repo, ref): several entries can pin the same commit of the same repository (one
    # Type Pack repo publishes several packs from one release), and the answer cannot differ.
    home = lru_cache(maxsize=None)(lambda repo, ref: containing_ref(repo, ref, github_api))
    # What the base already pinned is not asked again (see `settled_pins`). Said out loud when the
    # base cannot be used, because the run then quietly costs a full audit's worth of API calls.
    base = os.environ.get("REGISTRY_BASE_SHA", "").strip()
    settled = settled_pins(base) if base else None
    if settled is None:
        if base:
            print(f"::notice::base {base} is not a commit on {PUBLISHED}; asking about every pin")
        settled = frozenset()
    asked = 0
    for catalog, entries in sorted(grouped.items()):
        validator = Draft202012Validator(load(SCHEMA_FOR[catalog]))
        for entry in entries:
            total += 1
            deps += len(entry.get("requires") or []) + len(entry.get("typepacks") or [])
            delisted += 1 if entry.get("status") else 0
            ident = entry.get("identifier", "<no identifier>")
            errors = sorted(validator.iter_errors(entry), key=lambda e: list(e.path))
            if errors:
                bad += 1
                print(f"::error file=packs/{ident}.json::{ident}: {errors[0].message}")
                continue
            asked += (entry.get("repo", ""), entry.get("ref", "")) not in settled
            problem = (
                authorship_error(entry, owners)
                or repo_owner_error(entry, owners, repo_owner_id)
                or ref_reachable_error(entry, home, settled)
                or status_error(entry, by_id)
                or dependency_error(entry, by_id)
            )
            if problem:
                bad += 1
                print(f"::error file=packs/{ident}.json::{ident}: {problem}")
        print(f"{catalog}: {len(entries)} entr{'y' if len(entries)==1 else 'ies'} checked")
    if bad:
        print(f"\n{bad} invalid entr{'y' if bad==1 else 'ies'} of {total}")
        sys.exit(1)
    if asked == total:
        pins = "every pinned commit is on a branch or tag of its own repo"
    elif asked == 0:
        pins = f"no pin is new or moved since {base[:12]} (all {total} unchanged, not asked again)"
    else:
        pins = (
            f"{asked} pin{'' if asked == 1 else 's'} new or moved since {base[:12]}, each on a "
            f"branch or tag of its own repo ({total - asked} unchanged, not asked again)"
        )
    print(
        f"\nall {total} entries valid ({delisted} delisted): every verified badge is backed by "
        f"verified-authors.json and the repo owner's GitHub id, {pins}, and all {deps} declared "
        f"dependencies resolve inside the catalog to a pack that is still live"
    )


if __name__ == "__main__":
    main()
