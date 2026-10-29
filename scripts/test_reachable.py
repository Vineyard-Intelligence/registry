#!/usr/bin/env python3
"""Self-test for the "pinned commit belongs to its repo" check in validate.py.

Every live pin sits on its repository's default branch, so validating the real catalog runs one
compare per pin and takes the accepting branch every time. Swap `CONTAINS` for its complement, or
let the search return the first branch it looks at, and the live run would go red — but delete the
branch and tag search, treat an API error as "fine", or forget that an orphan branch is not a
failure, and CI stays green. Those are the paths that matter, and none of them exist in the live
data, so they are constructed here against a stubbed GitHub.

The stub is a small commit graph, not a table of canned answers: compare is computed from
ancestry the way GitHub computes it, so a test cannot pass by the stub agreeing with whatever the
code happens to ask. Like GitHub, it resolves a commit that only a fork holds — the shared object
store is the whole reason this check exists — and only branches and tags decide.

Two more things the live run cannot show. Which pins are NOT asked (`settled_pins`): on a pull
request almost none are, so an exemption that grew too wide — a base the PR's author wrote, a
withdrawn row bringing in a new ref — would pass every live run; it is tested against a throwaway
git repository. And that `main()` asks at all: delete the call from it and every other case here
still passes, so one case runs `main()` end to end over a stubbed catalog.

Run with: python scripts/test_reachable.py
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import validate  # noqa: E402
from validate import (  # noqa: E402
    PAGE,
    SEARCH_BUDGET,
    GitHubError,
    containing_ref,
    ref_reachable_error,
    settled_pins,
)

REPO = "Vineyard-Intelligence/pluginpack-example"


def sha(label):
    """A 40-hex commit id for a readable label, so failures name `fork1` rather than a hash."""
    return (label.encode().hex() + "0" * 40)[:40]


class FakeGitHub:
    """Enough of the REST API for `containing_ref`: repo, compare, branches, tags.

    `parents` holds every commit in the fork NETWORK — including ones no branch or tag of REPO
    reaches — because that is what GitHub resolves through the parent's API. `fail` maps a path
    fragment to the GitHubError a request containing it raises, for the outage cases.
    """

    def __init__(self, parents, branches, tags=(), default="main", fail=None):
        self.parents = parents
        self.branches = dict(branches)
        self.tags = dict(tags)
        self.default = default
        self.fail = fail or {}
        self.calls = []

    def ancestors(self, commit):
        seen, stack = set(), [commit]
        while stack:
            c = stack.pop()
            if c in seen:
                continue
            seen.add(c)
            stack.extend(self.parents.get(c, ()))
        return seen

    def resolve(self, name):
        if name in self.parents:
            return name
        if name in self.branches:
            return self.branches[name]
        if name in self.tags:
            return self.tags[name]
        raise GitHubError(404, "Not Found")

    def __call__(self, path):
        self.calls.append(path)
        for fragment, error in self.fail.items():
            if fragment in path:
                raise error
        prefix = f"/repos/{REPO}"
        assert path.startswith(prefix), path
        rest = path[len(prefix):]
        if rest == "":
            return {"default_branch": self.default, "owner": {"id": 297755403}}
        route, _, query = rest.partition("?")
        params = dict(kv.split("=") for kv in query.split("&")) if query else {}
        if route.startswith("/compare/"):
            base_name, head_name = route[len("/compare/"):].split("...")
            base, head = self.resolve(base_name), self.resolve(head_name)
            if base == head:
                return {"status": "identical"}
            up_head, up_base = self.ancestors(head), self.ancestors(base)
            if base in up_head:
                return {"status": "ahead"}
            if head in up_base:
                return {"status": "behind"}
            if up_head & up_base:
                return {"status": "diverged"}
            raise GitHubError(404, f"No common ancestor between {base_name} and {head_name}.")
        if route in ("/branches", "/tags"):
            refs = self.branches if route == "/branches" else self.tags
            rows = [{"name": n, "commit": {"sha": c}} for n, c in sorted(refs.items())]
            per, page = int(params["per_page"]), int(params["page"])
            return rows[(page - 1) * per: page * per]
        raise AssertionError(f"unexpected API path {path}")


# A small history, one shape for every case:
#
#   c1 - c2 - c3                 main
#          \
#           d1 - d2              dev (not merged)
#   c2 - r1                      tag v1.0.0-hotfix only (release commit, on no branch)
#   c3 - fork1                   a fork's commit; no branch or tag of REPO holds it
#   o1                           gh-pages (orphan: no common ancestor with anything)
PARENTS = {
    sha("c1"): (),
    sha("c2"): (sha("c1"),),
    sha("c3"): (sha("c2"),),
    sha("d1"): (sha("c2"),),
    sha("d2"): (sha("d1"),),
    sha("r1"): (sha("c2"),),
    sha("fork1"): (sha("c3"),),
    sha("o1"): (),
}
BRANCHES = {"main": sha("c3"), "dev": sha("d2"), "gh-pages": sha("o1")}
TAGS = {"v1.0.0": sha("c2"), "v1.0.0-hotfix": sha("r1")}


def gh(**kw):
    return FakeGitHub(PARENTS, kw.pop("branches", BRANCHES), kw.pop("tags", TAGS), **kw)


def entry(ref, **extra):
    return {"identifier": "run.vineyard.pluginpacks.example", "repo": REPO, "ref": ref, **extra}


def error_for(ref, api, settled=frozenset(), **extra):
    return ref_reachable_error(
        entry(ref, **extra), lambda repo, r: containing_ref(repo, r, api), settled
    )


def no_api(path):
    raise AssertionError(f"a settled pin must not reach the API: {path}")


# A registry repository on disk, for `settled_pins`, which reads the base through git:
#
#   A ---- B            main   (B re-pins `a` to ref s3)
#    \
#     C                 side   (a commit main does not contain, as a PR's own head would be)
def registry_repo(root):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}

    def git(*args):
        return subprocess.run(
            ["git", "-C", root, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
             "-c", "commit.gpgsign=false", *args],
            capture_output=True, text=True, check=True, env=env,
        ).stdout.strip()

    def commit(packs, message):
        for name, body in packs.items():
            with open(os.path.join(root, "packs", name), "w", encoding="utf-8") as fh:
                fh.write(body if isinstance(body, str) else json.dumps(body))
        git("add", "-A")
        git("commit", "-q", "-m", message)
        return git("rev-parse", "HEAD")

    git("init", "-q")
    git("symbolic-ref", "HEAD", "refs/heads/main")
    os.makedirs(os.path.join(root, "packs"))
    a = commit({
        "a.json": {"repo": REPO, "ref": sha("s1")},
        "b.json": {"repo": "acme/pack-b", "ref": sha("s2"), "status": {"state": "withdrawn"}},
        "broken.json": "{ not json",
        "README": "not an entry",
    }, "A")
    b = commit({"a.json": {"repo": REPO, "ref": sha("s3")}}, "B")
    git("checkout", "-q", "-b", "side", a)
    c = commit({"a.json": {"repo": REPO, "ref": sha("fork1")}}, "C")
    git("checkout", "-q", "main")
    return a, b, c


def run_main(listed, settled=None):
    """validate.main() over one listed entry against the stub: (exit code, printed text, stub).

    The real schemas and the real verified-authors.json, so the entry has to be a valid VINEYARD
    entry — which is the point: everything but the pin is right, and the pin decides.
    """
    api = gh()
    saved = (validate.load_packs, validate.github_api, validate.settled_pins)
    saved_base = os.environ.pop("REGISTRY_BASE_SHA", None)
    validate.load_packs = lambda: {"registry/community-pluginpacks.json": [listed]}
    validate.github_api = api
    if settled is not None:
        os.environ["REGISTRY_BASE_SHA"] = sha("base")
        validate.settled_pins = lambda base: settled
    out, code = io.StringIO(), 0
    try:
        with contextlib.redirect_stdout(out):
            validate.main()
    except SystemExit as exc:
        code = exc.code
    finally:
        validate.load_packs, validate.github_api, validate.settled_pins = saved
        os.environ.pop("REGISTRY_BASE_SHA", None)
        if saved_base is not None:
            os.environ["REGISTRY_BASE_SHA"] = saved_base
    return code, out.getvalue(), api


LISTED = {
    "identifier": "run.vineyard.pluginpacks.example",
    "content_type": "vineyard:pluginpack",
    "name": "Example",
    "author": "VINEYARD",
    "description": "A listed entry for the wiring case.",
    "repo": REPO,
    "path": "plugins/example.manifest.json",
    "version": "1.0.0",
    "verified": True,
}


CASES = 0


def case(ok, why):
    global CASES
    CASES += 1
    assert ok, why


def main():
    # --- accepted: the commit is on the default branch ----------------------------------------
    api = gh()
    case(containing_ref(REPO, sha("c1"), api) == "branch main", "an old commit of main")
    case(len(api.calls) == 2, f"the common case costs the repo lookup and ONE compare: {api.calls}")
    case(containing_ref(REPO, sha("c3"), gh()) == "branch main", "main's tip is identical, not ahead")
    case(error_for(sha("c2"), gh()) is None, "no error message for a commit main holds")

    # --- accepted: only another branch holds it -------------------------------------------------
    case(containing_ref(REPO, sha("d1"), gh()) == "branch dev", "a commit only on an unmerged branch")
    case(containing_ref(REPO, sha("d2"), gh()) == "branch dev", "a non-default branch tip")
    case(error_for(sha("d1"), gh()) is None, "branch-only commit is not refused")

    # --- accepted: only a tag holds it ----------------------------------------------------------
    api = gh()
    case(containing_ref(REPO, sha("r1"), api) == "tag v1.0.0-hotfix", "a release commit on no branch")
    case(not any("/compare/" in c and sha("d2") in c for c in api.calls),
         "a tag tip is matched from the listing, before any per-branch compare")
    # A tag that does not point AT the pin but whose history holds it: needs the compare.
    tagged_later = gh(branches={"main": sha("c1")}, tags={"v2": sha("r1")})
    case(containing_ref(REPO, sha("c2"), tagged_later) == "tag v2", "a commit under a tag, not at it")
    case(error_for(sha("r1"), gh()) is None, "tag-only commit is not refused")

    # --- refused: the commit exists only in a fork ----------------------------------------------
    api = gh()
    # The stub resolves it, as GitHub does through the parent's API — so this is refused by the
    # branch/tag test alone, not by a lookup that happened to 404.
    case(api.resolve(sha("fork1")) == sha("fork1"), "the fork commit is resolvable in the network")
    case(containing_ref(REPO, sha("fork1"), api) is None, "no branch or tag holds a fork-only commit")
    msg = error_for(sha("fork1"), gh())
    case(bool(msg) and REPO in msg and sha("fork1") in msg, f"refusal names repo and ref: {msg}")
    case("fork" in msg, f"refusal tells the submitter what went wrong: {msg}")
    # Refused for an unverified entry too: the check never reads `author` or `verified`.
    msg = error_for(sha("fork1"), gh(), author="mallory", verified=False)
    case(bool(msg), "an unverified entry naming someone else's repo is refused the same way")

    # --- an orphan branch is an answer, not an outage -------------------------------------------
    # gh-pages is compared before zz and shares no history: its compare 404s with "No common
    # ancestor" (measured on a real mkdocs gh-pages branch). Raising there would refuse every pin
    # in a repo that publishes its docs that way.
    only_after_orphan = gh(branches={"main": sha("c1"), "gh-pages": sha("o1"), "zz": sha("d2")})
    case(containing_ref(REPO, sha("d1"), only_after_orphan) == "branch zz",
         "search continues past an orphan branch")

    # --- refused: GitHub cannot answer ----------------------------------------------------------
    down = gh(fail={"/compare/": GitHubError(403, "API rate limit exceeded")})
    msg = error_for(sha("c1"), down)
    case(bool(msg) and "could not confirm" in msg and REPO in msg and sha("c1") in msg,
         f"a rate limit is 'not proven', and says so: {msg}")
    case(len(down.calls) == 2, "an API failure stops the search instead of burning more calls")
    gone = gh(fail={"": GitHubError(404, "Not Found")})
    case(bool(error_for(sha("c1"), gone)), "a repository the API cannot find is refused")
    listing_down = gh(fail={"/tags": GitHubError(502, "Bad Gateway")})
    case(bool(error_for(sha("fork1"), listing_down)), "a failed listing is refused, not skipped")
    # An unknown commit is a plain 404 on the first compare — refused, with GitHub's reason.
    msg = error_for("f" * 40, gh())
    case(bool(msg) and "404" in msg, f"an unknown commit is refused: {msg}")

    # --- pagination, and the budget that bounds it ----------------------------------------------
    # A full first page of listing, and the pin is the tip of a branch on page two. Matched from
    # the listing, so it costs the two pages and no per-branch compares at all.
    many = {f"b{i:03d}": sha("c1") for i in range(PAGE)}
    many["zzz"] = sha("d2")  # sorts last: page two
    api = gh(branches={"main": sha("c1"), **many})
    case(containing_ref(REPO, sha("d2"), api) == "branch zzz", "a branch tip on the second page")
    case(sum("/compare/" in c for c in api.calls) == 1,
         f"found by listing, after only the default-branch compare: {api.calls}")
    crowded = {f"b{i:03d}": sha("c1") for i in range(SEARCH_BUDGET + 5)}
    api = gh(branches={"main": sha("c1"), **crowded}, tags={})
    msg = error_for(sha("fork1"), api)
    case(bool(msg) and str(SEARCH_BUDGET) in msg, f"the search gives up, refused: {msg}")
    case(len(api.calls) <= SEARCH_BUDGET + 2, f"and never spends past the budget: {len(api.calls)}")

    # --- a pin the base already listed is not asked again ----------------------------------------
    # Asked when it was approved; asking again would turn every PR red the day its repository's
    # history is rewritten. `no_api` fails the case if the exemption spends even one call.
    settled = {(REPO, sha("fork1"))}
    case(error_for(sha("fork1"), no_api, settled) is None, "an unchanged pin is not asked again")
    msg = error_for(sha("fork1"), gh(), {(REPO, sha("c1"))})
    case(bool(msg), "a re-pin to a new ref is asked, whatever the old ref was")
    moved = ref_reachable_error(
        {**entry(sha("fork1")), "repo": "someone/else"}, lambda repo, r: None, settled
    )
    case(bool(moved), "the same ref under another repo is a new pin, keyed on the pair")

    # --- withdrawn entries: exempt only through the base, never by state -------------------------
    # Withdrawing in place keeps the pin, so the base exempts it — its content may be gone, as in
    # verify_pinned.py, and here even its repository is.
    msg = error_for(sha("fork1"), gone, settled, status={"state": "withdrawn"})
    case(msg is None, "a pack withdrawn in place is not held to a pin it may have lost")
    # A withdrawn row that brings in a NEW ref is asked: build_approved.py publishes its ref in the
    # approved lists like any other, and the client loads by those lists.
    msg = error_for(sha("fork1"), gh(), status={"state": "withdrawn"})
    case(bool(msg) and "not on any branch or tag" in msg,
         f"a withdrawn row cannot carry a fork-only commit into the approved lists: {msg}")
    msg = error_for(sha("fork1"), gh(), status={"state": "deprecated"})
    case(bool(msg), "a deprecated entry still loads for its users, so it is still checked")

    # --- which base may exempt anything ----------------------------------------------------------
    with tempfile.TemporaryDirectory() as root:
        a, b, c = registry_repo(root)

        def at(base):
            return settled_pins(base, root=root, published="refs/heads/main")

        case(at(a) == {(REPO, sha("s1")), ("acme/pack-b", sha("s2"))},
             f"every pin the base listed, withdrawn ones too, and nothing that is not one: {at(a)}")
        case(at(b) == {(REPO, sha("s3")), ("acme/pack-b", sha("s2"))}, "the base's own pins, not older ones")
        # The PR's own head is what `github.event.before` is on a pull_request. Its author wrote
        # it, so it must exempt nothing — not even the pins it shares with main.
        case(at(c) is None, "a base main does not contain exempts nothing")
        case(at(b[:12]) is None, "an abbreviated sha is not a base")
        case(at("main") is None, "a branch name is not a base")
        case(at("--output=/dev/null") is None, "an option-shaped value never reaches git")
        case(at("0" * 40) is None, "the all-zero `before` of a new branch is not a base")
        case(at("f" * 40) is None, "a commit the clone does not have is not a base")
        case(at("") is None, "no base, no exemption")
        case(settled_pins(a, root=root, published="refs/remotes/origin/main") is None,
             "a clone without the published branch exempts nothing")

    # --- main() asks, and the base is what spares it ------------------------------------------------
    code, out, _ = run_main({**LISTED, "ref": sha("fork1")})
    case(code == 1 and "not on any branch or tag" in out, f"main() refuses a fork-only pin: {out}")
    code, out, _ = run_main({**LISTED, "ref": sha("d1")})
    case(code == 0, f"main() accepts a branch-held pin: {out}")
    code, out, api = run_main({**LISTED, "ref": sha("fork1")}, settled={(REPO, sha("fork1"))})
    case(code == 0 and not any("/compare/" in c for c in api.calls),
         f"main() passes the base's pins through without asking: {out} {api.calls}")
    code, out, _ = run_main({**LISTED, "ref": sha("fork1")}, settled={(REPO, sha("c1"))})
    case(code == 1, f"main() still asks about a pin the base did not have: {out}")

    # --- the request is the one that was meant ---------------------------------------------------
    seen = []

    def recording(path):
        seen.append(path)
        raise GitHubError(404, "Not Found")

    containing = lambda repo, r: containing_ref(repo, r, recording)  # noqa: E731
    ref_reachable_error({"repo": "a/b?x=1#y", "ref": sha("c1")}, containing)
    case(seen == ["/repos/a/b%3Fx%3D1%23y"], f"submitter text cannot reshape the request: {seen}")

    print(f"ref reachability ok: {CASES} cases")


if __name__ == "__main__":
    main()
