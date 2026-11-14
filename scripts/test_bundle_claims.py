#!/usr/bin/env python3
"""Self-test for verify_pinned's bundle check — the version/licence a pack's CODE declares.

Running it against the live catalog proves only what the live catalog happens to contain. Two
of nineteen packs drift today and both drift on `version`, so the licence half of the comparison
is exercised by nothing at all: deleting it would leave CI green while a GPL port kept shipping
an Apache notice, which is the case that motivated the check. The same is true of the two rules
that keep it from crying wolf — `min_app_version` must not read as `version`, and a member is
allowed to carry a different licence from its pack.

The entry half has the same problem in a sharper form. Every published manifest says
`dist/pack.mjs`, so a bundle_url() that passed an absolute `entry` straight through (it did) is
indistinguishable on the live catalog from one that refuses it — and the absolute case is the one
where CI checks a file inside the pin while the app runs a branch somewhere else. The spellings
below are each a real way out of the pin to a browser's URL parser.

Offline: no network, no catalog. Run with: python scripts/test_bundle_claims.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify_pinned  # noqa: E402
from verify_pinned import ENTRY, bundle_claims, bundle_mismatch, bundle_url  # noqa: E402

PACK_ID = "run.vineyard.pluginpacks.demo"
MEMBER_ID = "run.vineyard.plugins.demo"

# esbuild output, shortened: the member object first, the pack object last, both minified with
# bare keys — the shape every published pack actually has.
BUNDLE = (
    'var de={identifier:"%s",content_type:"vineyard:plugin",name:"Demo",version:"1.0.0",'
    'license:"GPL-3.0",platforms:{primary:"desktop",web:{runtime:"sandbox-js",entry:"dist/pack.mjs"},'
    'desktop:{runtime:"sandbox-js",entry:"dist/pack.mjs",min_app_version:"0.1.0"}}};'
    'var pe={identifier:"%s",content_type:"vineyard:pluginpack",name:"Demo",version:"2.0.0",'
    'license:"Apache-2.0",plugins:[de]};export{pe as default};' % (MEMBER_ID, PACK_ID)
)


def pack(**over):
    doc = {
        "identifier": PACK_ID,
        "content_type": "vineyard:pluginpack",
        "version": "2.0.0",
        "license": "Apache-2.0",
        "platforms": {"web": {"runtime": "sandbox-js", "entry": "dist/pack.mjs"}},
        "plugins": [{"identifier": MEMBER_ID, "version": "1.0.0", "license": "GPL-3.0"}],
    }
    doc.update(over)
    return doc


ROOT = "https://cdn.jsdelivr.net/gh/Org/repo@" + "a" * 40 + "/"
URL = ROOT + "dist/pack.mjs"


def main():
    # --- parsing: each identifier gets its OWN object's values, not the file's first --------------
    assert bundle_claims(BUNDLE, PACK_ID) == {"version": "2.0.0", "license": "Apache-2.0"}
    assert bundle_claims(BUNDLE, MEMBER_ID) == {"version": "1.0.0", "license": "GPL-3.0"}
    # A member legitimately differing from its pack must not read as a mismatch of either.
    assert bundle_claims(BUNDLE, MEMBER_ID)["license"] != bundle_claims(BUNDLE, PACK_ID)["license"]

    # `min_app_version` is a suffix of `version`. Without the lookbehind in _prop, the member's
    # version reads as "0.1.0" and every desktop pack fails on a version it never claimed.
    assert bundle_claims(BUNDLE, MEMBER_ID)["version"] == "1.0.0"

    # A bundle that never names the identifier states nothing, and silence is not a contradiction.
    assert bundle_claims(BUNDLE, "run.vineyard.plugins.absent") is None
    # Nor is a field the object simply omits.
    assert bundle_claims('{identifier:"x",name:"y"}', "x") == {"version": None, "license": None}

    # Non-minified output (quoted keys, spaces) is the same statement, so it must parse the same.
    pretty = '{ "identifier": "%s", "version": "2.0.0", "license": "Apache-2.0" }' % PACK_ID
    assert bundle_claims(pretty, PACK_ID) == {"version": "2.0.0", "license": "Apache-2.0"}

    # --- entry resolution: repo ROOT, not the manifest's directory --------------------------------
    doc = pack()
    assert bundle_url(doc, doc, ROOT) == URL
    # A member with no web block of its own runs the pack's module — one file, many plugins.
    assert bundle_url(doc["plugins"][0], doc, ROOT) == URL
    # 'inline' names no module, so the plugin cannot run: refused, not skipped.
    inline = {"platforms": {"web": {"entry": "inline"}}}
    try:
        bundle_url(inline, inline, ROOT)
    except ValueError:
        pass
    else:
        raise AssertionError("an 'inline' entry must be refused")
    assert bundle_url({}, {}, ROOT) is None

    # --- entry containment: the code CI checks is the code the app runs ---------------------------
    # Each of these resolves OUTSIDE `ROOT` in a browser (or, for %2F, at the CDN, which decodes it
    # before routing), so the worker would import code the pin does not cover. Refused, naming the
    # field, whether the pack or a member says it.
    evil = "https://cdn.jsdelivr.net/gh/someone/x@main/evil.mjs"
    escapes = [
        evil,  # measured: kept as written by the app, imported by the worker
        "HTTPS://cdn.jsdelivr.net/gh/someone/x@main/evil.mjs",
        "http://cdn.jsdelivr.net/gh/someone/x@main/evil.mjs",
        "//cdn.jsdelivr.net/gh/someone/x@main/evil.mjs",
        "../../someone/x@main/evil.mjs",
        "dist/../../../someone/x@main/evil.mjs",
        "/gh/someone/x@main/evil.mjs",  # used to be stripped to gh/… and checked INSIDE the pin
        "/dist/pack.mjs",  # likewise: the browser reads it from the CDN root, not the repo root
        "%2e%2e/%2e%2e/someone/x@main/evil.mjs",
        "%2E%2E/%2E%2E/someone/x@main/evil.mjs",
        ".%2e/.%2e/someone/x@main/evil.mjs",
        "..\\..\\someone\\x@main\\evil.mjs",
        "\\\\cdn.jsdelivr.net\\gh\\someone\\x@main\\evil.mjs",
        "dist%2F..%2F..%2F..%2Fsomeone%2Fx@main%2Fevil.mjs",
        "dist%5Cpack.mjs",
        ".\t./.\t./someone/x@main/evil.mjs",  # the parser strips the tab
        "https:../../someone/x@main/evil.mjs",
        "data:text/javascript,export default {}",
        "javascript:1",
        "./dist/pack.mjs",  # harmless, but not a spelling Python and the browser are sure to agree on
        "dist/./pack.mjs",
        "dist/pack.mjs?v=1",
        "dist/pack.mjs#x",
        " dist/pack.mjs",
        "dist/pack.mjs ",
        ROOT + "dist/pack.mjs",  # even the pin itself, spelled absolutely: a manifest cannot know its own SHA
    ]
    for bad in escapes:
        doc = pack(platforms={"web": {"runtime": "sandbox-js", "entry": bad}})
        try:
            bundle_url(doc, doc, ROOT)
        except ValueError as e:
            assert "not a path inside the pinned commit" in str(e), (bad, e)
        else:
            raise AssertionError(f"entry {bad!r} must be refused")
        member = {"identifier": MEMBER_ID, "platforms": {"web": {"runtime": "sandbox-js", "entry": bad}}}
        try:
            bundle_url(member, pack(), ROOT)
        except ValueError:
            pass
        else:
            raise AssertionError(f"member entry {bad!r} must be refused")
    for not_a_string in (1, ["dist/pack.mjs"], {"path": "dist/pack.mjs"}):
        try:
            bundle_url({"platforms": {"web": {"entry": not_a_string}}}, {}, ROOT)
        except ValueError:
            pass
        else:
            raise AssertionError(f"entry {not_a_string!r} must be refused")
    # What a real pack names still resolves to the file in the pin, exactly as the app builds it.
    for good in ("dist/pack.mjs", "pack.mjs", "build/v2/pack-1.0.0.mjs"):
        doc = pack(platforms={"web": {"runtime": "sandbox-js", "entry": good}})
        assert bundle_url(doc, doc, ROOT) == ROOT + good, good

    # Refused BEFORE anything is fetched: the point is that CI never looks at a stand-in file.
    real_fetch = verify_pinned.fetch_text

    def no_fetch(url, *a, **k):
        raise AssertionError(f"fetched {url} for an entry that should have been refused")

    verify_pinned.fetch_text = no_fetch
    try:
        err = bundle_mismatch(pack(platforms={"web": {"runtime": "sandbox-js", "entry": evil}}), {"content_type": "vineyard:pluginpack"}, ROOT, {})
        assert err and "not a path inside the pinned commit" in err and "member" not in err, err
        doc = pack()
        doc["plugins"][0]["platforms"] = {"web": {"runtime": "sandbox-js", "entry": "../../someone/x@main/evil.mjs"}}
        err = bundle_mismatch(doc, {"content_type": "vineyard:pluginpack"}, ROOT, {URL: BUNDLE})
        assert err and err.startswith(f"member {MEMBER_ID}: ") and "not a path inside the pinned commit" in err, err
    finally:
        verify_pinned.fetch_text = real_fetch

    # The schemas state the same grammar, wherever they describe an `entry` — never a copy that drifts.
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    found = []

    def entries(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "entry" and isinstance(v, dict) and v.get("type") == "string":
                    found.append((path + [k], v))
                entries(v, path + [k])
        elif isinstance(node, list):
            for i, v in enumerate(node):
                entries(v, path + [str(i)])

    for rel in ("schemas/plugin.schema.json", "schemas/pluginpack.schema.json"):
        with open(os.path.join(here, rel), "r", encoding="utf-8") as fh:
            entries(json.load(fh), [rel])
    # plugin: web, desktop. pluginpack: pack web, pack desktop, member web, member desktop.
    assert len(found) == 6, [".".join(p) for p, _ in found]
    for path, node in found:
        assert node.get("pattern") == ENTRY.pattern, ".".join(path)
        assert "absolute URL" not in node.get("description", ""), ".".join(path)

    # --- comparison: is it actually COMPARED, or merely computed? ---------------------------------
    entry = {"content_type": "vineyard:pluginpack", "version": "2.0.0"}
    cache = {URL: BUNDLE}
    assert bundle_mismatch(pack(), entry, ROOT, cache) is None, "consistent pack must pass"

    # The xeuledoc case: the manifest JSON was bumped, dist/ was not rebuilt.
    err = bundle_mismatch(pack(version="3.0.0"), {**entry, "version": "3.0.0"}, ROOT, cache)
    assert err and "bundle version '2.0.0'" in err and "manifest version '3.0.0'" in err, err
    assert "member" not in err, "the pack's own drift must not be reported as a member's"

    # The half nothing live exercises: a relicensed manifest over a stale bundle.
    err = bundle_mismatch(pack(license="MIT"), entry, ROOT, cache)
    assert err and "bundle license 'Apache-2.0'" in err and "manifest license 'MIT'" in err, err

    # And the same for a MEMBER, which is where xeuledoc's Apache-over-GPL actually sits.
    doc = pack()
    doc["plugins"][0]["license"] = "MIT"
    err = bundle_mismatch(doc, entry, ROOT, cache)
    assert err and err.startswith(f"member {MEMBER_ID}: "), err

    # A typepack has no code to check, and may not be turned into a fetch, or CI fails on packs
    # that are correct.
    assert bundle_mismatch({"version": "9"}, {"content_type": "vineyard:typepack"}, ROOT, {}) is None
    # A pack whose entry is 'inline' is refused before any fetch, naming the field to fix.
    err = bundle_mismatch({"platforms": {"web": {"entry": "inline"}}}, entry, ROOT, {})
    assert err and "'inline'" in err, err
    doc = pack()
    doc["plugins"][0]["platforms"] = {"web": {"runtime": "sandbox-js", "entry": "inline"}}
    err = bundle_mismatch(doc, entry, ROOT, cache)
    assert err and err.startswith(f"member {MEMBER_ID}: ") and "'inline'" in err, err

    print("bundle claim self-test: ok")


if __name__ == "__main__":
    main()
