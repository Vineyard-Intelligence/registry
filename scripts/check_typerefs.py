#!/usr/bin/env python3
"""Every type a plugin declares in `io` must be a type some published Type Pack really defines.

WHY THIS IS BLOCKING RATHER THAN ADVISORY

A broken typeRef does not fail loudly, it fails invisibly, and the app has already been bitten by
the symptom once. `RunPluginsDialog` builds the set of acceptable seed types from `io.consumes` and
matches it against node types:

    const consumeTypes = new Set(consumesOf(p).map(typeOf));
    return nodes.flatMap((n) => (consumeTypes.has(n.type) ? [String(n.id)] : []));

A type string nothing defines matches no node, ever. The plugin then falls out of the dialog's
`matched` list — installed, approved, and absent, with no error anywhere. (The comment in that file
records exactly this shape happening for a different reason: a pack was "silently unrunnable from
the moment it shipped" because its `consumes` list did not behave as its author expected.)

`produces` fails in the other direction: `type-visuals` falls back to a tinted initial letter when
no Type Pack defines the type, so collection "succeeds" and leaves unstyled, unlabelled nodes on
the canvas.

The third check is about the install plan. `marketplace/registry.ts` builds the co-install offer
from the ENTRY's `typepacks` list, not from the manifest's `io` — so a pack whose `io` uses a Type
Pack the entry forgot to declare gets installed without it, and lands straight in the failure
above.

CATALOG-ONLY, ON PURPOSE

The palette is built from Type Packs published in this catalog and nowhere else. A plugin may not
reference a Type Pack the registry does not carry: the install flow can only co-install what it can
resolve, so an outside reference is not "unverified", it is broken for every user. The cost is an
ordering rule — publish the Type Pack, then the plugin that uses it — which the error message says
out loud.

Run with: python scripts/check_typerefs.py
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_registry import load_packs  # noqa: E402

TYPEPACKS = "registry/community-typepacks.json"
PLUGINPACKS = "registry/community-pluginpacks.json"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "vineyard-registry-typerefs"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def cdn(entry):
    return f"https://cdn.jsdelivr.net/gh/{entry['repo']}@{entry['ref']}/{entry['path'].lstrip('/')}"


def qualified(ref):
    """A typeRef as the app stores it on `Node.type`: "<category>.<name>"."""
    return f"{ref.get('category')}.{ref.get('name')}"


# Property types whose values are not scalars. `nodeIdentityKey` in the app voids the WHOLE key
# when an identity field holds one of these (type-visuals.tsx), so naming one here does not make
# the type de-dup badly — it makes it never de-dup at all, silently, forever.
NON_SCALAR = {"array", "object", "json"}


def check_identity(ident, key, t, errors):
    """`identity_properties` must name real, scalar properties of this same type.

    THE REGISTRY VALIDATES NO TYPE PACK DOCUMENT TODAY — validate.py only maps the catalog ENTRY
    schemas, and typepack.schema.json is read once for an identifier regex. So a pack could ship
    an identity that quietly does nothing and nobody would hear about it. Both failures are silent
    in the app and neither raises:

      * a NAME THAT DOES NOT EXIST contributes a permanently empty segment, so every node of the
        type shares whatever the other fields say — or, if it is the only entry, they all collapse
        onto one key;
      * a NON-SCALAR name voids the key outright, and the type stops merging anything ever. A
        collector then adds a fresh duplicate on every run.

    Cheap to check here because build_palette already holds the document.
    """
    props = t.get("properties") or {}
    for name in t.get("identity_properties") or []:
        if name not in props:
            errors.append((ident, f"type '{key}': identity_properties names '{name}', which is not a property of it"))
        elif (props[name] or {}).get("type") in NON_SCALAR:
            errors.append((
                ident,
                f"type '{key}': identity_properties names '{name}', which is a "
                f"{(props[name] or {}).get('type')} — a non-scalar voids the whole key and the type "
                f"would never de-dup",
            ))


# The `normalize` rules the app implements (identity-normalize.ts in the frontend). The schema enum
# says the same, but nothing here validates a Type Pack document against the schema (see
# check_identity), so a typo would otherwise ship.
NORMALIZE_RULES = {"crypto_address", "hex"}


def check_normalize(ident, key, t, errors):
    """A property's `normalize` must be a rule the app has, and must not be combined with
    `case_insensitive: true`.

    Both failures are silent in the app. An UNKNOWN rule is compared exactly — the app treats it as
    a rule from a newer schema it cannot apply — so a misspelt `crypto_adress` quietly turns every
    checksummed-versus-lower-case pair back into two nodes. A rule ALONGSIDE `case_insensitive`
    loses the flag (the rule wins), which is the outcome the author asked for only by accident;
    folding every value as well would merge Base58 addresses that differ by case alone.
    """
    for name, prop in (t.get("properties") or {}).items():
        prop = prop or {}
        if "normalize" not in prop:
            continue
        rule = prop.get("normalize")
        if rule not in NORMALIZE_RULES:
            errors.append((
                ident,
                f"type '{key}': property '{name}' has normalize {rule!r}, which is not one of "
                f"{', '.join(sorted(NORMALIZE_RULES))} — the app would compare it exactly",
            ))
        elif prop.get("case_insensitive") is True:
            errors.append((
                ident,
                f"type '{key}': property '{name}' sets both normalize and case_insensitive — "
                f"normalize decides per value and replaces the flag; drop case_insensitive",
            ))
        elif prop.get("type") in NON_SCALAR:
            errors.append((
                ident,
                f"type '{key}': property '{name}' is a {prop.get('type')}, and normalize applies to "
                f"a scalar value only",
            ))


def build_palette(entries, errors):
    """"category.name" -> the Type Pack identifier that defines it, across the whole catalog."""
    palette = {}
    for entry in entries:
        ident = entry["identifier"]
        try:
            doc = fetch(cdn(entry))
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as e:
            errors.append((ident, f"cannot fetch Type Pack document: {e}"))
            continue
        for t in doc.get("types") or []:
            key = f"{t.get('category')}.{t.get('name')}"
            check_identity(ident, key, t, errors)
            check_normalize(ident, key, t, errors)
            if key in palette and palette[key] != ident:
                # Two packs defining one type makes `Node.type` ambiguous — the app resolves by
                # the qualified string alone, so whichever is activated last silently wins.
                errors.append((ident, f"type '{key}' is already defined by {palette[key]}"))
            palette[key] = ident
    return palette


def check_member(member, entry, palette, errors):
    declared = set(entry.get("typepacks") or [])
    ident = member.get("identifier", "<no id>")
    io = member.get("io") or {}
    checked = 0
    for direction in ("consumes", "produces"):
        for ref in io.get(direction) or []:
            checked += 1
            key = qualified(ref)
            owner = palette.get(key)
            if owner is None:
                errors.append((
                    entry["identifier"],
                    f"{ident} {direction} '{key}', which no published Type Pack defines. "
                    f"Publish the Type Pack first, then list the plugin.",
                ))
                continue
            claimed = ref.get("typepack")
            if claimed and claimed != owner:
                errors.append((
                    entry["identifier"],
                    f"{ident} {direction} '{key}' as typepack={claimed}, but {owner} defines it",
                ))
                continue
            if owner not in declared:
                errors.append((
                    entry["identifier"],
                    f"{ident} {direction} '{key}' from {owner}, which is missing from this "
                    f"entry's `typepacks` — the install flow offers co-installs from that list, "
                    f"so the pack would install without the Type Pack it writes.",
                ))
    return checked


def live(entries):
    """Entries a client would still load. A WITHDRAWN pack is excluded from both sides of this
    check: its content is not required to exist any more (so fetching it would fail CI), and it
    defines nothing a live plugin is allowed to reference — `validate.py` blocks that edge first,
    with an error that names the delisting rather than an unresolved type."""
    return [e for e in entries if (e.get("status") or {}).get("state") != "withdrawn"]


def main():
    grouped = load_packs()
    errors = []
    typepack_entries = live(grouped.get(TYPEPACKS, []))
    palette = build_palette(typepack_entries, errors)

    refs = 0
    plugins = 0
    for entry in live(grouped.get(PLUGINPACKS, [])):
        try:
            doc = fetch(cdn(entry))
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as e:
            errors.append((entry["identifier"], f"cannot fetch manifest: {e}"))
            continue
        for member in doc.get("plugins") or [doc]:
            plugins += 1
            refs += check_member(member, entry, palette, errors)

    print(f"palette: {len(palette)} types from {len(typepack_entries)} Type Packs")
    print(f"checked: {refs} typeRefs across {plugins} plugins")

    # A pass with nothing checked looks identical to a clean pass. If the catalog stops yielding
    # types or plugins, that is a broken check reporting success, not a healthy registry.
    if not palette:
        print("::error::no types found — the Type Pack catalog is empty or unreadable")
        sys.exit(1)
    if not refs:
        print("::error::no typeRefs found — this check inspected nothing and cannot pass")
        sys.exit(1)

    for ident, message in errors:
        print(f"::error file=packs/{ident}.json::{ident}: {message}")
    if errors:
        print(f"\n{len(errors)} unresolved typeRef(s)")
        sys.exit(1)
    print("\nevery typeRef resolves to a published Type Pack the entry declares")


if __name__ == "__main__":
    main()
