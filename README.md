<div align="center">

# Synorive

**Local-first semantic search for every file you own — plus a fact-checked web researcher.**

Search your documents, source code, PDFs, images and videos by **meaning**, not by filename.
Then search the open web across many engines, hunt for counter-evidence, and get a briefing
where **every single line is a verbatim quote with its source**.

Runs fully offline. Your files never leave your machine.
Ships **24 MCP tools** for Claude Code.

**English** · [简体中文](docs/i18n/README.zh-CN.md) · [Français](docs/i18n/README.fr.md) · [Español](docs/i18n/README.es.md) · [Русский](docs/i18n/README.ru.md) · [العربية](docs/i18n/README.ar.md)

[![Download](https://img.shields.io/badge/download-v0.1.5-0F4C8C)](https://github.com/Aevorine/Synorive/releases/latest)
[![License](https://img.shields.io/badge/license-AGPL--3.0-1E9E76)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Android-0F4C8C)](https://github.com/Aevorine/Synorive/releases/latest)
[![Engine](https://img.shields.io/badge/engine-Python%203.13%20%2B%20FastAPI-1E9E76)](engine)
[![Desktop](https://img.shields.io/badge/desktop-Electron%2041%20%2B%20React%2019-0F4C8C)](apps/desktop)
[![Offline](https://img.shields.io/badge/works-fully%20offline-1E9E76)](#design-decisions-that-are-not-obvious)
[![MCP](https://img.shields.io/badge/MCP-24%20tools-C8871B)](mcp)

### ⬇️ Download

| | |
|---|---|
| **Windows installer** | [`Synorive-Setup-0.1.5.exe`](https://github.com/Aevorine/Synorive/releases/latest) — Python runtime bundled, **in-app auto-update** |
| **Windows portable** | [`Synorive-0.1.5-portable.exe`](https://github.com/Aevorine/Synorive/releases/latest) — no install; auto-update not available for this form |
| **Android** | [`app-release.apk`](https://github.com/Aevorine/Synorive/releases/latest) — thin client, talks to the engine on your PC over LAN |

**No Python installation required.** The interpreter and every engine dependency ship inside the
installer, so a fresh machine with no Python, no pip access and no internet still starts up.

</div>

![Synorive research workbench — multi-engine web search with per-source trust ranking](docs/screenshots/research-light.png)

<div align="center"><sub>

The research workbench: multi-engine search, per-source trust ranking, and an excluded-results
drawer that always tells you *why* something was filtered out.
Dark theme: [screenshot](docs/screenshots/research-dark.png)

</sub></div>

---

## What it does

| | |
|---|---|
| 🔍 | **Semantic search over your own files** — documents, source code, PDFs (indexed section by section), images (OCR), video (down to the second), saved web pages |
| 🖼 | **Cross-modal search** — find an image by describing it, or find *which video a frame came from and at what second* |
| 🌐 | **Multi-engine web search** — Bing / Baidu / 360 / Mojeek / Wikipedia / Reddit, plus Google and DuckDuckGo through a self-hosted SearXNG |
| 🛡 | **Actively hunts for counter-evidence** — searches for debunkings, traces a claim back to its earliest source, flags retracted papers |
| 📋 | **Extract-only briefings** — every line is a verbatim quote with its source. Conflicting claims are shown **side by side, undecided** |
| 🔌 | **24 MCP tools for Claude Code** — let your agent search your own library and verify claims for you |
| 🔒 | **Privacy fence** — web search and cloud inference are two *separate* switches, because one leaks *what you ask* and the other leaks *what you have* |
| ❓ | **Ask a question, get quoted answers** — the answer is assembled *only* from sentences that already exist in your files, each with its source. Nothing is generated, nothing is reworded |
| 📝 | **One-click draft** — pick the results you want, get a Markdown / plain-text / PDF draft with numbered citations and clickable anchors |
| ⚡ | **Searchable in seconds** — a new file is keyword-searchable the moment it is chunked; semantic indexing backfills in the background instead of making you wait |
| 🎚 | **Ranking you control** — eight sliders (semantic, keyword, recency, source trust, popularity, title hits, result diversity, short-fragment penalty), five presets, and you can save your own |
| 📖 | **Reading comfort** — a paper theme, three density scales, and a main input area big enough for a long question |

**Keywords:** local semantic search · offline AI search engine · multimodal RAG · personal knowledge base ·
document search · vector search · hybrid search · fact checking · misinformation detection ·
MCP server · Claude Code · SQLite FTS5 · sqlite-vec · HNSW · OCR · video search · Chinese NLP ·
Electron desktop app · privacy-first · self-hosted · offline RAG · desktop search ·
question answering with citations · extract-only · no hallucination · reranking · embeddings ·
local LLM alternative · no API key · air-gapped search

---

## Why another search tool?

Most "search your files" tools stop at keyword matching, and most "AI research" tools hand you a
fluent summary you cannot verify. Synorive refuses both:

- **Retrieval is measured, not claimed.** Every performance number below was benchmarked on real
  data — including the two that **did not** reach their targets. They are listed with the reason
  instead of being quietly dropped.
- **Nothing is silently discarded.** Results filtered out as low quality go into an "excluded"
  drawer with the reason, one click to bring them back.
- **It never tells you something is false.** It finds who disputes a claim and shows you both
  sides. Judging truth is not a capability it has, and pretending otherwise would be the most
  dangerous thing it could do.

---

## What works today

Phases 1–3, 5 and 8 are complete. **The application is genuinely usable right now.**

### Searching your own files

- Drop in a folder mixing documents, code, images and video → indexed concurrently in the
  background, the UI never freezes
- Semantic search over documents in Chinese and English — describe the content, no need to
  remember the filename
- Query syntax straight in the search box: `type:pdf date:last7days -draft "exact phrase"`
- Search text *inside* images (OCR, measured at 100% character coverage)
- Find similar images from one image, or find **which video a frame came from and at what second**
- Search a spoken line and jump straight to 3 m 24 s of the video
- Papers indexed by section (Abstract / Method / Results); hits are labelled `page 2 · Background`
- Ask a PDF **"what questions can you answer?"** and click a question to expand the source passage

### Searching the web and checking it

- Many engines concurrently (cn.bing / Baidu / 360 / Mojeek / Wikipedia); with a self-hosted
  SearXNG, **Google and DuckDuckGo work too**
- Deep research **reads the first round before deciding what to ask next**, then searches again
- A Chinese query automatically gets an English variant, routed to the engines with better English
  coverage — primary sources are usually in English
- Actively reverse-searches for "debunked / disputed / retracted / controversy" and puts the
  counter-evidence in front of you
- Traces a claim **back to its earliest source**; a dozen sites publishing the same story within
  two days gets flagged as a syndication burst
- A cited paper **that has been retracted is flagged in red** (via OpenAlex)
- Five academic sources merged by DOI, with citation counts and PDF links

### Using it from Claude Code

After `claude mcp add synorive`, Claude Code can search your library, verify a claim, and compare
**what you have** against **what the web says** — in the same answer.

### How-to guides

- Desktop, including every keyboard shortcut — [`docs/操作指南-电脑版.md`](docs/操作指南-电脑版.md)
- Android phone / tablet, step by step — [`docs/操作指南-安卓版.md`](docs/操作指南-安卓版.md)

The full technical design and the 76-item feature menu live in
[`docs/00-技术方案.md`](docs/00-技术方案.md). Every performance target, how it counts as measured,
and **which ones are still untested**, are declared in code at
[`engine/synorive/metrics.py`](engine/synorive/metrics.py); the benchmark scripts are in
[`engine/tests/`](engine/tests) (`bench_g_series` / `bench_research` / `bench_ingest_stages`).

---

## Benchmarks (measured, not estimated)

| | Measured | Target |
|---|---|---|
| Cold start to searchable | **1.30 s** | ≤2.0 s ✅ |
| First results @ 102k chunks | P50 **45 ms** / P95 **186 ms** | ≤80 / ≤200 ✅ |
| Full retrieval @ 102k chunks | P95 **373 ms** | ≤500 ✅ |
| Scroll frame rate | **59.9 fps** | ≥55 ✅ |
| Disk for 100k chunks | **374 MB** | ≤3 GB ✅ |
| Resume after interruption | 54/54 skipped, 1067× faster | ✅ |
| Image ingest (OCR deferred) | **19.35 images/s** | — |
| Image OCR (background pass) | 1.2–1.5 images/s | ← bound by the Python GIL |
| Video fast path | **88.6× realtime** | — |
| Video with transcription | 5.97× realtime | ≥6 ⚠️ |
| Text embedding (single worker) | **19.8 chunks/s** (was 12.6; batch 16→8 gave **1.57×**) | ⚠️ see below |
| Deep research briefing P95 | **8.29 s** (was 23.79 s; a global deadline cut it **65%**) | ≤8.0 ⚠️ short by 0.29 s |
| Warm cache hit | P50 **17.6 ms** | ≤200 ✅ |
| Drop-in to searchable | P95 **0.8 s** | ≤3.0 ✅ |
| Quick web search P95 | **2.4 s** | ≤3.0 ✅ |

⚠️ Ingest throughput is bound by this machine (i5-1155G7, no discrete GPU). Stage-by-stage timing
shows **embedding alone is 97.7%** of the cost; the other five stages together are 2.3%. Going
faster from here means a quantised model or a GPU, not more tuning.

⚠️ The deep-research P95 dropped from 23.79 s to 8.29 s, but **the cost was that 20 of 20 runs
skipped the second follow-up round**. The number and its price have to be read together.

See the `how` field of A6/A7 in [`engine/synorive/metrics.py`](engine/synorive/metrics.py) — the
target column there says "⚠️ to be re-set" instead of a number, and that is deliberate: a target
everyone knows is unreachable is worse than admitting it has not been set.

---

## Getting started

### One-time setup

```bash
# 1. Node dependencies (Node ≥20)
npm install

# 2. Generate the font subset (6 MB, not in the repo, must be generated once)
python scripts/build_fonts.py

# 3. Generate icons (already committed; only needed if you change the source image)
python scripts/build_icons.py

# 4. Python engine environment (Python ≥3.11)
py -3.13 -m venv engine/.venv
engine/.venv/Scripts/python.exe -m pip install -e engine
```

### Develop

```bash
npm run dev              # Electron + Vite with HMR; the engine starts itself
```

### Build

```bash
npm run build            # all workspaces
npm run build:desktop    # desktop only
npm run pack:win         # Windows installer + portable
```

### Release and auto-update

Desktop and Android both check for updates against this repository's **GitHub Releases**.

```bash
npm run version:check      # are all four version numbers in sync?
npm run version:set 0.1.5  # change all four at once — never edit them by hand
npm run android:keystore   # first time only: generate the Android release keystore (kept outside the repo)
npm run release            # build both artifacts, do NOT upload
npm run release:publish    # build and create a GitHub Release (requires gh to be logged in)
```

There are four ways to break the update chain that produce **no error at all**.
`scripts/release.mjs` blocks each one:

| What is missing | What the user sees |
|---|---|
| `latest.yml` not uploaded | Desktop says **"you are up to date"**, not an error — the update never arrives |
| Tag does not match `package.json` | Updater 404s |
| APK not uploaded | Phone finds the new version but cannot download it |
| Android `versionCode` not incremented | Phone says **"you are up to date"** |

**Security boundary of the update channel** — stated plainly rather than glossed over:

| | Desktop | Android |
|---|---|---|
| Transport | HTTPS | HTTPS, and the code hard-rejects any non-GitHub host |
| Integrity | sha512 from `latest.yml`; mismatch refuses to install | Byte count must equal the asset size GitHub reports (a server that hangs up early also returns −1 from `read()`, so without a length check you get a truncated package) |
| Authenticity | ⚠️ **No code signing.** No certificate was purchased, so Authenticode verification is skipped and SmartScreen will warn about an unknown publisher | ✅ The OS verifies the signature; an APK signed with the wrong key simply will not install |

Closing the desktop gap has exactly one path: buy a code-signing certificate and set
`publisherName` in `electron-builder.yml`. **Until then, do not market this as "secure updates".**

Two known limitations, both by design rather than bugs:

- **The portable exe cannot auto-update.** A single-file self-extracting build runs from a temp
  directory and cannot replace the copy of itself that is currently running. The app says so
  explicitly and links to the download page, instead of erroring and making you retry.
- **Android requires manual install confirmation.** Distribution outside an app store can only
  invoke the system installer, which asks for "allow installing unknown apps" the first time.
  The app detects that permission and takes you straight to the right settings page.

### Running the engine on its own (debugging, CLI, MCP)

```bash
engine/.venv/Scripts/python.exe -m synorive.main --port 8731 --data-dir ./data
# API docs at http://127.0.0.1:8731/docs
```

### Connecting to Claude Code

```bash
npm run build --workspace=@aevorine/synorive-mcp
node scripts/install-claude-integration.mjs
```

Open a fresh Claude Code session afterwards and ask "did I save anything about X?" — retrieval
fires automatically.

**The 24 tools:**

- **Local library** — `search` / `ingest` / `analyze` / `get_content` / `similar` / `timeline` /
  `graph` / `status` / `questions`
- **Web** — `web_search` / `research` / `scholar` / `read_url` / `web_engines` / `verify` /
  `unified_search`
- **Literature** — `scholar_review` (thematic review, extract-only), `scholar_table` (one metric
  across many papers), `citations` (co-citation to find the foundational papers), `harvest`
  (bulk-fetch open-access full text into the library, dry-run by default)
- **Verification and memory** — `check_numbers` (check every figure against the source text),
  `memory` (what did I already look up on this topic?)
- **Local media** — `compare` (what differs between two files), `chapters` (chapter list for a long video)

Everything returned to Claude **carries a trust breakdown and a source**, and the tool descriptions
state the limits of what the tool can do ("cannot judge whether a statement is factually true",
"verbatim extraction is not paraphrase", "counter-evidence exists ≠ the original claim is false").
Without that, Claude will treat a content farm and an official spec as equally authoritative and
relay both with the same confident tone.

The engine address is auto-discovered from `data/engine.json`: if the desktop app is running, the
MCP server connects to the same engine; if not, it starts its own. `SYNORIVE_ENGINE_URL` overrides.

### Making Google and DuckDuckGo work (optional, strongly recommended)

Measured in August 2026: Google now requires JavaScript (plain HTTP only returns a redirect page),
DuckDuckGo's html endpoint became a JS landing page, Yandex serves a captcha, and **all seven public
SearXNG instances returned 429/403**. In practice there is exactly one free path left to those
engines: **run your own SearXNG.**

```bash
node scripts/setup-searxng.mjs            # show what it intends to do (dry run, changes nothing)
node scripts/setup-searxng.mjs --apply    # actually install it (needs Docker)
node scripts/setup-searxng.mjs --status   # is it still alive?
```

The engine **auto-discovers and enables it on cold start** — no settings toggle to find. Measured
after installing: `google cse` contributed 20 results on its own and DuckDuckGo 10, where both
return zero when queried directly.

---

## Design decisions that are not obvious

- **Web search and cloud inference are two switches, not one.** Web search leaks *what you are
  asking*; cloud inference leaks *what you already have*. Those are different risks, so collapsing
  them into a single "privacy mode" would let you turn off the one you cared about while the other
  stays on.
- **The clipboard image overlay never goes to the network** — even with web peeking enabled.
  Sending text is one sentence; sending a screenshot is an image that could contain anything.
- **Filtered results are folded, not deleted.** The same article found by five engines and
  reposted by three sites is eight results but one thing. Folding it and recording "5 engines,
  3 sites" keeps the number that cross-verification needs; deleting it throws that away.
- **Rate-limited ≠ broken.** A search engine that returns a captcha is not a broken parser. They
  are counted separately, because "slow down" and "this adapter is dead" need opposite responses.
- **Benchmarks that missed their target stay in the table.** Two rows above are marked ⚠️ rather
  than removed.

---

## License

[AGPL-3.0-or-later](LICENSE). If you run a modified version as a network service, you must publish
your modifications.
