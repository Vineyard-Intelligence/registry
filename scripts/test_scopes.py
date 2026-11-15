#!/usr/bin/env python3
"""Self-test for verify_pinned.scope_problem() — the rule for `scopes.network_configurable`.

A configurable scope lets a plugin call a server whose address the analyst sets: a self-hosted MISP
or OpenCTI, an organisation's own TIP. The app grants nothing for one that does not read a URL
setting declared in the same plugin, so a broken declaration would pass review, show the analyst an
install line, and then refuse every request. No published pack declares the scope yet, so validating
the live catalog exercises none of this; these cases are the only place the rule is proven.

Offline: no network, no catalog. Run with: python scripts/test_scopes.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_pinned import scope_problem  # noqa: E402

PACK = "vineyard:pluginpack"
URL = {"key": "misp_url", "type": "url", "label": "MISP URL"}


def member(scopes, ident="run.vineyard.plugins.misp_events"):
    return {"identifier": ident, "platforms": {"web": {}}, "scopes": scopes}


def pack(*members):
    return {"content_type": PACK, "version": "1.0.0", "plugins": list(members)}


def scope(**over):
    return {"config_key": "misp_url", "methods": ["POST"], **over}


def main():
    # --- what passes --------------------------------------------------------------------------------
    assert scope_problem(pack(member({}))) is None, "no configurable scope, nothing to say"
    assert scope_problem(pack(member({"network_configurable": [scope()], "config": [URL]}))) is None
    assert scope_problem(pack(member({"network_configurable": [scope(schemes=["https", "http"])], "config": [URL]}))) is None
    single = {"content_type": "vineyard:plugin", **member({"network_configurable": [scope()], "config": [URL]})}
    assert scope_problem(single) is None, "a lone plugin document is its own member"

    # --- the setting must be this plugin's own URL setting ---------------------------------------------
    assert "does not declare" in scope_problem(pack(member({"network_configurable": [scope()]})))
    as_string = {"network_configurable": [scope()], "config": [{"key": "misp_url", "type": "string"}]}
    assert "does not declare" in scope_problem(pack(member(as_string))), "a string setting is not an address"
    # Declared by a sibling only: the app reads config per member, so the sibling's setting is not this
    # plugin's, and the analyst would be choosing an address for a different plugin.
    sibling = member({"config": [URL]}, ident="run.vineyard.plugins.misp_settings")
    lonely = member({"network_configurable": [scope()]})
    assert "does not declare" in scope_problem(pack(sibling, lonely))
    assert "not a config key" in scope_problem(pack(member({"network_configurable": [scope(config_key="Misp URL")], "config": [URL]})))

    # --- shape ------------------------------------------------------------------------------------------
    assert "must be a list" in scope_problem(pack(member({"network_configurable": scope(), "config": [URL]})))
    assert "methods" in scope_problem(pack(member({"network_configurable": [{"config_key": "misp_url"}], "config": [URL]})))
    for bad in ([], ["ftp"], "https"):
        problem = scope_problem(pack(member({"network_configurable": [scope(schemes=bad)], "config": [URL]})))
        assert problem and "schemes" in problem, bad
    # The app upper-cases the request method and looks it up exactly, so a lower-case verb, one the
    # schema does not list, or a non-string refuses every request while CI would have stayed green.
    for bad in (["post"], ["get"], ["OPTIONS"], ["FETCH"], [1], ["POST", "POST"]):
        problem = scope_problem(pack(member({"network_configurable": [scope(methods=bad)], "config": [URL]})))
        assert problem and "methods" in problem, bad
    assert scope_problem(pack(member({"network_configurable": [scope(methods=[])], "config": [URL]}))) is None, (
        "an empty list allows every method, as for network"
    )
    assert scope_problem(
        pack(member({"network_configurable": [scope(methods=["GET", "POST", "PUT", "PATCH", "DELETE"])], "config": [URL]}))
    ) is None
    # additionalProperties: false. The app ignores an `endpoint`, so it pins nothing.
    stray = scope_problem(pack(member({"network_configurable": [scope(endpoint="https://evil.example")], "config": [URL]})))
    assert stray and "'endpoint'" in stray, stray
    assert "purpose" in scope_problem(pack(member({"network_configurable": [scope(purpose=1)], "config": [URL]})))

    print("scope_problem: configurable network scopes must read their own URL setting — ok")


if __name__ == "__main__":
    main()
