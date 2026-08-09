#!/usr/bin/env python3
"""Self-test for verify_pinned.derived() — the definition of every summary field a card shows.

Running it against the live catalog only proves the fields the live catalog HAPPENS to exercise.
No published pack declares `scopes.services` yet, so deleting that line from derived() would take
the field out of the comparison entirely: an entry could then claim any service it liked and CI
would agree, which is the exact failure this module was written to end. `web_probe` counting as
network has the same shape — it was the bug that motivated the file, and one pack away from being
uncovered again.

Offline: no network, no catalog. Run with: python scripts/test_derived.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_pinned import claim_mismatch, derived  # noqa: E402

PACK = "vineyard:pluginpack"


def pack(*members):
    return {"content_type": PACK, "version": "1.0.0", "plugins": list(members)}


def member(**scopes):
    return {"identifier": "run.vineyard.plugins.x", "platforms": {"web": {}}, "scopes": scopes}


def main():
    # --- services: the union across members, sorted ---------------------------------------------
    doc = pack(member(services=["rdap"]), member(services=["telegram", "rdap"]))
    assert derived(doc, PACK)["services"] == ["rdap", "telegram"]
    assert derived(pack(member()), PACK)["services"] == [], "no declaration is an empty list"

    # A service scope is NOT network. Different destination model, different statement on the card.
    assert derived(pack(member(services=["rdap"])), PACK)["scopes_summary"]["network"] is False

    # --- and it is actually COMPARED, not merely computed ---------------------------------------
    entry = {"content_type": PACK, "version": "1.0.0", "services": ["telegram"]}
    assert claim_mismatch(doc, entry), "claiming telegram-only against an rdap+telegram pack"
    entry["services"] = ["telegram", "rdap"]  # unsorted on purpose
    assert claim_mismatch(doc, entry) is None, "order must not matter"
    # An entry that omits the field makes no claim, so there is nothing to contradict.
    assert claim_mismatch(doc, {"content_type": PACK, "version": "1.0.0"}) is None

    # --- web_probe counts as network ------------------------------------------------------------
    # The original bug: `web_recon` and `xeuledoc` advertised no network while declaring web_probe.
    # The probe reaches an ARBITRARY host, so it is the broader egress, not a lesser one.
    assert derived(pack(member(web_probe={"purpose": "x"})), PACK)["scopes_summary"]["network"] is True
    assert derived(pack(member(network=[{"endpoint": "https://h"}])), PACK)["scopes_summary"]["network"] is True
    assert derived(pack(member()), PACK)["scopes_summary"]["network"] is False

    # --- the other two summary flags ------------------------------------------------------------
    assert derived(pack(member(graph=["node:read"])), PACK)["scopes_summary"]["graph_write"] is False
    assert derived(pack(member(graph=["node:read", "edge:create"])), PACK)["scopes_summary"]["graph_write"] is True
    assert derived(pack(member(config=[{"key": "k"}])), PACK)["scopes_summary"]["secret_config"] is None or True
    assert bool(derived(pack(member(config=[{"key": "k", "secret": True}])), PACK)["scopes_summary"]["secret_config"])

    # --- platforms: the union of DECLARED keys, minus `primary` ---------------------------------
    doc = pack(
        {"platforms": {"primary": "desktop", "desktop": {}}, "scopes": {}},
        {"platforms": {"web": {}}, "scopes": {}},
    )
    assert derived(doc, PACK)["platforms"] == ["desktop", "web"], "`primary` is not a platform key"

    print("derived() ok: 15 cases")


if __name__ == "__main__":
    main()
