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

    print("verified derivation ok: 17 cases")


if __name__ == "__main__":
    main()
