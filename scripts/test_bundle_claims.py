#!/usr/bin/env python3
"""Self-test for verify_pinned's bundle check — the version/licence a pack's CODE declares.

Running it against the live catalog proves only what the live catalog happens to contain. Two
of nineteen packs drift today and both drift on `version`, so the licence half of the comparison
is exercised by nothing at all: deleting it would leave CI green while a GPL port kept shipping
an Apache notice, which is the case that motivated the check. The same is true of the two rules
that keep it from crying wolf — `min_app_version` must not read as `version`, and a member is
allowed to carry a different licence from its pack.

Offline: no network, no catalog. Run with: python scripts/test_bundle_claims.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_pinned import bundle_claims, bundle_mismatch, bundle_url  # noqa: E402

PACK_ID = "run.vineyard.pluginpacks.demo"
MEMBER_ID = "run.vineyard.plugins.demo"

# esbuild output, shortened: the member object first, the pack object last, both minified with
# bare keys — the shape every published pack actually has.
BUNDLE = (
    'var de={identifier:"%s",content_type:"vineyard:plugin",name:"Demo",version:"1.0.0",'
    'license:"GPL-3.0",platforms:{primary:"desktop",web:{runtime:"sandbox-js",entry:"inline"},'
    'desktop:{runtime:"sandbox-js",entry:"inline",min_app_version:"0.1.0"}}};'
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
    # 'inline' names no remote module: a builtin, nothing to fetch.
    inline = {"platforms": {"web": {"entry": "inline"}}}
    assert bundle_url(inline, inline, ROOT) is None
    assert bundle_url({}, {}, ROOT) is None

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

    # A typepack has no code to check, and a pack whose members are all inline has none to fetch —
    # neither may be turned into a fetch, or CI fails on packs that are correct.
    assert bundle_mismatch({"version": "9"}, {"content_type": "vineyard:typepack"}, ROOT, {}) is None
    assert bundle_mismatch({"platforms": {"web": {"entry": "inline"}}}, entry, ROOT, {}) is None

    print("bundle claim self-test: ok")


if __name__ == "__main__":
    main()
