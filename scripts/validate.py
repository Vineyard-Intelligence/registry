#!/usr/bin/env python3
"""Validate every submission in `packs/` against the registry-entry JSON Schemas.

Run in CI on every pull request. Exits non-zero if any entry is invalid.

Validates the SUBMISSIONS, not the generated catalogs, so a failure annotates the file the
author actually wrote — `packs/<identifier>.json` — rather than a line in a build artifact
they are not supposed to edit. `build_registry.py` decides which schema applies, by the same
`content_type` routing that decides which catalog the entry lands in.
"""
import json
import os
import sys

from jsonschema import Draft202012Validator

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_registry import ROOT, load_packs  # noqa: E402

SCHEMA_FOR = {
    "registry/community-typepacks.json": "schemas/registry-typepack-entry.schema.json",
    "registry/community-pluginpacks.json": "schemas/registry-plugin-entry.schema.json",
    "registry/community-skillpacks.json": "schemas/registry-skillpack-entry.schema.json",
}


KINDS = ("plugins", "pluginpacks", "typepacks", "skillpacks")


def load(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as fh:
        return json.load(fh)


def namespace(identifier):
    """The author's reverse-DNS prefix — everything before the kind segment — or None.

    Split at the FIRST kind segment, not the last: a typepack name may itself contain dots
    (`typepacks\\.[a-z0-9]+(?:[._-][a-z0-9]+)*`), so the trailing part is not a fixed width.
    """
    parts = identifier.split(".")
    for i, part in enumerate(parts):
        if part in KINDS and i >= 2:
            return ".".join(parts[:i])
    return None


def authorship_error(entry, owners):
    """How the entry misuses `verified` or `author`, or None.

    `verified` and `author` are the two fields that make a pack look trustworthy, and both were
    plain submitter input: the schema promised `verified` was "set by CI, not self-asserted", but
    no file backed it and no code read it. The check runs in both directions on purpose —

      - a namespace someone owns may only be published under by that someone, so a stranger
        cannot list under `run.vineyard.*`;
      - a name someone owns may only be worn inside their own namespaces, so a stranger cannot
        publish `com.evil.pluginpacks.x` as `author: vineyard-run`.

    One direction alone leaves the other half of the impersonation open.
    """
    ident = entry.get("identifier", "")
    author = entry.get("author")
    ns = namespace(ident)
    claimed_by = {n: handle for handle, spec in owners.items() for n in spec.get("namespaces", [])}

    if ns in claimed_by and author != claimed_by[ns]:
        return (
            f"namespace '{ns}' belongs to {claimed_by[ns]}, but this entry is authored by "
            f"'{author}'. Publish under a namespace you control."
        )
    if author in owners and ns not in owners[author].get("namespaces", []):
        return (
            f"author '{author}' is a listed author whose namespaces are "
            f"{owners[author].get('namespaces', [])}, but this entry is '{ns}'. "
            f"An entry may not claim a name it does not publish under."
        )
    if entry.get("verified") is True and author not in owners:
        return (
            f"verified=true but '{author}' is not in verified-authors.json. The badge is set by "
            f"the operator, not by the submission — omit it."
        )
    return None


DEPENDENCY_FIELDS = {
    # field -> what the install flow does with it, for the error message
    "requires": "a Skill Pack's steps call these Plugin Packs; install offers them alongside",
    "typepacks": "a Plugin Pack writes these types; install offers the Type Packs alongside",
}


def status_error(entry, by_id):
    """How the entry's `status` block is unusable, or None.

    Only `replacement` is checkable — `state`/`reason`/`since` are the schema's business. But a
    replacement that does not resolve is the delisting notice failing at the one job it has: the
    analyst is told to move to something, and the something is not there. Pointing at another
    delisted pack is the same failure one hop out.
    """
    status = entry.get("status")
    if not status:
        return None
    dest = status.get("replacement")
    if dest is None:
        return None
    if dest == entry.get("identifier"):
        return "`status.replacement` points at this entry itself."
    target = by_id.get(dest)
    if target is None:
        return (
            f"`status.replacement` names '{dest}', which is not in this catalog. Point at a live "
            f"pack, or drop the field — a dead pointer reads worse than no advice."
        )
    if target.get("status"):
        return (
            f"`status.replacement` names '{dest}', which is itself "
            f"{target['status'].get('state')}. Send users somewhere live."
        )
    return None


def dependency_error(entry, by_id):
    """A declared dependency that is not in this catalog, or is no longer live, or None.

    Both fields feed the same place — `marketplace/registry.ts` builds the co-install offer from
    them — so an identifier that resolves to nothing is not a cosmetic error: the analyst installs
    the pack, the dependency is silently not offered, and the pack runs against types or plugins
    that were never installed. Nothing checked this, in either field.

    Existence and liveness are the whole check. The KIND is already pinned by the entry schemas'
    patterns (`typepacks` items must match `…typepacks\\.…`, `requires` items `…plugins|pluginpacks…`)
    and identifiers are globally unique, so an id of the right shape can only have been registered by
    an entry of the right kind. Catalog-only, for the same reason as typeRefs: the install flow can
    offer nothing it cannot resolve.
    """
    for field, why in DEPENDENCY_FIELDS.items():
        for dep in entry.get(field) or []:
            target = by_id.get(dep)
            if target is None:
                return (
                    f"`{field}` lists '{dep}', which is not in this catalog — {why}, so it would "
                    f"install without one. Publish that pack first, or drop the dependency."
                )
            # A live pack may not depend on a delisted one. The co-install offer is built from these
            # fields, so leaving the edge in place means installing this pack hands the analyst a
            # pack the registry has already said not to use — and for `withdrawn`, one the client
            # refuses to load, which puts them straight back in the failure the field exists to
            # prevent. Delisting a dependency is therefore a two-step: fix the dependants first.
            if target.get("status") and not entry.get("status"):
                state = target["status"].get("state")
                return (
                    f"`{field}` lists '{dep}', which is {state} — {why}, so this pack would offer "
                    f"a delisted one alongside itself. Move to a live pack, or delist this one too."
                )
    return None


def main():
    bad = total = deps = delisted = 0
    owners = load("verified-authors.json").get("authors", {})
    grouped = load_packs()
    # Every entry in the catalog by identifier, gathered before validating, so a dependency (or a
    # `status.replacement`) may point at a pack listed in the same pull request rather than forcing
    # two round trips to publish a pair. Entries, not bare identifiers: resolving a reference now
    # also means asking whether the target is still live.
    by_id = {e["identifier"]: e for entries in grouped.values() for e in entries}
    for catalog, entries in sorted(grouped.items()):
        validator = Draft202012Validator(load(SCHEMA_FOR[catalog]))
        for entry in entries:
            total += 1
            deps += len(entry.get("requires") or []) + len(entry.get("typepacks") or [])
            delisted += 1 if entry.get("status") else 0
            ident = entry.get("identifier", "<no identifier>")
            errors = sorted(validator.iter_errors(entry), key=lambda e: list(e.path))
            if errors:
                bad += 1
                print(f"::error file=packs/{ident}.json::{ident}: {errors[0].message}")
                continue
            problem = (
                authorship_error(entry, owners)
                or status_error(entry, by_id)
                or dependency_error(entry, by_id)
            )
            if problem:
                bad += 1
                print(f"::error file=packs/{ident}.json::{ident}: {problem}")
        print(f"{catalog}: {len(entries)} entr{'y' if len(entries)==1 else 'ies'} checked")
    if bad:
        print(f"\n{bad} invalid entr{'y' if bad==1 else 'ies'} of {total}")
        sys.exit(1)
    print(
        f"\nall {total} entries valid ({delisted} delisted): every verified badge is backed by "
        f"verified-authors.json, and all {deps} declared dependencies resolve inside the catalog "
        f"to a pack that is still live"
    )


if __name__ == "__main__":
    main()
