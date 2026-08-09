# Vineyard registry

The **catalog index** for the Vineyard ecosystem. This repository is the place packs are
*listed*; the packs themselves live in their own content repositories.

Published at **https://registry.vineyard.run/** (GitHub Pages). The documentation site and the
in-app marketplace fetch these index files over XHR — this registry stores **metadata only** and
never hosts or executes plugin code. The catalogs are generated from `packs/` and committed, so
there is nothing for a client to build or resolve: the JSON you see is what ships.

## Layout

| Path | Purpose |
| --- | --- |
| `packs/<identifier>.json` | **The source.** One file per pack — this is what a submission adds |
| `registry/community-typepacks.json` | Published index of Type Packs (**generated**) |
| `registry/community-pluginpacks.json` | Published index of Plugin Packs (**generated**) |
| `registry/community-skillpacks.json` | Published index of Skill Packs (**generated**) |
| `schemas/*.schema.json` | JSON Schemas for packs and registry entries |
| `verified-authors.json` | Who may show the verified badge, and the namespaces each owns. **Operator-owned** — never edited by a submission |
| `scripts/build_registry.py` | Builds the three catalogs from `packs/` |
| `scripts/validate.py` | Validates each submission against the schemas (runs in CI) |
| [`SPEC.md`](SPEC.md) | The registry contract — entry format, pinning rule, what CI enforces |

## Endpoints

```
GET https://registry.vineyard.run/registry/community-typepacks.json
GET https://registry.vineyard.run/registry/community-pluginpacks.json
GET https://registry.vineyard.run/registry/community-skillpacks.json
```

Each entry carries the card-level summary (name, author, counts, scopes summary) **plus** a
pointer to the pack's content repo (`repo` + **immutable commit SHA** `ref` + in-repo `path`). Consumers render the
catalog from these three files and hydrate full pack detail straight from the content repo via the
jsDelivr CDN, e.g. `https://cdn.jsdelivr.net/gh/{repo}@{ref}/{path}`. Nothing is vendored here.

## Two contribution flows

- **Update a pack's content** → PR to the content repo
  ([`typepack-basic`](https://github.com/Vineyard-Intelligence/typepack-basic),
  [`pluginpack-chaos`](https://github.com/Vineyard-Intelligence/pluginpack-chaos),
  [`skillpack-account-identity-pivoting`](https://github.com/Vineyard-Intelligence/skillpack-account-identity-pivoting), …).
- **Add a pack to the catalog** → PR here, adding **one file** `packs/<identifier>.json` that
  points at the content repo (`repo`), the **immutable commit SHA** (`ref`) of the release you are
  submitting, and the in-repo `path`. Resolve the SHA with
  `python scripts/resolve_ref.py owner/repo <tag-or-branch>` — **tags and branches are mutable
  (re-pointable to other code) and are rejected**, so the catalog can never serve code other than
  what was reviewed at that commit.
  CI (`.github/workflows/validate.yml`) validates each entry against `schemas/registry-*-entry.schema.json`,
  re-fetches the pinned commit (`scripts/verify_pinned.py`) to confirm the document there matches
  the entry's identity, and scans the bytes the pack actually ships (`scripts/scan.py`).

> **Do not edit `registry/community-*.json`.** They are built from `packs/` by
> `scripts/build_registry.py` and rebuilt on merge, so a hand edit is overwritten. One file per
> pack is what keeps concurrent submissions from conflicting, stops a diff from reaching another
> author's pinned `ref`, and turns a duplicate identifier into a path collision instead of a check
> somebody has to remember to run. See [`SPEC.md`](SPEC.md).

## License

MIT
