# Access fallback ladder

Use the least invasive route that can access the source:

1. RCSB/Europe PMC/Crossref API.
2. OA PDF or JATS XML.
3. Public publisher/repository HTML through `scripts/browser_fetch.mjs`.
4. User-authorized browser profile or storage state for Reaxys, SciFinder,
   institutional publisher access or patent databases.
5. Computer Use only when the available browser automation cannot operate the
   authorized UI and the Computer Use plugin is available.
6. Manual export by the user when the source still requires an interactive
   license/authentication step.

Never bypass a paywall, CAPTCHA, institutional login or license restriction.
Never automate a user password field. A session may be reused only with explicit
authorization for that site.

## Browser fetch

```bash
node scripts/browser_fetch.mjs \
  --url "https://publisher.example/article" \
  --download-links \
  --sleep-ms 2000 \
  --output-dir browser_evidence
```

Batch a DOI/URL queue and keep failed entries isolated:

```bash
node scripts/browser_fetch.mjs \
  --url-file urls.txt \
  --download-links \
  --max-links 12 \
  --sleep-ms 5000 \
  --profile-dir "C:/path/to/authorized/profile" \
  --output-dir browser_evidence
```

The script auto-discovers the current Codex bundled Node module directory. If
that fails, set `RUNTIME_NODE_MODULES` explicitly.

For an already-authorized browser session:

```bash
node scripts/browser_fetch.mjs \
  --url "https://publisher.example/article" \
  --profile-dir "C:/path/to/authorized/profile" \
  --output-dir browser_evidence
```

or use a user-provided storage state:

```bash
node scripts/browser_fetch.mjs \
  --url "https://publisher.example/article" \
  --storage-state "C:/path/to/state.json" \
  --output-dir browser_evidence
```

Do not commit or transmit the profile or storage state.
Keep requests serial and spaced by at least 1.5-2 seconds. A PDF-like URL is
accepted only when the payload starts with `%PDF-`; HTTP 200 HTML is retained
as an access-blocker, not renamed to PDF.

For publisher batches, use `--sleep-ms 5000` or more and retry only failed
DOIs. Keep the authorized browser profile alive during retries. Do not export
cookies into a third-party client; fetch inside the authorized page/context.

The browser script verifies file signatures rather than trusting HTTP status,
content type or URL suffix alone. `%PDF-`, ZIP/OOXML, TIFF, PNG and JPEG magic
are recorded; an HTML login/error page is never renamed to `.pdf`.

For the first run on a licensed publisher, launch `browser_fetch.mjs --headed`
with a dedicated `--profile-dir`, let the user complete lawful sign-in and any
bot check manually, and open one representative PDF action. Keep that profile
alive for subsequent serial runs. Never automate a password field or CAPTCHA.

## Blocker resolution

A blocker is resolved only when one of these is recorded:

- a downloadable full text or SI with hash;
- a rendered page image and OCR/visual review;
- a patent publication with an opened example/table;
- a licensed-database export and query metadata;
- an explicit `needs_user_access` state after all authorized routes fail.

`needs_user_access` is not a completed affinity record.

## Blocker ledger

After running the available routes:

```bash
python scripts/resolve_blockers.py \
  --evidence-pack evidence_pack \
  --identity-resolution identity/identity_resolution.csv \
  --source-register source_register.csv \
  --database-ledger database_query_ledger.csv \
  --browser-manifest browser_evidence/browser_manifest.json \
  --output blocker_resolution.csv
```

`blockers.csv` preserves the original failure. `blocker_resolution.csv`
records the route, evidence, hash and terminal decision. An unauthenticated
licensed database remains `requires_authorized_session`; it is not described as
“no hit.”
