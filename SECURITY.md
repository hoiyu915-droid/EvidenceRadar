# Security Policy

## Supported version

The `main` branch is the only actively supported line unless a release states otherwise. `legacy/python-runtime/` is retained for provenance and is not an active service.

## Reporting

Use GitHub private vulnerability reporting or a private repository security advisory. Include the affected path or version, reproduction steps, impact, and proposed mitigation when available.

Do not place credentials, private datasets, exploitable details, or sensitive source-access information in public issues.

Research-quality, provenance, licensing, and broken-link reports that do not create a security risk may use normal issues.

## Credential policy

Secrets must be supplied only through the execution environment's protected secret mechanism. `.env`, credentials, raw source caches, and generated user artifacts are excluded from version control. If a secret is committed, revoke it first; deleting the current file is not sufficient because Git history remains accessible.
