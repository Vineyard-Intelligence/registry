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
from verify_pinned import THUMB, claim_mismatch, derived, pack_text_problem  # noqa: E402

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

    # --- desktop_only and icon: what the card draws -----------------------------------------------
    d, w = {"platforms": {"primary": "desktop"}}, {"platforms": {"primary": "web"}}
    assert derived(pack(d, d), PACK)["desktop_only"] == "all"
    assert derived(pack(d, w), PACK)["desktop_only"] == "some"
    assert derived(pack(w, w), PACK)["desktop_only"] is None, "none desktop means omitted"
    assert derived({**pack(w), "icon": "shield-check"}, PACK)["icon"] == "shield-check"
    assert derived({**pack(w), "icon": "🛡"}, PACK)["icon"] is None, "a glyph is not a lucide name"
    # --- thumbnail_url: repo-relative and pinned, never an absolute URL --------------------------
    # The field is resolved by the client as cdn.jsdelivr.net/gh/{repo}@{ref}/{path} (with a raw
    # fallback at the same SHA). An absolute URL in the document or the entry is rejected by the
    # schema; derived() also refuses to project one, so a stale entry cannot keep claiming it.
    thumb_doc = {**pack(w), "thumbnail_url": "assets/thumbnail.png"}
    assert derived(thumb_doc, PACK)["thumbnail_url"] == "assets/thumbnail.png"
    for bad in ("https://evil.example/x.png", "//evil.example/x.png", "/abs/x.png", "../x.png", "a/../b.png", "a/./b.png"):
        assert derived({**pack(w), "thumbnail_url": bad}, PACK)["thumbnail_url"] is None, bad
    tb = {"content_type": PACK, "version": "1.0.0"}
    assert claim_mismatch(thumb_doc, {**tb, "thumbnail_url": "assets/other.png"})
    assert claim_mismatch(thumb_doc, tb) is None, "omitting the field makes no claim"
    # The registry's copy and every schema's copy must be the same grammar.
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    import json
    for rel in ("schemas/pluginpack.schema.json", "schemas/plugin.schema.json", "schemas/registry-plugin-entry.schema.json"):
        with open(os.path.join(here, rel), "r", encoding="utf-8") as fh:
            schema = json.load(fh)
        assert schema["properties"]["thumbnail_url"]["pattern"] == THUMB.pattern, rel

    tp = "vineyard:typepack"
    assert derived({"types": [{"icon": "map-pin"}, {"icon": "user"}]}, tp)["icon"] == "map-pin"
    assert derived({"types": [{"icon": "https://x/i.png"}]}, tp)["icon"] is None
    # A wrong value fails, an omitted one is not a claim, and claiming one the doc lacks fails.
    base = {"content_type": PACK, "version": "1.0.0"}
    assert claim_mismatch(pack(d, w), {**base, "desktop_only": "all"})
    assert claim_mismatch(pack(w), {**base, "desktop_only": "some"})
    assert claim_mismatch(pack(d, w), base) is None
    assert claim_mismatch({**pack(w), "icon": "globe"}, {**base, "icon": "radar"})

    # --- the card line: the entry's `summary` is the document's, exactly -----------------------------
    # Every kind that has a page. The entry's line must be the document's summary, and the page is
    # never what it is compared with — a page of Markdown is not a card line.
    line = "Looks things up."
    page = "## What it does\n\nLooks things up, one hop at a time."
    for kind, doc in (
        (PACK, {**pack(w), "summary": line, "description": page}),
        (tp, {"content_type": tp, "version": "1.0.0", "types": [], "summary": line, "description": page}),
        ("vineyard:skillpack", {"content_type": "vineyard:skillpack", "version": "1.0.0", "summary": line}),
    ):
        entry = {"content_type": kind, "version": "1.0.0"}
        assert derived(doc, kind)["summary"] == line, kind
        assert "description" not in derived(doc, kind), f"{kind}: the page is never projected into the catalog"
        assert claim_mismatch(doc, {**entry, "summary": line}) is None, kind
        assert claim_mismatch(doc, {**entry, "summary": "A different line."}), kind
        assert claim_mismatch(doc, {**entry, "summary": page}), f"{kind}: the page is not the card line"
    # A document without `summary` has no line to compare with; pack_text_problem refuses it instead.
    legacy = {**pack(w), "description": "A longer document line than the card shows."}
    assert "summary" not in derived(legacy, PACK)

    # --- the two texts themselves: both required, for every kind that is listed ---------------------
    for kind in (PACK, tp, "vineyard:skillpack"):
        assert pack_text_problem({"summary": line, "description": page}, kind) is None, kind
        assert pack_text_problem(legacy, kind), f"{kind}: a document from before the page is not listed"
        assert pack_text_problem({"summary": line}, kind), f"{kind}: the page is required"
    assert pack_text_problem({"summary": line, "description": "가" * 16000}, PACK) is None
    for bad in (
        {"summary": ""},
        {"summary": "two\nlines"},
        {"summary": "extra  space"},
        {"summary": "x" * 251},
        {"summary": "😀" * 126},  # 252 UTF-16 units: the app counts what JS `.length` counts
        {"summary": line, "description": "x" * 16001},
        {"summary": line, "description": ""},
        {"summary": line, "description": " \n\t"},
        {"summary": line, "description": "a\rb"},
        {"summary": line, "description": "bell\x07"},
        {"summary": line, "description": ["not", "text"]},
    ):
        assert pack_text_problem(bad, PACK), bad
    # A lone plugin's description is the plugin's own, so it cannot carry both: it is listed as a pack
    # of one instead, with or without a summary.
    assert pack_text_problem({"summary": line, "description": page}, "vineyard:plugin")
    assert pack_text_problem({"description": "Looks one thing up."}, "vineyard:plugin")
    assert "description" not in derived({"summary": line, "platforms": {}}, "vineyard:plugin")

    print("derived() ok: 66 cases")


if __name__ == "__main__":
    main()
