#!/usr/bin/env python3
"""Self-test for the delisting rules in validate.py.

The live catalog holds no delisted entry, so nothing here is exercised by validating it — the
guards could be deleted and CI would stay green until the first real withdrawal, which is the worst
possible moment to find out. These asserts run the two pure functions directly, offline.

Run with: python scripts/test_delisting.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate import dependency_error, status_error  # noqa: E402

LIVE = {"identifier": "run.vineyard.typepacks.social", "content_type": "vineyard:typepack"}
GONE = {
    "identifier": "run.vineyard.typepacks.old",
    "content_type": "vineyard:typepack",
    "status": {"state": "withdrawn", "reason": "x" * 10, "since": "2026-01-01"},
}
DEPRECATED = {
    "identifier": "run.vineyard.typepacks.tired",
    "content_type": "vineyard:typepack",
    "status": {"state": "deprecated", "reason": "x" * 10, "since": "2026-01-01"},
}
BY_ID = {e["identifier"]: e for e in (LIVE, GONE, DEPRECATED)}


def pack(**kw):
    return {"identifier": "run.vineyard.pluginpacks.p", "content_type": "vineyard:pluginpack", **kw}


def main():
    # --- dependencies ------------------------------------------------------------------
    assert dependency_error(pack(typepacks=[LIVE["identifier"]]), BY_ID) is None
    assert dependency_error(pack(), BY_ID) is None
    assert "not in this catalog" in dependency_error(pack(typepacks=["com.nobody.typepacks.x"]), BY_ID)
    # A live pack may depend on neither state. Deprecated matters as much as withdrawn: the
    # co-install offer is built from this field, so the analyst is handed the retired pack anyway.
    assert "withdrawn" in dependency_error(pack(typepacks=[GONE["identifier"]]), BY_ID)
    assert "deprecated" in dependency_error(pack(typepacks=[DEPRECATED["identifier"]]), BY_ID)
    # ...but a pack that is ITSELF delisted may keep its edges — otherwise withdrawing a type pack
    # and the packs that use it could never be done, in either order.
    dead = pack(typepacks=[GONE["identifier"]], status={"state": "withdrawn", "reason": "x" * 10, "since": "2026-01-01"})
    assert dependency_error(dead, BY_ID) is None
    # `requires` is the same rule on the skillpack side, not a second implementation.
    assert "withdrawn" in dependency_error({"requires": [GONE["identifier"]]}, BY_ID)

    # --- status.replacement ------------------------------------------------------------
    def with_status(**extra):
        return {"identifier": "run.vineyard.typepacks.z", "status": {"state": "deprecated", "reason": "x" * 10, "since": "2026-01-01", **extra}}

    assert status_error({"identifier": "run.vineyard.typepacks.z"}, BY_ID) is None  # no status at all
    assert status_error(with_status(), BY_ID) is None  # status without a replacement
    assert status_error(with_status(replacement=LIVE["identifier"]), BY_ID) is None
    assert "not in this catalog" in status_error(with_status(replacement="com.nobody.typepacks.x"), BY_ID)
    assert "withdrawn" in status_error(with_status(replacement=GONE["identifier"]), BY_ID)
    assert "deprecated" in status_error(with_status(replacement=DEPRECATED["identifier"]), BY_ID)
    # Self-reference is checked against a LIVE identifier on purpose: pointing a delisted entry at
    # itself would ALSO trip the "replacement is delisted" branch, so the case would still fail with
    # the self-check deleted and would not prove it exists.
    self_ref = {"identifier": LIVE["identifier"], "status": {"state": "deprecated", "reason": "x" * 10, "since": "2026-01-01", "replacement": LIVE["identifier"]}}
    assert "points at this entry itself" in (status_error(self_ref, BY_ID) or "")

    print("delisting rules ok: 14 cases")


if __name__ == "__main__":
    main()
