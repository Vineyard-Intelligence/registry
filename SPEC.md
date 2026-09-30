# Vineyard registry specification

The contract between a pack author and the Vineyard catalog: what this repository publishes,
what a submission must contain, and what CI enforces before one is merged.

Author documentation — how to *write* a pack — lives at
[docs.vineyard.run/develop](https://docs.vineyard.run/develop/). This document covers only the
registry.

---

## 1. What the registry is

A **catalog index**. It stores pointers and card-level metadata. It never stores, serves, or
executes pack code — that stays in each author's own repository, and a client fetches it from
there at the pinned commit.

Three files are published, one per content kind:

```
GET https://registry.vineyard.run/registry/community-typepacks.json
GET https://registry.vineyard.run/registry/community-pluginpacks.json
GET https://registry.vineyard.run/registry/community-skillpacks.json
```

Each is a JSON array of entries, sorted by `identifier`, served with no build step and no
authentication. Consumers (the in-app marketplace and the documentation site's browser) fetch
one file per kind and render entirely client-side.

## 2. Content kinds

| `content_type` | What it is | Catalog |
|---|---|---|
| `vineyard:typepack` | Entity/edge type definitions (JSON, no code) | `community-typepacks.json` |
| `vineyard:pluginpack` | A bundle of one or more plugins (JS, sandboxed) | `community-pluginpacks.json` |
| `vineyard:plugin` | A single plugin — a pack of one | `community-pluginpacks.json` |
| `vineyard:skillpack` | An investigation playbook (text, no code, no permissions) | `community-skillpacks.json` |

## 3. Entry format

Every entry, of every kind, carries these:

| Field | Meaning |
|---|---|
| `identifier` | `<namespace>.<kind>.<name>` — the namespace is the author's OWN reverse-DNS prefix, two labels or more (`com.acme`, `io.github.someone`); `run.vineyard.*` is first-party. Globally unique across all three catalogs, and must equal the `identifier` in the pack document itself. |
| `content_type` | One of the four above. |
| `name`, `author`, `description` | What the card shows. |
| `repo` | `owner/name` of the content repository holding the pack. |
| `ref` | **Immutable commit SHA** (40-hex or 64-hex) of the release being listed. |
| `path` | Path to the pack document within `repo` at `ref`. |
| `version` | Human-readable mirror of the pinned document's `version`. |
| `verified` | **Derived, not submitted.** `build_registry.py` sets it from `verified-authors.json`: true when the identifier's namespace is claimed by the handle in `author`. Omit it — a value written in `packs/` is overwritten, not read. |
| `status` | Present only on a delisted pack. See §7. |

The remaining fields are **derived projections** of the full document, present so the browse page
can render without fetching every manifest. CI recomputes each of them from the pinned document
and rejects any that disagrees. The fields are — `platforms` / `scopes_summary` / `plugin_count` for
plugin packs, `categories` / `type_count` / `edge_count` for type packs, `applies_to` /
`section_count` / `requires` for skill packs. The normative field list is the JSON Schema:

- [`schemas/registry-plugin-entry.schema.json`](schemas/registry-plugin-entry.schema.json)
- [`schemas/registry-typepack-entry.schema.json`](schemas/registry-typepack-entry.schema.json)
- [`schemas/registry-skillpack-entry.schema.json`](schemas/registry-skillpack-entry.schema.json)

Detail is hydrated lazily from the content repo at the pinned commit:

```
https://cdn.jsdelivr.net/gh/{repo}@{ref}/{path}
```

## 4. `ref` must be a commit SHA

Tags and branches can be moved after review, so they are **rejected**.

Resolve a tag or branch to its commit with:

```
python scripts/resolve_ref.py owner/repo v1.2.0
```

### The approved-ref lists

Alongside each catalog the registry publishes every ref it has **ever** approved for that kind:

```
GET https://registry.vineyard.run/registry/approved-pluginpacks.json
```

```json
[{ "identifier": "...", "repo": "org/repo", "ref": "<sha>", "path": "...",
   "version": "1.0.0", "approved_at": "2026-08-10", "sha256": "<digest of the document>" }]
```

`sha256` is the digest of the pinned document's bytes. The app loads an installed pack only if its
pointer is on this list and the fetched document matches the digest. Because the list keeps every
ref ever approved, an older install stays valid after the pack is republished. The lists are
generated from the history of `packs/`; you never edit them.

## 5. Submitting a pack

**Add one file. Do not edit the catalogs.**

```
packs/<identifier>.json
```

One entry per file, named for its `identifier`; `content_type` decides which catalog it joins.
The three `registry/community-*.json` files are **generated** from `packs/` by
`scripts/build_registry.py` and rebuilt on merge — a hand edit to them is overwritten.

1. Fork this repository.
2. Pin an immutable `ref` (§4).
3. Add `packs/<identifier>.json` with your entry.
4. Open a pull request. CI validates it (§6).
5. After green CI and a human merge, the entry is live on the next registry fetch. There is no
   coupled app release.

To publish a new version of an existing pack, edit that pack's file in place with the new `ref`
and `version`.

## 6. What CI enforces

All blocking — a pull request cannot merge until every one passes.

| Check | Script |
|---|---|
| Filename equals `identifier`; `content_type` is known | `build_registry.py` |
| Entry validates against its registry-entry schema | `validate.py` |
| A namespace is used only by the author who owns it, and an author name is worn only inside its own namespaces | `validate.py` |
| `verified` reflects `verified-authors.json` and nothing else, whatever the submission said | `build_registry.py`, proven by `test_verified.py` |
| Every declared dependency — a Skill Pack's `requires`, a Plugin Pack's `typepacks` — names a pack that is in this catalog | `validate.py` |
| Identifier patterns still reject malformed shapes | `check_identifiers.py` |
| `ref` is an immutable commit SHA | `verify_pinned.py` |
| Every current catalog row appears in its approved-ref list, and a commit that was never published does not | `build_approved.py`, proven by `test_approved.py` |
| Pinned document is reachable, and its `identifier` / `content_type` / `version` match the entry | `verify_pinned.py` |
| Every summary field the entry carries — `scopes_summary`, `platforms`, `plugin_count`, `section_count`, `type_count`, `edge_count` — equals what the pinned document implies | `verify_pinned.py` |
| Every `io` type reference resolves to a type a **published** Type Pack defines, names its real owner, and that Type Pack is listed in the entry's `typepacks` | `check_typerefs.py` |
| A live pack declares no dependency on a delisted one, and a `status.replacement` names a live pack of the same kind | `validate.py` |
| The delisting rules still hold, checked against cases the live catalog does not contain | `test_delisting.py` |

There is no automated static analysis of pack code. A reviewer reads the bundle and weighs: scope
breadth against what the pack plausibly needs, `node:delete` usage, minified-only bundles,
secret-looking `params` keys, an unbuildable `native`/`subprocess` runtime, and namespace
ownership — a submission under a namespace you do not own is refused at review.

## 7. Delisting a pack

**Deleting the entry is not how a pack is taken down.** Removing the row only takes the pack off the
browse page; projects that already installed it keep loading it and are never told. So the row
**stays**, and gains a `status`:

```json
"status": {
  "state": "withdrawn",
  "reason": "Sent project data to an endpoint outside its declared allowlist.",
  "since": "2026-08-09",
  "replacement": "com.acme.pluginpacks.recon"
}
```

| State | Catalog | Already installed | Use it for |
|---|---|---|---|
| `deprecated` | Browsable, installable, badged | Loads normally; the analyst is told once per project open | A pack that is superseded, unmaintained, or being retired |
| `withdrawn` | Hidden from browse unless the project has it; install refused | **Not loaded**, and the reason is shown | A pack that turned out to be harmful, or whose content is gone |

`reason` is shown to analysts verbatim, so write it for them. `replacement` must name a live pack of
the same kind. Withdrawal is the operator's call, not an author's.

Before you delist:

- **A live pack may not depend on a delisted one.** CI names the dependants; fix them first, or
  delist them in the same pull request.
- **A withdrawn entry is not pin-verified**, so its content may be gone. A deprecated pack still
  loads for its users, so it is still held to its pin.

Removing the row entirely is reserved for an entry that was never usable in the first place (a
mistaken submission, a duplicate). Anything a client may have installed gets a `status`.

## 8. Not in scope

The registry carries no chain of custody, no provenance stamping, and no evidentiary integrity —
Vineyard is an OSINT tool, not a DFIR one.
