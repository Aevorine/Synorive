<!--
Thanks for the PR. The checklist below is short — it only asks for the things this project has
actually been bitten by before. See CONTRIBUTING.md for the reasoning behind each one.
-->

## What this changes / 这个改动做了什么

<!-- One or two sentences. If it fixes an issue, write "Fixes #123". -->

## Why / 为什么

<!--
The problem, not the patch. "Indexing silently stopped at 40% with no error" is useful;
"refactored the ingest loop" is not.
问题本身，而不是补丁。「入库到 40% 悄悄停了且不报错」有用，「重构了入库循环」没用。
-->

## Checks / 检查项

- [ ] `npm run typecheck` passes
- [ ] `node scripts/check-hardcoded-style.mjs` passes (no hard-coded colours or font sizes)
- [ ] Python files I touched compile (`py_compile`) using `engine/.venv`, not the system Python

## If it touches any of these, please say how / 如果碰到了下面这些，请说明怎么处理的

- [ ] **A performance number in the README** — which benchmark did you re-run, and what did it say?
      Include the number even if it got worse.
- [ ] **Search result filtering** — filtered results must stay recoverable with a stated reason,
      never silently dropped.
- [ ] **A search engine adapter** — a captcha or rate limit is `challenged`, not `broken`. Only a
      genuinely unparseable page is `broken`, and it should return a specific reason.
- [ ] **The release chain** (`set-version.mjs`, `release.mjs`, `electron-builder.yml`, the
      workflow) — every failure mode here is silent by default. Say what you did to make yours loud.
- [ ] **Anything the packaged app depends on at runtime** — verify against
      `release/win-unpacked/`, not against your dev environment. Those two have diverged before.

## Not required / 不要求

No CLA, no commit message format, no squashing. If the change is right, the shape of the history
is not worth arguing about.
