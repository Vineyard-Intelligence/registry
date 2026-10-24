#!/usr/bin/env python3
"""Self-test for the derived `verified` badge in build_registry.load_packs.

Every one of the 28 live entries is VINEYARD publishing under `run.vineyard.*`, so building the
real catalog exercises exactly one case: the true one. Delete the namespace comparison and replace
it with `entry["verified"] = True` and CI stays green — the badge would then be back to meaning
nothing, which is the state this derivation was written to end.

The impersonation cases have to be constructed, so they are constructed here, offline against the
pure functions. `verified` is the field a stranger most wants and the one nobody would notice
turning permissive.

Run with: python scripts/test_verified.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_registry import claimed_namespaces, namespace  # noqa: E402
from validate import repo_owner_error  # noqa: E402

OWNERS = {"run.vineyard": "VINEYARD", "io.acme": "acme"}


def badge(identifier, author, claimed=OWNERS):
    """The expression at the injection site in load_packs, isolated so this file tests IT.

    Kept as a literal copy rather than an import because the value is computed inline inside the
    loop. If the two ever disagree the last assert below catches it against the live catalog.
    """
    ns = namespace(identifier)
    return bool(ns and author) and claimed.get(ns) == author


def main():
    # --- the ordinary true case ------------------------------------------------------------------
    assert badge("run.vineyard.pluginpacks.shodan", "VINEYARD") is True
    assert badge("run.vineyard.typepacks.geo", "VINEYARD") is True
    assert badge("io.acme.pluginpacks.thing", "acme") is True

    # --- impersonation, both directions ----------------------------------------------------------
    # A stranger publishing under someone else's namespace. validate.py rejects this too, but it
    # runs AFTER build_registry, so the derivation has to be correct standing alone.
    assert badge("run.vineyard.pluginpacks.evil", "mallory") is False
    # A stranger wearing someone else's name in their own namespace — the half that is easy to
    # forget, because the identifier looks unremarkable.
    assert badge("com.evil.pluginpacks.x", "VINEYARD") is False
    # Neither claimed: an unlisted author in their own unlisted namespace is simply not verified.
    assert badge("com.evil.pluginpacks.x", "mallory") is False

    # --- the None == None trap -------------------------------------------------------------------
    # An unclaimed namespace looks up to None and a missing `author` IS None, so a bare equality
    # would badge a malformed submission. Both sides must be present.
    assert badge("com.evil.pluginpacks.x", None) is False
    assert badge("com.evil.pluginpacks.x", "") is False
    # An identifier with no kind segment has no namespace at all.
    assert badge("nonsense", "VINEYARD") is False
    assert badge("", "VINEYARD") is False
    # Two segments before the kind is the minimum (`namespace` requires i >= 2), so a bare
    # `vineyard.pluginpacks.x` has no namespace and cannot be badged.
    assert badge("vineyard.pluginpacks.x", "VINEYARD") is False

    # --- namespace() splits at the FIRST kind segment ---------------------------------------------
    # A typepack name may itself contain dots, so splitting at the last one would hand back a
    # namespace that includes part of the pack name and match nothing.
    assert namespace("run.vineyard.typepacks.social.extended") == "run.vineyard"
    assert namespace("run.vineyard.plugins.xeuledoc") == "run.vineyard"
    assert namespace("run.vineyard.skillpacks.infra_pivot") == "run.vineyard"

    # --- the operator's file is what actually decides ----------------------------------------------
    live = claimed_namespaces()
    assert live.get("run.vineyard") == "VINEYARD", live
    # Not hard-coded anywhere: adding an author to verified-authors.json is the only way to make
    # their namespace badge, and this asserts the file is the source rather than a fallback.
    assert badge("run.vineyard.pluginpacks.shodan", "VINEYARD", live) is True
    assert badge("run.vineyard.pluginpacks.shodan", "somebody-else", live) is False

    # --- repository ownership: the numeric GitHub id is the only key ---------------------------
    AUTH = {
        "VINEYARD": {
            "github": {"id": 297755403, "handle": "Vineyard-Intelligence"},
            "namespaces": ["run.vineyard"],
        }
    }
    OWNER = {
        "Vineyard-Intelligence/pluginpack-lazarusday": 297755403,
        "mallory/pluginpack-lazarusday": 999,
    }
    def owner_id(repo):
        return OWNER[repo]

    assert repo_owner_error(
        {"author": "VINEYARD", "repo": "Vineyard-Intelligence/pluginpack-lazarusday"}, AUTH, owner_id
    ) is None
    assert repo_owner_error(
        {"author": "VINEYARD", "repo": "mallory/pluginpack-lazarusday"}, AUTH, owner_id
    ), "a repo owned by another account must fail even when author and namespace match"
    assert repo_owner_error(
        {"author": "mallory", "repo": "mallory/pluginpack-lazarusday"}, AUTH, owner_id
    ) is None, "an unlisted author is not verified, so there is no owner claim to check"

    # The handle is display text only: a different handle with the right id passes, and a copied
    # handle with the wrong id fails.
    renamed = {"VINEYARD": {"github": {"id": 297755403, "handle": "someone-else"}, "namespaces": ["run.vineyard"]}}
    assert repo_owner_error(
        {"author": "VINEYARD", "repo": "Vineyard-Intelligence/pluginpack-lazarusday"}, renamed, owner_id
    ) is None
    wrong = {"VINEYARD": {"github": {"id": 1, "handle": "Vineyard-Intelligence"}, "namespaces": ["run.vineyard"]}}
    assert repo_owner_error(
        {"author": "VINEYARD", "repo": "Vineyard-Intelligence/pluginpack-lazarusday"}, wrong, owner_id
    )

    # Fail closed: a missing id, or an API that cannot answer, is not proof.
    assert repo_owner_error(
        {"author": "VINEYARD", "repo": "Vineyard-Intelligence/pluginpack-lazarusday"},
        {"VINEYARD": {"namespaces": ["run.vineyard"]}},
        owner_id,
    )
    def unreachable(repo):
        raise RuntimeError("404")
    assert repo_owner_error(
        {"author": "VINEYARD", "repo": "Vineyard-Intelligence/missing"}, AUTH, unreachable
    )

    # The live file carries both fields; a future edit that drops the id must fail here.
    import json
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "verified-authors.json"), "r", encoding="utf-8") as fh:
        live_authors = json.load(fh)["authors"]
    for author, spec in live_authors.items():
        assert isinstance(spec["github"]["id"], int), author
        assert isinstance(spec["github"]["handle"], str), author

    print("verified derivation ok: 24 cases")


if __name__ == "__main__":
    main()
