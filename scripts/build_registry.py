#!/usr/bin/env python3
"""Generate docs/data/registry.json for the static community browser.

The community page (docs/community.md + javascripts/community.js) is fully static and
fetches one JSON file. This script builds that file deterministically from the canonical
marketplace sources so the catalog never drifts by hand:

  - marketplace/examples/registry/community-plugins.json     (which plugin entries exist)
  - marketplace/examples/registry/community-typepacks.json   (which typepack entries exist)
  - marketplace/examples/plugins/*.manifest.json             (bundled plugins, io, scopes)
  - frontend/public/typepacks/*.json                         (type palette for detail view)

Run:  python3 scripts/build_registry.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)                       # marketplace/site
MARKET = os.path.dirname(SITE)                     # marketplace
VINEYARD = os.path.dirname(MARKET)                 # repo root
OUT = os.path.join(SITE, "docs", "data", "registry.json")


def load(*parts):
    path = os.path.join(*parts)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def first_existing(candidates):
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def scopes_summary_from_manifest(manifest):
    sc = manifest.get("scopes", {}) or {}
    graph = sc.get("graph", []) or []
    network = sc.get("network", []) or []
    publish = sc.get("publish", []) or []
    config = sc.get("config", []) or []
    graph_write = any(
        v.split(":")[-1] in ("create", "update", "delete") for v in graph
    )
    secret_config = any(
        isinstance(c, dict) and c.get("secret") for c in config
    )
    return {
        "network": bool(network),
        "graph_write": graph_write,
        "publish": bool(publish),
        "secret_config": secret_config,
    }


def manifest_index():
    """identifier -> manifest, including each plugin inside a pack."""
    idx = {}
    plugins_dir = os.path.join(MARKET, "examples", "plugins")
    if not os.path.isdir(plugins_dir):
        return idx
    for fn in sorted(os.listdir(plugins_dir)):
        if not fn.endswith(".json"):
            continue
        doc = load(plugins_dir, fn)
        if not isinstance(doc, dict):
            continue
        idx[doc.get("identifier")] = doc
        for sub in doc.get("plugins", []) or []:
            idx[sub.get("identifier")] = sub
    return idx


def typepack_doc(path_hint, identifier):
    """Find the full typepack document (for the type palette)."""
    name = identifier.split(".")[-1]  # e.g. infrastructure / threat
    candidates = [
        os.path.join(VINEYARD, "frontend", "public", "typepacks", name + ".json"),
        os.path.join(MARKET, "examples", "typepacks", name + ".json"),
    ]
    path = first_existing(candidates)
    return load(path) if path else None


def build_plugin_entry(entry, manifests):
    ident = entry.get("identifier")
    manifest = manifests.get(ident, {})
    out = {
        "type": "pluginpack",
        "identifier": ident,
        "name": entry.get("name") or manifest.get("name"),
        "author": entry.get("author") or (manifest.get("author") or {}).get("name") or "—",
        "description": entry.get("description") or manifest.get("description") or "",
        "icon": manifest.get("icon") or "package",
        "version": entry.get("version") or manifest.get("version"),
        "repo": entry.get("repo"),
        "ref": entry.get("ref"),
        "license": manifest.get("license"),
        "platforms": entry.get("platforms")
        or (["web"] if (manifest.get("platforms") or {}).get("web") else []),
        "scopes_summary": entry.get("scopes_summary")
        or (scopes_summary_from_manifest(manifest) if manifest else {}),
        "plugin_count": entry.get("plugin_count")
        or len(manifest.get("plugins", []) or []) or 1,
        "verified": bool(entry.get("verified")),
    }
    # single-plugin detail
    if manifest and not manifest.get("plugins"):
        out["scopes"] = manifest.get("scopes")
        out["io"] = manifest.get("io")
        out["lifecycle"] = manifest.get("lifecycle")
    # pack -> bundled plugins
    bundled = manifest.get("plugins", []) or []
    if bundled:
        out["plugins"] = [
            {
                "identifier": p.get("identifier"),
                "name": p.get("name"),
                "description": p.get("description"),
                "icon": p.get("icon") or "box",
                "scopes": p.get("scopes"),
                "lifecycle": p.get("lifecycle"),
            }
            for p in bundled
        ]
    return out


def build_typepack_entry(entry):
    ident = entry.get("identifier")
    doc = typepack_doc(entry.get("path"), ident) or {}
    types = []
    for t in doc.get("types", []) or []:
        props = t.get("properties", {}) or {}
        types.append(
            {
                "category": t.get("category"),
                "name": t.get("name"),
                "label": (t.get("name") or "").replace("_", " ").title(),
                "color": t.get("color"),
                "icon": t.get("icon"),
                "description": t.get("description"),
                "property_count": len(props),
            }
        )
    edge_types = [
        {"name": e.get("name"), "label": e.get("label") or e.get("name")}
        for e in doc.get("edge_types", []) or []
    ]
    return {
        "type": "typepack",
        "identifier": ident,
        "name": entry.get("name") or doc.get("name"),
        "author": entry.get("author") or "—",
        "description": entry.get("description") or doc.get("description") or "",
        "icon": "layers",
        "version": entry.get("version") or doc.get("version"),
        "repo": entry.get("repo"),
        "ref": entry.get("ref"),
        "categories": entry.get("categories") or [],
        "type_count": entry.get("type_count")
        if entry.get("type_count") is not None
        else len(types),
        "edge_count": entry.get("edge_count")
        if entry.get("edge_count") is not None
        else len(edge_types),
        "verified": bool(entry.get("verified")),
        "types": types,
        "edge_types": edge_types,
    }


def main():
    manifests = manifest_index()

    plugin_entries = load(MARKET, "examples", "registry", "community-plugins.json") or []
    typepack_entries = load(MARKET, "examples", "registry", "community-typepacks.json") or []

    catalog = []
    seen = set()

    for e in plugin_entries:
        catalog.append(build_plugin_entry(e, manifests))
        seen.add(e.get("identifier"))

    # Surface standalone example plugin manifests not already in the catalog
    # (e.g. cidr_expand) so the documented examples are browsable too.
    for ident, manifest in manifests.items():
        if ident in seen:
            continue
        if manifest.get("content_type") != "vineyard:plugin":
            continue
        if manifest.get("plugins"):
            continue  # a pack's parent identifier; sub-plugins handled via pack
        # skip the bundled sub-plugins of an already-listed pack
        if any(ident == sub.get("identifier")
               for m in manifests.values()
               for sub in (m.get("plugins") or [])):
            continue
        synthetic = {
            "identifier": ident,
            "name": manifest.get("name"),
            "author": (manifest.get("author") or {}).get("name"),
            "description": manifest.get("description"),
            "version": manifest.get("version"),
            "repo": (manifest.get("distribution") or {}).get("repository"),
            "ref": (manifest.get("distribution") or {}).get("ref"),
            "platforms": ["web"] if (manifest.get("platforms") or {}).get("web") else [],
            "verified": True,
        }
        catalog.append(build_plugin_entry(synthetic, manifests))
        seen.add(ident)

    for e in typepack_entries:
        catalog.append(build_typepack_entry(e))

    catalog.sort(key=lambda x: (x["type"], (x.get("name") or "").lower()))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(catalog, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    n_plugin = sum(1 for x in catalog if x["type"] == "pluginpack")
    n_bundled = sum(len(x.get("plugins", [])) for x in catalog if x["type"] == "pluginpack")
    n_tp = sum(1 for x in catalog if x["type"] == "typepack")
    print("Wrote %s" % os.path.relpath(OUT, SITE))
    print("  plugin pack entries: %d (%d bundled)" % (n_plugin, n_bundled))
    print("  type packs:          %d" % n_tp)
    for x in catalog:
        extra = (
            "%d bundled" % len(x["plugins"])
            if x.get("plugins")
            else ("%d types" % len(x.get("types", [])) if x["type"] == "typepack" else "single")
        )
        print("   - [%s] %s (%s)" % (x["type"], x["name"], extra))
    return 0


if __name__ == "__main__":
    sys.exit(main())
