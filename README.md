# Vineyard registry

The **catalog index** for the Vineyard ecosystem. This repository is the place packs are
*listed*; the packs themselves live in their own content repositories.

Published at **https://registry.vineyard.run/** (GitHub Pages). The documentation site and the
in-app marketplace fetch these index files over XHR — this registry stores **metadata only** and
never hosts or executes plugin code. There is **no build step**: the JSON you see is what ships.

## Layout

| Path | Purpose |
| --- | --- |
| `registry/community-typepacks.json` | Index of Type Packs (the PR target to list one) |
| `registry/community-plugins.json` | Index of Plugin Packs |
| `schemas/*.schema.json` | JSON Schemas for packs and registry entries |
| `scripts/validate.py` | Validates the index entries against the schemas (runs in CI) |

## Endpoints

```
GET https://registry.vineyard.run/registry/community-typepacks.json
GET https://registry.vineyard.run/registry/community-plugins.json
```

Each entry carries the card-level summary (name, author, counts, scopes summary) **plus** a
pointer to the pack's content repo (`repo` + release `ref` + in-repo `path`). Consumers render the
catalog from these two files and hydrate full pack detail straight from the content repo via the
jsDelivr CDN, e.g. `https://cdn.jsdelivr.net/gh/{repo}@{ref}/{path}`. Nothing is vendored here.

## Two contribution flows

- **Update a pack's content** → PR to the content repo
  ([`typepack-basic`](https://github.com/Vineyard-Intelligence/typepack-basic),
  [`pluginpack-chaos`](https://github.com/Vineyard-Intelligence/pluginpack-chaos), …).
- **Add a pack to the catalog** → PR here, adding one entry to the relevant `community-*.json`
  that points at the content repo (`repo`), a release tag (`ref`), and the in-repo `path`.
  CI (`.github/workflows/validate.yml`) validates the entry against `schemas/registry-*-entry.schema.json`.

> The index files under `registry/` are the source of truth **and** are served directly, so a
> catalog PR is reviewable as a plain diff — no generated artifact to regenerate.

## License

MIT
