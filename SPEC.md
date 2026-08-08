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

Each is a JSON array of entries, served with no build step and no authentication. Consumers (the
in-app marketplace and the documentation site's browser) fetch one file per kind and render
entirely client-side.

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
| `identifier` | Reverse-DNS id, globally unique across all three catalogs. Must equal the `identifier` in the pack document itself. |
| `content_type` | One of the four above. |
| `name`, `author`, `description` | What the card shows. |
| `repo` | `owner/name` of the content repository holding the pack. |
| `ref` | **Immutable commit SHA** (40-hex or 64-hex) of the release being listed. |
| `path` | Path to the pack document within `repo` at `ref`. |
| `version` | Human-readable mirror of the pinned document's `version`. |

The remaining fields are **derived projections** of the full document, present so the browse page
can render without fetching every manifest — `platforms` / `scopes_summary` / `plugin_count` for
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

1. Fork this repository.
2. Pin an immutable `ref` (§4).
3. Append **one** entry to the catalog matching your `content_type` — and nothing else in the file.
4. Open a pull request. CI validates it (§6).
5. After green CI and a human merge, the entry is live on the next registry fetch. There is no
   coupled app release.

To publish a new version of an existing pack, update that entry's `ref` and `version` in place.

## 6. What CI enforces

All blocking — a pull request cannot merge until every one passes.

| Check | Script |
|---|---|
| Entry validates against its registry-entry schema | `validate.py` |
| `ref` is an immutable commit SHA | `verify_pinned.py` |
| Pinned document is reachable, and its `identifier` / `content_type` / `version` / type counts match what the entry claims | `verify_pinned.py` |
| Plugin bundles contain no `eval`, computed `import()`, `importScripts`, credential-store access, or egress outside `ctx.net` | `scan.py` |
| Skill pack text does not try to override the agent's instructions or route around analyst review | `scan.py` |

`scan.py --self-test` proves the rules still fire on known-bad samples before they are trusted on
real packs.

Review beyond this is human. Scope breadth, `node:delete` usage, secret-looking `params` keys, and
minified-only bundles are things a reviewer weighs; none of them are automatic rejections.

## 7. Not in scope

The registry carries no chain of custody, no provenance stamping, and no evidentiary integrity —
Vineyard is an OSINT tool, not a DFIR one. Commit pinning is supply-chain hygiene: it guarantees a
client runs the bytes that were reviewed, and nothing more.

## License

MIT
