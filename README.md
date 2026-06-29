# Vineyard registry

The **catalog index** for the Vineyard ecosystem. This repository is the place packs are
*listed*; the packs themselves live in their own content repositories.

Published at **https://registry.vineyard.run/** (GitHub Pages). The documentation site and the
in-app marketplace fetch the merged catalog over XHR — this registry stores **metadata only** and
never hosts or executes plugin code.

## Layout

| Path | Purpose |
| --- | --- |
| `registry/community-typepacks.json` | Index of Type Packs (the PR target to list one) |
| `registry/community-plugins.json` | Index of Plugin Packs |
| `registry/registry.json` | Merged, enriched catalog the sites actually fetch |
| `schemas/*.schema.json` | JSON Schemas for packs and registry entries |
| `scripts/build_registry.py` | Regenerates `registry/registry.json` from the index + pack content |
| `scripts/validate.py` | Validates the index entries against the schemas (runs in CI) |

## Endpoints

```
GET https://registry.vineyard.run/registry/registry.json
GET https://registry.vineyard.run/registry/community-typepacks.json
GET https://registry.vineyard.run/registry/community-plugins.json
```

## Two contribution flows

- **Update a pack's content** → PR to the content repo
  ([`typepack-basic`](https://github.com/Vineyard-Intelligence/typepack-basic),
  [`pluginpack-chaos`](https://github.com/Vineyard-Intelligence/pluginpack-chaos), …).
- **Add a pack to the catalog** → PR here, adding one entry to the relevant `community-*.json`
  that points at the content repo (`repo`), a release tag (`ref`), and the in-repo `path`.
  CI validates the entry against `schemas/registry-*-entry.schema.json`.

## How `registry.json` is built

`registry/registry.json` is generated from the index entries plus each pack's content (fetched at
the pinned `ref`). Entry detail (full Type Pack / Plugin Pack documents) is **not** vendored here —
clients hydrate it straight from the content repos at `repo@ref/path`.

> Note: the index files under `registry/` are the source of truth **and** are served directly, so
> a catalog PR is reviewable as a plain diff.

## License

MIT
