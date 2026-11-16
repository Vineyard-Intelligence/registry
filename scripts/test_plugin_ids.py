#!/usr/bin/env python3
"""Self-test for verify_pinned.identifier_problem() — the identifiers a pack's plugins run under.

The app stages a plugin's writes under its identifier and tells its own writers (the AI agent, a
merge, a re-verification, the value menu's New node) from a plugin by that id alone, so a member
named `run.vineyard.agent` would be handed the agent's bridge, and one named `run.vineyard.value`
would have its links recorded as the analyst's. Every published member is `run.vineyard.plugins.*`,
so verifying the live catalog never takes the refusing branch; these cases are the only place it is
proven, and the first one proves main() refuses such a pack rather than only that the function exists.

Offline: no network, no catalog. Run with: python scripts/test_plugin_ids.py
"""
import contextlib
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify_pinned  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WRITERS = ("run.vineyard.agent", "run.vineyard.merge", "run.vineyard.retest", "run.vineyard.value")
PACK_ID = "run.vineyard.pluginpacks.example"


def member(ident="run.vineyard.plugins.example"):
    return {"identifier": ident, "content_type": "vineyard:plugin", "version": "1.0.0", "scopes": {}}


def pack(*members, ident=PACK_ID):
    return {
        "identifier": ident,
        "content_type": "vineyard:pluginpack",
        "version": "1.0.0",
        "summary": "Looks things up.",
        "description": "Looks things up, at length.",
        "plugins": list(members) or [member()],
    }


def run_main(doc):
    """verify_pinned.main() over a one-entry catalog whose pin serves `doc`: (exit code, output)."""
    entry = {
        "identifier": doc["identifier"],
        "content_type": doc["content_type"],
        "version": doc["version"],
        "repo": "Vineyard-Intelligence/pluginpack-example",
        "ref": "0" * 40,
        "path": "manifest.json",
    }
    saved = {k: getattr(verify_pinned, k) for k in ("CATALOGS", "load", "fetch", "fetch_text")}

    def no_fetch_text(url, *a, **k):
        raise AssertionError(f"fetched {url}: the example pack names no module")

    verify_pinned.CATALOGS = ["registry/community-pluginpacks.json"]
    verify_pinned.load = lambda rel: [entry]
    verify_pinned.fetch = lambda url: json.loads(json.dumps(doc))
    verify_pinned.fetch_text = no_fetch_text
    out = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out):
            verify_pinned.main()
    except SystemExit as e:
        code = e.code
    finally:
        for k, v in saved.items():
            setattr(verify_pinned, k, v)
    return code, out.getvalue()


def schema_pattern(rel, *path):
    node = json.load(open(os.path.join(ROOT, "schemas", rel), encoding="utf-8"))
    for key in path:
        node = node[key]
    return node["pattern"]


def main():
    cases = 0

    # --- main() refuses the pack, and passes the same pack with its member renamed -------------------
    code, out = run_main(pack(member()))
    assert code == 0 and "ok  run.vineyard.pluginpacks.example" in out, f"a well-named pack must verify:\n{out}"
    cases += 1
    for writer in WRITERS:
        code, out = run_main(pack(member(), member(writer)))
        assert code == 1, f"main() let a pack with a member named {writer} through:\n{out}"
        assert f'::error file=packs/{PACK_ID}.json::{PACK_ID}: member "{writer}"' in out, out
        assert "re-pin `ref`" in out, out
        cases += 1
    code, out = run_main(pack(ident="run.vineyard.plugins.example"))
    assert code == 1 and "is not a plugin pack identifier" in out, (
        f"main() listed a pack under a plugin's identifier, which the entry pattern admits:\n{out}"
    )
    cases += 1

    problem = verify_pinned.identifier_problem
    PACK = "vineyard:pluginpack"

    # --- what passes ---------------------------------------------------------------------------------
    for ok in (
        pack(member()),
        pack(member("com.acme.plugins.thing"), member("io.github.someone.plugins.a_b"), ident="com.acme.pluginpacks.x"),
    ):
        assert problem(ok, PACK) is None, ok
        cases += 1
    lone = {**member(), "content_type": "vineyard:plugin"}
    assert problem(lone, "vineyard:plugin") is None, "a lone plugin document is held to the plugin pattern"
    # Type and skill packs name no plugin; their own identifiers are the entry schema's business.
    for kind in ("vineyard:typepack", "vineyard:skillpack"):
        assert problem({"identifier": "run.vineyard.agent"}, kind) is None, kind
    cases += 3

    # --- the writers' ids, as a member, as the pack and as a lone plugin ------------------------------
    for writer in WRITERS:
        why = problem(pack(member(), member(writer)), PACK)
        assert why and why.startswith(f'member "{writer}" is the identifier one of the app\'s own writers'), why
        assert problem(pack(ident=writer), PACK).startswith(f'"{writer}" is the identifier'), writer
        assert "own writers" in problem({**lone, "identifier": writer}, "vineyard:plugin"), writer
        cases += 3
    # Refused by name even if a pattern were loosened to admit them: the shape alone is not the rule.
    saved = verify_pinned.MEMBER_ID
    verify_pinned.MEMBER_ID = re.compile(r".+")
    try:
        for writer in WRITERS:
            assert "own writers" in (problem(pack(member(writer)), PACK) or ""), f"{writer} admitted by a loose pattern"
            cases += 1
        assert problem(pack(member("anything")), PACK) is None, "the stub pattern must be what is consulted"
    finally:
        verify_pinned.MEMBER_ID = saved

    # --- shape: a member's id is a plugin's, a pack's a pack's ----------------------------------------
    for bad in (
        "run.vineyard.pluginpacks.example",  # a pack's kind segment on a member
        "run.vineyard.typepacks.example",
        "vineyard.plugins.example",  # one namespace label
        "Run.Vineyard.plugins.example",
        "run.vineyard.plugins.example\n",  # Python's `$` alone would admit this
        "run.vineyard.plugins.",
        "",
        None,
        1,
        ["run.vineyard.plugins.example"],
    ):
        why = problem(pack(member(), member(bad)), PACK)
        assert why and why.startswith("member ") and "is not a plugin identifier" in why, (bad, why)
        cases += 1
    assert "is not a plugin identifier" in problem(pack(member(), "run.vineyard.plugins.example"), PACK), (
        "a member that is not an object has no identifier"
    )
    no_id = member()
    del no_id["identifier"]
    assert "null is not a plugin identifier" in problem(pack(no_id), PACK)
    for bad in ("run.vineyard.plugins.example", "run.vineyard.example", None):
        assert "is not a plugin pack identifier" in problem(pack(ident=bad), PACK), bad
    assert "is not a plugin identifier" in problem({**lone, "identifier": PACK_ID}, "vineyard:plugin")
    assert "must be a list" in problem({**pack(), "plugins": {"identifier": "run.vineyard.agent"}}, PACK)
    cases += 7

    # --- the patterns are the schemas' own ------------------------------------------------------------
    assert verify_pinned.PLUGIN_ID.pattern == schema_pattern("plugin.schema.json", "properties", "identifier")
    assert verify_pinned.MEMBER_ID.pattern == schema_pattern(
        "pluginpack.schema.json", "$defs", "member", "properties", "identifier"
    )
    assert verify_pinned.PLUGINPACK_ID.pattern == schema_pattern("pluginpack.schema.json", "properties", "identifier")
    cases += 3

    print(f"identifier_problem: no plugin is named outside its kind or as one of the app's writers — ok: {cases} cases")


if __name__ == "__main__":
    main()
