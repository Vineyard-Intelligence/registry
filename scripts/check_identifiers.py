#!/usr/bin/env python3
"""Prove the identifier patterns in schemas/ still reject what they are supposed to reject.

An identifier pattern is the one kind of rule that fails SILENTLY when it is wrong. Loosen it by
one character and every submission still validates — including one that squats a namespace or
drops the kind segment entirely — and nothing in CI notices, because every real entry keeps
passing. That is exactly what happened when these were opened up from the hardcoded
`run.vineyard.` prefix to any author's own reverse-DNS namespace.

So this reads the patterns straight out of the schema files (never a copy — a copy would drift)
and runs them against both directions: identifiers that MUST match, including every one currently
published, and the malformed shapes the pattern exists to refuse.

Run with: python scripts/check_identifiers.py
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def pattern_at(schema_rel, *path):
    node = json.load(open(os.path.join(ROOT, "schemas", schema_rel), encoding="utf-8"))
    for key in path:
        node = node[key]
    return node["pattern"]


# schema file, JSON path to the property, and the kind segment(s) it accepts.
#
# All but one accept exactly one kind. `registry-plugin-entry` accepts two on purpose: a catalog
# row may list a bundle (`pluginpacks.*`) or a lone plugin published by itself (`plugins.*`), and
# both land in community-pluginpacks.json. Everything published today is a bundle.
SUBJECTS = [
    ("plugin.schema.json", ("properties", "identifier"), ["plugins"]),
    ("pluginpack.schema.json", ("properties", "identifier"), ["pluginpacks"]),
    ("pluginpack.schema.json", ("$defs", "member", "properties", "identifier"), ["plugins"]),
    ("registry-plugin-entry.schema.json", ("properties", "identifier"), ["plugins", "pluginpacks"]),
    ("registry-skillpack-entry.schema.json", ("properties", "identifier"), ["skillpacks"]),
    ("registry-typepack-entry.schema.json", ("properties", "identifier"), ["typepacks"]),
    ("typepack.schema.json", ("properties", "identifier"), ["typepacks"]),
]

# `{kind}` is filled in per subject, so each pattern is tested against its OWN kind segment.
MUST_MATCH = [
    "run.vineyard.{kind}.telegram",  # first-party, the form every published pack uses
    "com.acme.{kind}.shodan",  # a third party's own namespace — the point of the change
    "io.github.someone.{kind}.thing",  # more than two namespace labels
    "org.some-org.{kind}.thing",  # hyphen inside a label
    "com.acme.{kind}.a_b_c",  # underscores in the name
]
MUST_NOT_MATCH = [
    "{kind}.telegram",  # no namespace at all — would let anyone take a bare name
    "vineyard.{kind}.telegram",  # one label is not a reverse-DNS namespace
    "run.vineyard.telegram",  # kind segment missing entirely
    "Run.Vineyard.{kind}.telegram",  # uppercase
    "run..vineyard.{kind}.telegram",  # empty label
    "-run.vineyard.{kind}.telegram",  # leading hyphen
    "run-.vineyard.{kind}.telegram",  # trailing hyphen
    "run.vineyard.{kind}.telegram ",  # trailing space (anchors must hold)
    "run.vineyard.{kind}.tele gram",  # space inside the name
    "run.vineyard.{kind}.",  # empty name
]
# The kinds are deliberately near-misses of each other, so a pattern that forgot to pin its own
# kind segment is caught here rather than by a mis-filed pack six months later.
OTHER_KINDS = ["plugins", "pluginpacks", "typepacks", "skillpacks"]


def published_identifiers():
    """Every identifier the registry actually serves or ships, grouped by kind segment.

    Sourced from the catalogs and from each pack document's member list, so a pattern that would
    reject something already published fails here loudly instead of on the next submission.
    """
    found = {k: set() for k in OTHER_KINDS}
    reg = os.path.join(ROOT, "registry")
    for name in sorted(os.listdir(reg)):
        if not name.endswith(".json"):
            continue
        for entry in json.load(open(os.path.join(reg, name), encoding="utf-8")):
            ident = entry.get("identifier", "")
            seg = ident.split(".")[-2] if ident.count(".") >= 2 else ""
            if seg in found:
                found[seg].add(ident)
    return found


def main():
    bad = 0
    checked = 0
    live = published_identifiers()

    for schema_rel, path, kinds in SUBJECTS:
        rx = re.compile(pattern_at(schema_rel, *path))
        label = f"{schema_rel}:{'.'.join(path)}"

        for kind in kinds:
            for sample in MUST_MATCH:
                s = sample.format(kind=kind)
                checked += 1
                if not rx.fullmatch(s):
                    print(f"  FAIL  {label} rejects a valid identifier: {s}")
                    bad += 1

            for sample in MUST_NOT_MATCH:
                s = sample.format(kind=kind)
                checked += 1
                if rx.fullmatch(s):
                    print(f"  FAIL  {label} ACCEPTS a malformed identifier: {s!r}")
                    bad += 1

            for ident in sorted(live.get(kind, ())):
                checked += 1
                if not rx.fullmatch(ident):
                    print(f"  FAIL  {label} rejects a PUBLISHED identifier: {ident}")
                    bad += 1

        for other in OTHER_KINDS:
            if other in kinds:
                continue
            s = f"run.vineyard.{other}.thing"
            checked += 1
            if rx.fullmatch(s):
                print(f"  FAIL  {label} pins {kinds} but accepts a '{other}' identifier: {s}")
                bad += 1

        print(f"  ok    {label} pins {'/'.join(kinds)}")

    print(f"\n{checked} identifier(s) checked against {len(SUBJECTS)} patterns")
    if bad:
        print(f"{bad} failure(s)")
        sys.exit(1)
    print("all identifier patterns accept every published id and reject every malformed shape")


if __name__ == "__main__":
    main()
