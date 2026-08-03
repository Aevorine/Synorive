# @aevorine/synorive-mcp

**MCP server for [Synorive](https://github.com/Aevorine/Synorive)** — gives Claude Code
(or any MCP client) 24 tools to search your own local library and fact-check claims
against the open web.

Everything runs on your machine. Your files never leave it.

## What your agent can do with it

| Tool group | What it does |
|---|---|
| `synorive_search` | Semantic + keyword hybrid search over documents, source code, PDFs (by section), images (OCR), video (to the second), web archives |
| `synorive_ingest` / `analyze` | Feed a file, folder or URL into the library and analyse it |
| `synorive_similar` / `get_content` | Find near-duplicates; pull the full text of any indexed item |
| `synorive_timeline` / `graph` | Everything on a time axis; people/places/organisations and how they connect |
| `synorive_status` | What the engine is doing, which models are ready |

Plus web-research tools that search several engines at once, **actively hunt for
counter-evidence**, trace a claim to its earliest source, and return briefings where
every line is a verbatim quote with its source.

## Install

```bash
npm install -g @aevorine/synorive-mcp --registry=https://npm.pkg.github.com
```

> This package is published to **GitHub Packages**. To install it you need a `.npmrc` with
> `@aevorine:registry=https://npm.pkg.github.com` and a GitHub token that has `read:packages`.
> Prefer no setup at all? Clone the repo and point Claude Code at `mcp/dist/index.js` directly —
> see below.

## Connect to Claude Code

```bash
# from a global install
claude mcp add synorive -- synorive-mcp

# or straight from a clone, no registry needed
claude mcp add synorive -- node <repo>/mcp/dist/index.js
```

Then just ask: *"search my library for why vector search gets slow, and cross-check what you find."*

## Requires

The Synorive engine running locally — it ships inside the
[desktop app](https://github.com/Aevorine/Synorive/releases/latest) (Python runtime bundled,
nothing to install), or you can run it from source.

The server talks to `http://127.0.0.1:8731` by default; override with `SYNORIVE_ENGINE_URL`.

## License

AGPL-3.0-or-later. Full docs: <https://github.com/Aevorine/Synorive>
