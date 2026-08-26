#!/usr/bin/env python3
"""Online supply-chain check: every catalog entry must pin an IMMUTABLE commit SHA,
and the document fetched at that pinned commit must match the entry's claims.

Runs in CI after schema validation. For each entry it fetches the content repo's
document at the pinned ref via the jsDelivr CDN and asserts:
  - the ref is a commit SHA (40-hex SHA-1 or 64-hex SHA-256) - never a tag/branch,
  - the document is reachable (HTTP 200) and valid JSON,
  - its `identifier` equals the entry's identifier,
  - its `content_type` equals the entry's content_type,
  - its `version` equals the entry's version,
  - every SUMMARY field the entry carries is what the document actually implies:
    `scopes_summary`, `platforms`, `plugin_count`, `section_count`, `type_count`,
    `edge_count`. See derived() for the definition and for what it caught, and
  - the CODE the manifest points at - `platforms.web.entry`, fetched at the same
    pinned ref - declares the same version and licence as the manifest does. See
    bundle_mismatch() for why that is this file's job and not the client's.

The version/count comparison is the reason this file exists in its current form. It
used to check only identifier and content_type, and still printed "document matches",
which is how the catalog came to advertise the Infrastructure and Threat packs at
v1.2.0 while the pinned SHA served v1.1.0: someone bumped the entry metadata and did
not re-pin the ref, and the check said ok. A pin whose CLAIMS drift from its BYTES is
worse than no pin, because the marketplace shows one thing and installs another.

Fails closed: any mismatch or fetch error is an error. Because the ref is a commit
SHA (also enforced by the entry schema), the bytes a consumer runs can never change
under them - a later force-push/tag-move in the content repo cannot affect this pin.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

CATALOGS = [
    "registry/community-typepacks.json",
    "registry/community-pluginpacks.json",
    "registry/community-skillpacks.json",
]
HEX = set("0123456789abcdef")


def load(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as fh:
        return json.load(fh)


def is_sha(ref):
    return len(ref) in (40, 64) and all(c in HEX for c in ref)


def fetch_text(url, accept="*/*"):
    req = urllib.request.Request(url, headers={"User-Agent": "vineyard-registry-verify", "Accept": accept})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def fetch(url):
    return json.loads(fetch_text(url, "application/json"))


WRITE_VERBS = (":create", ":update", ":delete")


def members(doc):
    """The plugins a pack document ships. A lone plugin document is a pack of one."""
    return doc.get("plugins") or [doc]


def derived(doc, content_type):
    """What the entry's summary fields MUST equal, computed from the pinned document.

    These are the fields the browse card renders before anyone opens a pack, and until this
    existed nothing checked a single one of them - `claim_mismatch` compared version and the two
    typepack counts, so `scopes_summary`, `platforms`, `plugin_count` and `section_count` were
    whatever the submitter typed. Measured across the catalog when this was added: five entries
    disagreed with their own manifests, and three of those understated what the pack does -
    `disposable_email` advertised no graph write while declaring `node:update`, `web_recon` and
    `xeuledoc` advertised no network while declaring `web_probe`. A permission summary nobody
    verifies is worse than none, because the card looks like a statement of fact.

    The derivation is written down HERE and nowhere else, because the drift came from there being
    no definition at all: three packs computed `scopes_summary.network` three different ways.

    `web_probe` counts as network. It is a SECOND egress shape - an anonymous cross-origin probe
    from the desktop main process - and it is broader than `network`, not narrower: `network`
    pins declared endpoints, `web_probe` reaches an arbitrary host. A pack holding it and showing
    no network badge is the exact inversion of what the badge is for.

    `platforms` is the union of DECLARED platform keys, which is what 13 of 15 packs already
    meant. Whether a pack is desktop-ONLY is a different question, answered by
    `platforms.primary` and enforced in the app (`isDesktopOnly` / `blockedReason`); folding that
    into this list would make two different facts share one field.
    """
    if content_type.endswith(("plugin", "pluginpack")):
        ms = members(doc)
        scopes = [m.get("scopes") or {} for m in ms]
        return {
            "scopes_summary": {
                "network": any(s.get("network") or s.get("web_probe") for s in scopes),
                "graph_write": any(
                    any(v.endswith(WRITE_VERBS) for v in (s.get("graph") or [])) for s in scopes
                ),
                "secret_config": any(
                    c.get("secret") for s in scopes for c in (s.get("config") or [])
                ),
            },
            "platforms": sorted({k for m in ms for k in (m.get("platforms") or {}) if k != "primary"}),
            # Deliberately NOT folded into scopes_summary.network. A service call is egress, but to
            # a destination the host fixes and with the analyst's identity attached — a card reading
            # only "Network" would understate one half and overstate the other.
            "services": sorted({name for s in scopes for name in (s.get("services") or [])}),
            "plugin_count": len(ms),
        }
    if content_type.endswith("skillpack"):
        return {"section_count": len(doc.get("sections") or [])}
    if content_type.endswith("typepack"):
        return {"type_count": len(doc.get("types") or []), "edge_count": len(doc.get("edge_types") or [])}
    return {}


def claim_mismatch(doc, entry):
    """The first way the fetched document contradicts what the entry advertises, or None.

    A field the entry OMITS is not a claim, and is skipped - inventing one here would fail packs
    that are simply described more loosely. A field the entry DOES carry must be right.
    """
    if doc.get("version") != entry.get("version"):
        return f"pinned doc version '{doc.get('version')}' != entry version '{entry.get('version')}'"
    for field, want in derived(doc, entry.get("content_type", "")).items():
        claimed = entry.get(field)
        if claimed is None:
            continue
        if field == "scopes_summary":
            for key, value in want.items():
                if bool(claimed.get(key)) != value:
                    return (
                        f"scopes_summary.{key}={claimed.get(key)} but the pinned manifest says "
                        f"{value} - the browse card shows this before anyone opens the pack"
                    )
            continue
        if field in ("platforms", "services"):
            claimed = sorted(claimed)
        if claimed != want:
            return f"entry claims {field}={claimed} but the pinned document has {want}"
    return None


# --- the bundle: the half of a pack nobody was reading -------------------------------------------
#
# Everything above verifies the MANIFEST. The manifest is not what runs: `platforms.web.entry`
# names a JS module, and that module is what the plugin worker imports and executes. It ships in
# the same repo at the same pinned commit, and every pack embeds its own copy of the manifest
# inside it - so the pack has two statements of its version and its licence and, until this, one
# of them was checked.
#
# They drift, measured: xeuledoc is published at 2.0.0 / GPL-3.0 and the bundle at its own pinned
# commit says 1.0.0 / Apache-2.0, because the manifest JSON was edited and the bundle was not
# rebuilt. That is a licence notice that is simply wrong about a GPL port, and a version the
# update prompt cannot see.
#
# This is a PUBLISHING check, not a client one. The commit pin already guarantees the client gets
# the bytes that live at that hash - an immutable ref cannot be substituted - so there is nothing
# for the app to defend against here and no digest chain worth building for it. What is left is
# whether the thing being published is internally consistent, which is exactly what the registry
# is for.
_KEY = r'(?<![\w$])["\']?%s["\']?\s*:\s*'
_STR = r'["\']([^"\']*)["\']'


def _prop(js, key, chunk_start, chunk_end):
    """The value of a string-valued object key inside a slice of minified JS, or None.

    Deliberately a regex and not a JS parser. A pack bundle is esbuild output whose manifest is a
    plain object literal of string constants, and the alternative is a JavaScript engine in CI to
    read two fields. The lookbehind is the one non-obvious part and it is load-bearing: without it
    `min_app_version:"0.1.0"` matches a search for `version`, and every desktop pack fails on a
    version it never claimed.
    """
    m = re.search(_KEY % re.escape(key) + _STR, js[chunk_start:chunk_end])
    return m.group(1) if m else None


def bundle_claims(js, identifier):
    """What the bundle says about one identifier: {'version': .., 'license': ..}, or None.

    Scoped to the run of source from that `identifier:` to the NEXT one, because a pack bundle
    holds several manifests - the pack's and each member's - and members are allowed to differ
    from their pack (xeuledoc is GPL-3.0 inside an otherwise permissive pack, which the schema
    says in so many words). Reading the file globally would compare a member against the pack and
    call a legitimate difference a mismatch.

    None means the bundle never names this identifier, which is not a contradiction: a pack may
    build its manifest at runtime, or export only the members. A value the bundle does not state
    is not a claim, the same rule claim_mismatch() applies to the entry.
    """
    anchor = re.compile(_KEY % "identifier" + _STR)
    for m in anchor.finditer(js):
        if m.group(1) != identifier:
            continue
        nxt = anchor.search(js, m.end())
        end = nxt.start() if nxt else len(js)
        return {k: _prop(js, k, m.start(), end) for k in ("version", "license")}
    return None


def bundle_url(manifest, pack, root):
    """The absolute CDN url of the code this manifest runs, or None when it names none.

    `entry` is relative to the pack's REPO ROOT, not to the manifest's directory - the app
    resolves it exactly this way (frontend plugins/remote.ts), and getting it wrong yields
    plugins/dist/pack.mjs, which was a shipped 404. A member with no web block of its own
    inherits the pack's, which is the one-module-many-plugins case.
    """
    web = ((manifest.get("platforms") or {}).get("web")) or ((pack.get("platforms") or {}).get("web")) or {}
    entry = web.get("entry")
    if not entry or entry == "inline":
        return None
    return entry if entry.startswith("http") else root + entry.lstrip("/")


def bundle_mismatch(doc, entry, root, cache):
    """The first way the pinned BUNDLE contradicts the pinned manifest, or None.

    Fails closed on a fetch error, like every other check here: an `entry` that 404s at its own
    pinned commit is a pack that cannot run, and it is better to hear that at publish time.
    """
    if not (entry.get("content_type") or "").endswith(("plugin", "pluginpack")):
        return None
    for manifest in [doc] + (doc.get("plugins") or []):
        url = bundle_url(manifest, doc, root)
        if not url:
            continue
        if url not in cache:
            try:
                cache[url] = fetch_text(url)
            except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as e:
                return f"cannot fetch the pinned bundle {url}: {e}"
        claims = bundle_claims(cache[url], manifest.get("identifier"))
        if claims is None:
            continue
        for field in ("version", "license"):
            said, want = claims[field], manifest.get(field)
            if said is not None and want is not None and said != want:
                # Name the MEMBER only when it is not the pack itself — main() has already printed
                # the pack's identifier, and repeating it reads as two different packs.
                who = "" if manifest is doc else f"member {manifest.get('identifier')}: "
                return (
                    f"{who}bundle {field} '{said}' != manifest {field} '{want}' "
                    f"(catalog entry version '{entry.get('version')}') at {url}"
                    " — the manifest was edited without rebuilding dist/"
                )
    return None


def main():
    bad = 0
    ok = 0
    skipped = 0
    bundles = {}  # url -> source, so a pack's members share the one fetch of their shared module
    for cat in CATALOGS:
        for entry in load(cat):
            ident = entry.get("identifier", "<no id>")
            src = f"packs/{ident}.json"  # annotate what the author wrote, not the build output
            # A WITHDRAWN pack is not verified, because a common reason to withdraw one is that its
            # content is gone — repo deleted, taken down, made private. Keeping the fetch would mean
            # the registry goes red and STAYS red at exactly the moment the delisting has to merge.
            # A deprecated pack still loads for its users, so it is still held to the pin.
            if (entry.get("status") or {}).get("state") == "withdrawn":
                skipped += 1
                print(f"  skip {ident} (withdrawn — pinned content is no longer required to exist)")
                continue
            ref, repo, path = entry.get("ref", ""), entry.get("repo", ""), entry.get("path", "")
            if not is_sha(ref):
                print(f"::error file={src}::{ident}: ref '{ref}' is not an immutable commit SHA (tags/branches are mutable)")
                bad += 1
                continue
            url = f"https://cdn.jsdelivr.net/gh/{repo}@{ref}/{path}"
            try:
                doc = fetch(url)
            except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as e:
                print(f"::error file={src}::{ident}: cannot fetch pinned doc {url}: {e}")
                bad += 1
                continue
            if doc.get("identifier") != ident:
                print(f"::error file={src}::{ident}: pinned doc identifier '{doc.get('identifier')}' != entry identifier")
                bad += 1
            elif doc.get("content_type") != entry.get("content_type"):
                print(f"::error file={src}::{ident}: pinned doc content_type '{doc.get('content_type')}' != entry '{entry.get('content_type')}'")
                bad += 1
            elif claim_mismatch(doc, entry):
                # Two different fixes, so do not prescribe one: a version/count mismatch usually
                # means the ref was not re-pinned, while a summary mismatch means the entry says
                # something the manifest does not. Naming both beats confidently naming the wrong one.
                print(
                    f"::error file={src}::{ident}: {claim_mismatch(doc, entry)}"
                    " — correct the entry, or re-pin `ref` to a commit that matches it"
                )
                bad += 1
            elif bundle_err := bundle_mismatch(doc, entry, f"https://cdn.jsdelivr.net/gh/{repo}@{ref}/", bundles):
                # A different fix again, and only one: the manifest is what was reviewed, so the
                # bundle is what has to be rebuilt and re-pinned. Never "correct the manifest".
                print(f"::error file={src}::{ident}: {bundle_err}")
                bad += 1
            else:
                ok += 1
                print(f"  ok  {ident} @ {ref[:12]}… ({entry.get('content_type')})")
    if bad:
        print(f"\n{bad} pin verification failure(s)")
        sys.exit(1)
    tail = f", {skipped} withdrawn and skipped" if skipped else ""
    print(f"\nall {ok} pinned entries verified (immutable commit SHA + document and bundle match){tail}")


if __name__ == "__main__":
    main()
