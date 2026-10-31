# AlphaMa PatMap

Verified local implementation pattern:

```text
POST /prod-api/apps/api/patmap/execute
GET  /prod-api/apps/api/patmap/task_status?taskId=...
```

Authentication:

- `X-Auth-Token: ${PATMAP_API_KEY}`
- The key is account-specific and must stay in the environment.

Submit fields:

- multipart `file`: PDF
- `version`: default `1.6`
- `compute_function`: `table`, `reaction`, `coreferences`

On `SUCCESS`, download `data.downloadUrl`, verify the ZIP, reject path traversal
and extract into an evidence folder.

PatMap does not discover which patent to read. Use RCSB chemical names,
InChIKeys, Reaxys/SciFinder references and patent-family IDs to choose the
documents first.

`scripts/patmap_extract.py` requires Python `requests`. The Codex bundled Python
may not include it; run the script with the user's configured Python environment
that has `requests` installed.
