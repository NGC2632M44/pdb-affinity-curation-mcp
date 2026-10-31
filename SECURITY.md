# Security and Privacy

## Never commit

- passwords, API tokens, session cookies or private keys;
- licensed database exports;
- paywalled PDFs or supplementary files;
- human-review workbooks or participant/patient data;
- local absolute paths, machine names or institution network details.

## Credentials

Use environment variables or the host's credential store. The MCP checks only
whether optional variables exist; it does not need their values in prompts or
logs.

## Authorized access

Use only sources you are permitted to access. The project must not be used to
bypass paywalls, CAPTCHAs, entitlement controls or database terms.

## Local threat model

The server is a single-user local stdio process. It is not designed as a
multi-tenant network service. File tools accept caller-supplied paths, so run the
server in a constrained workspace or sandbox and do not expose it to untrusted
clients.

Wrapped scripts receive an allowlisted process environment. The PatMap API key
is passed only to the PatMap extractor. `patmap_extract` uploads supplied PDFs to
the configured PatMap service; obtain explicit authorization before using it.

`environment_check` can reveal absolute local paths and whether optional
credentials are configured. Do not publish raw environment-check output.

## Reporting

Report a vulnerability through GitHub Security Advisories for this repository.
Do not include credentials or licensed content in a public issue.

