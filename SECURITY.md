# Security Policy

## Supported Versions

Only the `main` branch is actively supported. Tagged releases are not yet shipped; deployments track main commits.

## Reporting a Vulnerability

**Please do not file public GitHub issues for security problems.** This project handles pharmaceutical batch records, OCR-extracted patient and product data, and compliance audit trails — even cosmetic-looking bugs can carry regulatory weight.

### Preferred: GitHub Security Advisories (private)

Open a private advisory at
<https://github.com/auropro-hyd/Docs-digitization/security/advisories/new>.

This routes the report to maintainers without exposing it publicly and lets us coordinate the fix + disclosure timeline through GitHub's native tooling.

### Backup: email

`anmol@auropro.com` — include:

- A description of the vulnerability and its impact.
- A reproducer (steps, doc IDs, payloads — sanitised to the minimum needed to verify).
- The commit SHA you tested against.
- Optional: a proposed fix or mitigation.

## Response timeline

| Stage | Target |
|---|---|
| Acknowledgement | within **3 business days** |
| Triage + severity assessment | within **7 business days** |
| Fix landed on `main` (critical / high severity) | within **30 days** |
| Fix landed on `main` (medium / low) | within **90 days** |
| Public disclosure | coordinated with reporter, no earlier than fix + 7 days |

## Scope

In-scope:

- Authentication / authorisation gaps in the FastAPI surface.
- Path-traversal, SSRF, injection vectors in upload / export / preview paths.
- Compliance pipeline correctness bugs that would mislead a regulatory reviewer (wrong findings, dropped HITL state, score tampering).
- Persistence-layer issues (segmentation overrides, compliance result mutation, telemetry leakage).
- Secret / PHI / PII leakage in logs, responses, telemetry, or rendered reports.
- Dependency vulnerabilities surfaced by Dependabot that the maintainers haven't yet patched.

Out of scope:

- Issues affecting only outdated branches (we don't maintain stable tags yet).
- Reports from automated scanners without a working reproducer.
- Social-engineering / phishing simulations against maintainers.
- Denial-of-service via API rate-limiting (no rate limits are claimed — operator deployments should run behind a reverse proxy).

## What you can expect

- Credit in the changelog and the security advisory, unless you ask for anonymity.
- A direct line to the maintainer for the duration of the report.
- No legal action for good-faith research that doesn't degrade production data or access content beyond what's needed to demonstrate the issue.
