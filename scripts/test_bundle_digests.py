#!/usr/bin/env python3
"""Self-test for the code digests on approved plugin rows (`bundle_sha256`).

The approved row's `sha256` covers the manifest. The app used to check that and then import the
module the manifest names straight from the CDN, unhashed, so the code that ran was whatever the
CDN served. Now each plugin row also records the digest of every module its document runs, and
the app refuses bytes that do not match, and refuses a pack whose map leaves one of its modules
out. Two failures follow from getting the map wrong, in opposite directions:

  * A map missing a module the app runs takes the whole pack down in every project, so the map
    must be all or nothing: a module that cannot be fetched means no map, not a shorter one.

  * A map built from bytes other than the approved document vouches for code nobody approved,
    so the document bytes must hash to the row's own digest first.

And the bytes hashed must be GitHub's at the pinned commit, not the CDN's: a digest taken from the
CDN only says the CDN served the same bytes twice, and records whatever it served first. The CDN copy
must match GitHub's or the build fails; a ref GitHub does not serve gets no digest at all, and a
current catalog row without its digests fails the build too, since the publish job commits whatever
the build wrote.

The live catalog exercises none of this: every current pack is one `dist/pack.mjs` that fetches fine
from both. Everything here is offline, against an in-memory GitHub and CDN.

Run: python scripts/test_bundle_digests.py
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_approved  # noqa: E402
from build_approved import (  # noqa: E402
    CDN,
    CODE_LIST,
    GITHUB_RAW,
    OUTPUTS,
    add_digests,
    bundle_digests,
    document_url,
    unverified_current,
)

FAILURES = []


def check(name, ok):
    print(("  ok  " if ok else "  FAIL") + f"  {name}")
    if not ok:
        FAILURES.append(name)


def sha(b):
    return hashlib.sha256(b).hexdigest()


REF = "a" * 40
REPO = "Vineyard-Intelligence/pluginpack-demo"
ROOT = f"{CDN}{REPO}@{REF}/"
GH = f"{GITHUB_RAW}{REPO}/{REF}/"
PACK = b"export default { identifier: 'run.vineyard.pluginpacks.demo' };\n"
OWN = b"export default { identifier: 'run.vineyard.plugins.demo_own' };\n"


def doc(pack_entry="dist/pack.mjs", members=({},)):
    return {
        "identifier": "run.vineyard.pluginpacks.demo",
        "content_type": "vineyard:pluginpack",
        "version": "1.0.0",
        "platforms": {"web": {"runtime": "sandbox-js", "entry": pack_entry}},
        "plugins": [
            {"identifier": f"run.vineyard.plugins.demo_{i}", "content_type": "vineyard:plugin", **m}
            for i, m in enumerate(members)
        ],
    }


def own(entry):
    return {"platforms": {"web": {"runtime": "sandbox-js", "entry": entry}}}


class Net:
    """url -> bytes, recording what was asked for. `files` is keyed by path in the demo pin and
    served identically by GitHub and the CDN; `gh` and `cdn` override one side."""

    def __init__(self, files, gh=None, cdn=None, root=ROOT, gh_root=GH):
        self.files = {}
        for path, data in files.items():
            self.files[gh_root + path] = data
            self.files[root + path] = data
        for path, data in (gh or {}).items():
            self.files[gh_root + path] = data
        for path, data in (cdn or {}).items():
            self.files[root + path] = data
        self.asked = []

    def add(self, other):
        self.files.update(other.files)
        return self

    def __call__(self, url):
        self.asked.append(url)
        return self.files.get(url)


ROW = {"identifier": "run.vineyard.pluginpacks.demo", "repo": REPO, "ref": REF, "path": "plugins/demo.manifest.json"}


def digests_for(document, files, digest=None, raw=None, gh=None, cdn=None):
    data = raw if raw is not None else json.dumps(document).encode()
    net = Net(files, gh, cdn)
    errors = []
    return bundle_digests(ROW, digest or sha(data), data, net, errors), net, errors


print("every module the document runs, keyed as the app resolves it")
got, net, errors = digests_for(doc(members=({}, {})), {"dist/pack.mjs": PACK})
check("one shared module", got == {"dist/pack.mjs": sha(PACK)})
check(
    "hashed from GitHub at the pinned commit, and the CDN copy fetched to compare, once each",
    net.asked == [GH + "dist/pack.mjs", ROOT + "dist/pack.mjs"] and errors == [],
)
got, _, _ = digests_for(doc(members=({}, own("dist/own.mjs"))), {"dist/pack.mjs": PACK, "dist/own.mjs": OWN})
check("a member's own module too", got == {"dist/own.mjs": sha(OWN), "dist/pack.mjs": sha(PACK)})
got, _, _ = digests_for(doc("inline", members=({}, own("dist/own.mjs"))), {"dist/own.mjs": OWN})
check("an inline pack entry names no module", got == {"dist/own.mjs": sha(OWN)})
got, net, _ = digests_for(doc("inline"), {})
check("a document that runs nothing gets an empty map", got == {} and net.asked == [])
got, _, _ = digests_for(
    {"identifier": "run.vineyard.plugins.lone", "content_type": "vineyard:plugin", **own("dist/pack.mjs")},
    {"dist/pack.mjs": PACK},
)
check("a lone plugin document", got == {"dist/pack.mjs": sha(PACK)})

print("all or nothing")
got, _, _ = digests_for(doc(members=({}, own("dist/own.mjs"))), {"dist/pack.mjs": PACK})
check("one module that cannot be fetched: no map at all", got is None)
for entry, why in [
    ("https://cdn.jsdelivr.net/gh/someone/x@main/evil.mjs", "an absolute url"),
    ("../../someone/x@main/evil.mjs", "a dot-dot escape"),
    ("/gh/someone/x@main/evil.mjs", "a leading slash"),
]:
    got, net, _ = digests_for(doc(members=({}, own(entry))), {"dist/pack.mjs": PACK})
    check(f"an entry outside the pin ({why}): no map, nothing fetched for it", got is None and len(net.asked) == 0)

print("GitHub's bytes, never the CDN's")
got, _, errors = digests_for(doc(), {}, cdn={"dist/pack.mjs": PACK})
check("GitHub does not serve the module and the CDN does: no map, no CDN-only digest", got is None and errors == [])
got, _, errors = digests_for(doc(), {}, gh={"dist/pack.mjs": PACK}, cdn={"dist/pack.mjs": OWN})
check("the CDN serves other bytes: the digest is still GitHub's", got == {"dist/pack.mjs": sha(PACK)})
check(
    "...and the difference is a build error, naming the CDN url",
    len(errors) == 1 and (ROOT + "dist/pack.mjs") in errors[0] and "other than GitHub" in errors[0],
)
got, _, errors = digests_for(doc(), {}, gh={"dist/pack.mjs": PACK})
check(
    "the CDN copy cannot be fetched: GitHub's digest, no error (a client cannot load it either)",
    got == {"dist/pack.mjs": sha(PACK)} and errors == [],
)

print("only from the approved document")
document = doc()
data = json.dumps(document).encode()
got, net, _ = digests_for(document, {"dist/pack.mjs": PACK}, digest=sha(b"something else"), raw=data)
check("document bytes that do not match the row's digest: no map, no module fetched", got is None and net.asked == [])
got, _, _ = digests_for(None, {}, raw=b"not json")
check("a document that is not JSON", got is None)
got, _, _ = digests_for(None, {}, raw=b"[1, 2]")
check("a document that is not an object", got is None)
check("no document bytes at all", bundle_digests(ROW, sha(data), None, Net({})) is None)

print("the build writes them on plugin rows only, and reuses what is committed")
plugin_doc = json.dumps(doc()).encode()
type_row = {"identifier": "run.vineyard.typepacks.demo", "repo": "o/typepack", "ref": REF, "path": "t.json"}
gone_row = {"identifier": "run.vineyard.pluginpacks.gone", "repo": "o/gone", "ref": REF, "path": "p.json"}
TYPE_ROOT, TYPE_GH = f"{CDN}o/typepack@{REF}/", f"{GITHUB_RAW}o/typepack/{REF}/"
seen = {out: {} for out in OUTPUTS}
seen[CODE_LIST]["p"] = dict(ROW)
seen[CODE_LIST]["gone"] = dict(gone_row)
seen["registry/approved-typepacks.json"]["t"] = dict(type_row)
net = Net({ROW["path"]: plugin_doc, "dist/pack.mjs": PACK}).add(
    Net({"t.json": b'{"types": []}'}, root=TYPE_ROOT, gh_root=TYPE_GH)
)
errors = add_digests(seen, {}, {}, net)
p = seen[CODE_LIST]["p"]
check("a plugin row gets its document digest", p.get("sha256") == sha(plugin_doc))
check("and the digest of its code", p.get("bundle_sha256") == {"dist/pack.mjs": sha(PACK)})
check(
    "the document was fetched once for both, from GitHub and the CDN",
    net.asked.count(GH + ROW["path"]) == 1 and net.asked.count(document_url(ROW)) == 1,
)
check("no problems when the two agree", errors == [])
t = seen["registry/approved-typepacks.json"]["t"]
check("a type pack row gets a document digest", bool(t.get("sha256")))
check("and no code digests: it runs no code", "bundle_sha256" not in t)
g = seen[CODE_LIST]["gone"]
check("an unfetchable document gets neither", "sha256" not in g and "bundle_sha256" not in g)

seen = {out: {} for out in OUTPUTS}
seen[CODE_LIST]["p"] = dict(ROW)
errors = add_digests(seen, {}, {}, Net({"dist/pack.mjs": PACK}, cdn={ROW["path"]: plugin_doc}))
check(
    "a document only the CDN serves gets no digest, document or code",
    "sha256" not in seen[CODE_LIST]["p"] and "bundle_sha256" not in seen[CODE_LIST]["p"] and errors == [],
)

seen = {out: {} for out in OUTPUTS}
seen[CODE_LIST]["p"] = dict(ROW)
errors = add_digests(
    seen, {}, {}, Net({"dist/pack.mjs": PACK}, gh={ROW["path"]: plugin_doc}, cdn={ROW["path"]: b'{"x": 1}'})
)
check(
    "a CDN document other than GitHub's: GitHub's digest, and a build error",
    seen[CODE_LIST]["p"].get("sha256") == sha(plugin_doc) and len(errors) == 1 and document_url(ROW) in errors[0],
)

seen = {out: {} for out in OUTPUTS}
seen[CODE_LIST]["p"] = dict(ROW)
net = Net({})
errors = add_digests(seen, {document_url(ROW): sha(plugin_doc)}, {document_url(ROW): {"dist/pack.mjs": sha(PACK)}}, net)
check(
    "committed digests are reused without a fetch",
    net.asked == [] and errors == [] and seen[CODE_LIST]["p"]["bundle_sha256"] == {"dist/pack.mjs": sha(PACK)},
)

seen = {out: {} for out in OUTPUTS}
seen[CODE_LIST]["p"] = dict(ROW)
add_digests(seen, {document_url(ROW): sha(plugin_doc)}, {}, Net({ROW["path"]: plugin_doc, "dist/pack.mjs": PACK}))
check(
    "a committed document digest with no code digests yet: the document is read again for its entries",
    seen[CODE_LIST]["p"].get("bundle_sha256") == {"dist/pack.mjs": sha(PACK)},
)

seen = {out: {} for out in OUTPUTS}
seen[CODE_LIST]["p"] = dict(ROW)
errors = add_digests(
    seen, {document_url(ROW): sha(plugin_doc)}, {}, Net({ROW["path"]: b'{"tampered": true}', "dist/pack.mjs": PACK})
)
check(
    "...and if GitHub's document is not the committed one, no code digests are recorded",
    "bundle_sha256" not in seen[CODE_LIST]["p"] and seen[CODE_LIST]["p"]["sha256"] == sha(plugin_doc),
)
check(
    "...and the build fails: clients have been accepting a document the commit does not hold",
    len(errors) == 1 and "committed sha256" in errors[0],
)

print("a current row the app would run on membership alone fails the build")
KEY = ("run.vineyard.pluginpacks.demo", REPO, REF, ROW["path"])
OLD_KEY = ("run.vineyard.pluginpacks.demo", REPO, "b" * 40, ROW["path"])
TYPE_KEY = ("run.vineyard.typepacks.demo", "o/typepack", REF, "t.json")


def gate(plugin_row, old_row=None, type_row=None):
    seen = {out: {} for out in OUTPUTS}
    seen[CODE_LIST][KEY] = {**ROW, **plugin_row}
    current = {(CODE_LIST, KEY)}
    if old_row is not None:
        seen[CODE_LIST][OLD_KEY] = {**ROW, "ref": "b" * 40, **old_row}
    if type_row is not None:
        seen["registry/approved-typepacks.json"][TYPE_KEY] = {"identifier": TYPE_KEY[0], "ref": REF, **type_row}
        current.add(("registry/approved-typepacks.json", TYPE_KEY))
    return unverified_current(seen, current)


check("a current plugin row with both digests passes", gate({"sha256": "d", "bundle_sha256": {}}) == [])
check("without code digests it fails", any("no code digests" in m for m in gate({"sha256": "d"})))
check("without a document digest it fails", any("no document digest" in m for m in gate({})))
check(
    "a HISTORICAL row without digests does not (a ref GitHub no longer serves stays on membership)",
    gate({"sha256": "d", "bundle_sha256": {}}, old_row={}) == [],
)
check("a current type pack row needs a document digest only", gate({"sha256": "d", "bundle_sha256": {}}, type_row={"sha256": "t"}) == [])
check(
    "and fails without one",
    any("typepacks.demo" in m for m in gate({"sha256": "d", "bundle_sha256": {}}, type_row={})),
)

print("the workflow token goes to GitHub only")
sent = []


class Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"x"


real_urlopen = build_approved.urllib.request.urlopen
build_approved.urllib.request.urlopen = lambda req, timeout=None: (sent.append(req), Resp())[1]
os.environ["GITHUB_TOKEN"] = "test-token-not-a-secret"
try:
    build_approved.fetch_bytes(GH + "dist/pack.mjs")
    build_approved.fetch_bytes(ROOT + "dist/pack.mjs")
finally:
    build_approved.urllib.request.urlopen = real_urlopen
    del os.environ["GITHUB_TOKEN"]
check("sent to raw.githubusercontent.com", sent[0].get_header("Authorization") == "token test-token-not-a-secret")
check("never to the CDN", sent[1].get_header("Authorization") is None)
# What urllib's own redirect handling would send next, for a GitHub url that redirects elsewhere.
moved = build_approved.urllib.request.HTTPRedirectHandler().redirect_request(
    sent[0], None, 302, "Found", {}, "https://elsewhere.example/dist/pack.mjs"
)
check("and not carried onto a redirect", moved is not None and moved.get_header("Authorization") is None)

print()
if FAILURES:
    print(f"::error::{len(FAILURES)} code digest check(s) failed: {', '.join(FAILURES)}")
    raise SystemExit(1)
print("code digests: all checks passed")
