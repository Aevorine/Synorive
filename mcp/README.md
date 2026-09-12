# @aevorine/synorive-mcp

**MCP server for [Synorive](https://github.com/Aevorine/Synorive)** — gives Claude Code
(or any MCP client) 26 tools to search your own local library and fact-check claims
against the open web.

Everything runs on your machine. Your files never leave it.

## The 26 tools

All names are prefixed `synorive_`.

| Group | Tools |
|---|---|
| **Local library** (9) | `search` · `ingest` · `analyze` · `get_content` · `similar` · `timeline` · `graph` · `status` · `questions` |
| **Web research** (7) | `web_search` · `research` · `scholar` · `read_url` · `web_engines` · `verify` · `unified_search` |
| **Reverse search** (2) | `reverse_image` — where else this picture appears online · `reverse_video` — trace a clip to its source by matching several keyframes |
| **Literature** (4) | `scholar_review` — thematic review, extract-only · `scholar_table` — one metric across many papers · `citations` — co-citation to surface the foundational papers · `harvest` — bulk-fetch open-access full text, dry-run by default |
| **Verification & memory** (2) | `check_numbers` — check every figure against the source text · `memory` — what did I already look up on this topic? |
| **Local media** (2) | `compare` — what differs between two files · `chapters` — chapter list for a long video |

`search` is hybrid: semantic + keyword over documents, source code, PDFs (indexed by
section), images (OCR), video (down to the second) and saved web pages.

The web tools query several engines at once, **actively hunt for counter-evidence**,
trace a claim back to its earliest source, and return briefings where every line is a
verbatim quote with its source.

## What it will not do

Every result carries a trust breakdown and a source, and each tool description states
its own limits in the text the model reads — *"cannot judge whether a statement is
factually true"*, *"verbatim extraction is not paraphrase"*, *"counter-evidence exists
≠ the original claim is false"*.

Without that, an agent treats a content farm and an official spec as equally
authoritative and relays both in the same confident tone.

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

## If the tools stop working after you enable LAN pairing

Fixed in **0.1.11**. Enabling LAN pairing switches the engine to HTTPS, and every client —
this MCP server included — still spoke plain HTTP, so `synorive_*` tools would report the engine
as unreachable while the engine itself was perfectly healthy.

The engine now writes the scheme and its self-signed certificate path into `data/engine.json`,
and this client reads both. Nothing to configure; just make sure the engine is 0.1.11 or newer.

## Requires

The Synorive engine running locally — it ships inside the
[desktop app](https://github.com/Aevorine/Synorive/releases/latest) (Python runtime bundled,
nothing to install), or you can run it from source.

The server talks to `http://127.0.0.1:8731` by default; override with `SYNORIVE_ENGINE_URL`.

## License

AGPL-3.0-or-later. Full docs: <https://github.com/Aevorine/Synorive>
