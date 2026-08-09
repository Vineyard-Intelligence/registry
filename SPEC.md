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
| `verified` | Operator-set. Backed by `verified-authors.json`; a submission that asserts it is rejected. |

The remaining fields are **derived projections** of the full document, present so the browse page
can render without fetching every manifest. CI recomputes each of them from the pinned document
and rejects any that disagrees — the permission summary on a card is a statement of fact, not a
description. The fields are — `platforms` / `scopes_summary` / `plugin_count` for
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

Tags and branches are **rejected**. Both can be re-pointed at different code after review, which
would let the catalog serve bytes nobody approved. A commit SHA cannot; a later force-push in the
content repo does not affect an existing pin.

Resolve a tag or branch to its commit with:

```
python scripts/resolve_ref.py owner/repo v1.2.0
```

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

Why one file rather than an append to a shared array: two open submissions never touch the same
path, a diff that adds a file cannot alter another author's pinned `ref`, and a duplicate
identifier becomes a path collision rather than a check somebody has to remember to run.

## 6. What CI enforces

All blocking — a pull request cannot merge until every one passes.

| Check | Script |
|---|---|
| Filename equals `identifier`; `content_type` is known | `build_registry.py` |
| Entry validates against its registry-entry schema | `validate.py` |
| A namespace is used only by the author who owns it, an author name is worn only inside its own namespaces, and `verified` is backed by `verified-authors.json` | `validate.py` |
| Every declared dependency — a Skill Pack's `requires`, a Plugin Pack's `typepacks` — names a pack that is in this catalog | `validate.py` |
| Identifier patterns still reject malformed shapes | `check_identifiers.py` |
| `ref` is an immutable commit SHA | `verify_pinned.py` |
| Pinned document is reachable, and its `identifier` / `content_type` / `version` match the entry | `verify_pinned.py` |
| Every summary field the entry carries — `scopes_summary`, `platforms`, `plugin_count`, `section_count`, `type_count`, `edge_count` — equals what the pinned document implies | `verify_pinned.py` |
| Every `io` type reference resolves to a type a **published** Type Pack defines, names its real owner, and that Type Pack is listed in the entry's `typepacks` | `check_typerefs.py` |
| Plugin bundles (web **and** desktop) contain no `eval`, computed `import()`, `importScripts`, credential-store access, or egress outside `ctx.net` | `scan.py` |
| No member declares a `native`/`subprocess` desktop runtime, which ships code the scanner cannot read | `scan.py` |
| Skill pack text does not try to override the agent's instructions or route around analyst review | `scan.py` |

`scan.py --self-test` proves the rules still fire on known-bad samples before they are trusted on
real packs.

Review beyond this is human. Scope breadth, `node:delete` usage, and minified-only bundles are
things a reviewer weighs; none of them are automatic rejections. **Namespace ownership is one of
them** — no pattern can tell whether you control `com.acme`, so a submission under a namespace
you do not own is refused at review.

## 7. Not in scope

The registry carries no chain of custody, no provenance stamping, and no evidentiary integrity —
Vineyard is an OSINT tool, not a DFIR one. Commit pinning is supply-chain hygiene: it guarantees a
client runs the bytes that were reviewed, and nothing more.

## License

MIT
