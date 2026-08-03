# Security Policy

## Reporting a vulnerability

Please report privately through
[GitHub Security Advisories](https://github.com/Aevorine/Synorive/security/advisories/new)
rather than opening a public issue.

Include what you did, what happened, and what you expected. A proof of concept helps but is not
required. Expect an initial reply within about a week — this is a small project, not a company
with an on-call rota, and saying so plainly is better than promising an SLA that will be missed.

## Supported versions

Only the latest release receives fixes. There are no long-term support branches.

## What this project already does about security

Stated so you can check the claims rather than take them on faith:

- **The engine binds to `127.0.0.1` only.** It is not reachable from the local network unless you
  deliberately change that.
- **The browser-render service also binds to loopback only**, and rejects any URL that is not
  `http`/`https` — otherwise anything able to reach that port could read local files through
  `file:///`.
- **The PDF print window runs with `nodeIntegration: false`, `javascript: false`, `sandbox: true`.**
  The HTML it renders contains text fetched from the public web.
- **Web search and cloud inference are separate switches.** Turning off one does not silently
  leave the other on.
- **The clipboard image overlay never goes to the network**, regardless of settings.
- **Cookies injected for authenticated capture go into a per-lane in-memory partition**, are
  cleared before each capture, and never touch the default session or disk.
- **Android update downloads hard-reject any non-GitHub host** and verify the downloaded byte
  count against the size GitHub reports.

## Known gaps — stated rather than glossed over

- **The Windows builds are not code-signed.** No certificate has been purchased, so Authenticode
  verification is skipped and SmartScreen will warn about an unknown publisher. Desktop update
  integrity therefore rests on the sha512 in `latest.yml` served over HTTPS, not on a signature.
  Do not treat the desktop update channel as authenticated.
- **API keys you enter (Brave, Serper, Tavily, Exa, Semantic Scholar, cloud inference) are stored
  locally in your user data directory.** They are not encrypted at rest beyond the file
  permissions your OS gives that directory.
- **Self-hosted SearXNG is a third party in the loop.** If you point it at a public instance
  instead of your own, that instance sees your queries.
