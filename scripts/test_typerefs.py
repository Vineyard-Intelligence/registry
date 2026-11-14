#!/usr/bin/env python3
"""Self-test for the Type Pack document checks in check_typerefs.py.

check_typerefs.py runs against the live catalog, and every published pack passes it, so its
rejecting branches are exercised by nothing: deleting one would leave CI green until a pack that
needed it shipped. These asserts run the two pure document checks directly, offline.

`normalize` is the reason this file exists, and `duplicate_key` the second. The Cryptocurrency pack is its first user and spells it
correctly, so the live catalog only ever takes the accepting branch — a misspelt rule, which the
app silently compares exactly, would otherwise be caught by no one.

Run with: python scripts/test_typerefs.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_typerefs import check_duplicate_key, check_identity, check_normalize  # noqa: E402


def errors_for(check, props, identity=None):
    t = {"category": "c", "name": "n", "label_property": "a", "properties": props}
    if identity is not None:
        t["identity_properties"] = identity
    errors = []
    check("run.vineyard.typepacks.x", "c.n", t, errors)
    return [message for _, message in errors]


def main():
    # --- normalize ----------------------------------------------------------------------
    assert errors_for(check_normalize, {"a": {"type": "string"}}) == []
    assert errors_for(check_normalize, {"a": {"type": "string", "normalize": "crypto_address"}}) == []
    assert errors_for(check_normalize, {"a": {"type": "hash", "normalize": "hex"}}) == []
    # `case_insensitive: false` beside a rule is harmless and stays allowed, as the schema allows it.
    assert errors_for(check_normalize, {"a": {"type": "string", "normalize": "hex", "case_insensitive": False}}) == []

    typo = errors_for(check_normalize, {"a": {"type": "string", "normalize": "crypto_adress"}})
    assert len(typo) == 1 and "not one of" in typo[0], typo
    assert len(errors_for(check_normalize, {"a": {"type": "string", "normalize": None}})) == 1

    both = errors_for(check_normalize, {"a": {"type": "string", "normalize": "crypto_address", "case_insensitive": True}})
    assert len(both) == 1 and "case_insensitive" in both[0], both
    for keeps_case in ("hex", "url"):
        pair = errors_for(check_normalize, {"a": {"type": "string", "normalize": keeps_case, "case_insensitive": True}})
        assert len(pair) == 1 and "case_insensitive" in pair[0], (keeps_case, pair)

    # The DNS, IP and URL rules. hostname, ip and cidr only ever produce lower case, so the flag
    # beside them is the fallback for an app that predates the rule, and is allowed.
    for rule in ("hostname", "ip", "cidr"):
        assert errors_for(check_normalize, {"a": {"type": "string", "normalize": rule}}) == [], rule
        assert errors_for(check_normalize, {"a": {"type": "string", "normalize": rule, "case_insensitive": True}}) == [], rule
    assert errors_for(check_normalize, {"a": {"type": "url", "normalize": "url"}}) == []

    nonscalar = errors_for(check_normalize, {"a": {"type": "array", "normalize": "hex"}})
    assert len(nonscalar) == 1 and "scalar" in nonscalar[0], nonscalar

    # Every property is checked, not only the identity ones: a rule on a field that is not part of
    # the identity today becomes load-bearing the day the pack adds it to `identity_properties`.
    off_identity = errors_for(
        check_normalize,
        {"a": {"type": "string"}, "b": {"type": "string", "normalize": "lower"}},
        identity=["a"],
    )
    assert len(off_identity) == 1 and "'b'" in off_identity[0], off_identity

    # --- duplicate_key --------------------------------------------------------------------
    phone = {"number": {"type": "string"}, "e164": {"type": "string", "duplicate_key": True}}
    assert errors_for(check_duplicate_key, phone) == []
    assert errors_for(check_duplicate_key, {"a": {"type": "string"}, "b": {"type": "string", "duplicate_key": False}}) == []
    not_bool = errors_for(check_duplicate_key, {"a": {"type": "string"}, "b": {"type": "string", "duplicate_key": "yes"}})
    assert len(not_bool) == 1 and "not a boolean" in not_bool[0], not_bool
    on_list = errors_for(check_duplicate_key, {"a": {"type": "string"}, "b": {"type": "array", "duplicate_key": True}})
    assert len(on_list) == 1 and "scalar" in on_list[0], on_list
    # label_property is the identity when identity_properties is absent ("a" in errors_for).
    on_identity = errors_for(check_duplicate_key, {"a": {"type": "string", "duplicate_key": True}})
    assert len(on_identity) == 1 and "identity already" in on_identity[0], on_identity
    in_composite = errors_for(
        check_duplicate_key,
        {"a": {"type": "string"}, "b": {"type": "string", "duplicate_key": True}},
        identity=["a", "b"],
    )
    assert len(in_composite) == 1 and "identity already" in in_composite[0], in_composite

    # --- identity_properties (the existing check, which had no self-test either) ---------
    assert errors_for(check_identity, {"a": {"type": "string"}, "b": {"type": "string"}}, identity=["a", "b"]) == []
    missing = errors_for(check_identity, {"a": {"type": "string"}}, identity=["a", "nope"])
    assert len(missing) == 1 and "'nope'" in missing[0], missing
    arr = errors_for(check_identity, {"a": {"type": "string"}, "tags": {"type": "array"}}, identity=["a", "tags"])
    assert len(arr) == 1 and "non-scalar" in arr[0], arr

    print("check_typerefs document checks: all assertions passed")


if __name__ == "__main__":
    main()
