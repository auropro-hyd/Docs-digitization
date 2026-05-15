# GitHub Actions CI/CD

The project uses **GitHub Actions** for continuous integration and quality gates. There are three workflows under [`.github/workflows/`](../../.github/workflows/):

| Workflow | File | Triggers | Purpose |
|----------|------|----------|---------|
| **CI** | `ci.yml` | `pull_request`, `push` to `main` | Backend tests (pytest) + Frontend type-check / lint / build |
| **PR Quality** | `pr-quality.yml` | `pull_request` (opened, edited, sync, label) | Typos check, semantic PR-title validation, path-based auto-labels, PR-size labels |
| **Maintenance** | `maintenance.yml` | Weekly cron (Mon 03:00 UTC) + manual dispatch | Markdown link check (lychee), stale-issue / stale-PR closer |

> **Legacy note**: `infra/azure-pipelines.yml` still exists from the original project scaffold (March 2026) but is **inactive** — there is no Azure DevOps service connected to the repo. It is kept for historical reference only and is not invoked anywhere. New CI changes go in `.github/workflows/`.

## Required status checks

Branch protection on `main` requires the following checks to pass before merge:

- `Backend (pytest)`
- `Frontend (tsc + lint + build)`

Branch protection also enforces:

| Setting | Value |
|---------|-------|
| Required approving reviews | `0` (CODEOWNERS auto-requests reviewers; review is recommended but not gating) |
| Require branches to be up to date | `true` (strict — rebase before merge) |
| Dismiss stale approvals on new push | `true` |
| Enforce admins | `true` |
| Allow force push | `false` |
| Allow deletions | `false` |

## CI workflow (`ci.yml`)

### Path-filter sentinel

A leading `changes` job uses [`dorny/paths-filter`](https://github.com/dorny/paths-filter) to detect whether the PR touches backend or frontend code:

```yaml
filters: |
  backend:
    - 'backend/**'
    - '.github/workflows/ci.yml'
  frontend:
    - 'frontend/**'
    - '.github/workflows/ci.yml'
```

The downstream `Backend (pytest)` and `Frontend (tsc + lint + build)` jobs keep their original names (so branch-protection keeps matching) but their heavy steps are gated behind `if: needs.changes.outputs.<name> == 'true'`. Docs-only PRs therefore short-circuit through the required checks in roughly 30 seconds.

### Backend job

| Step | Command | When |
|------|---------|------|
| Install native deps for WeasyPrint | `apt-get install libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0` | backend changed |
| Set up Python | `actions/setup-python@v6`, version 3.13, pip cache | backend changed |
| Install Python deps | `pip install -e ".[dev]"` | backend changed |
| Run tests | `pytest -q --tb=short` (`AT_ENV=test`) | backend changed |

WeasyPrint native deps are installed because the Spec 008 PDF renderer needs Pango / Cairo / GObject — without them the renderer tests skip and the report-export integration tests fall back to HTML.

### Frontend job

| Step | Command | When |
|------|---------|------|
| Set up Node | `actions/setup-node@v6`, version 20, npm cache | frontend changed |
| Install | `npm ci` | frontend changed |
| Type-check | `npx tsc --noEmit` | frontend changed |
| Lint | `npx next lint` | frontend changed |
| Build | `npx next build` | frontend changed |

The build step is kept in addition to type-check + lint because it's the only gate that catches things like missing env vars at static-route generation time and broken dynamic imports.

### Concurrency

```yaml
concurrency:
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```

New pushes to the same PR cancel any in-flight run so we don't pile up green-but-stale jobs.

## PR Quality workflow (`pr-quality.yml`)

| Job | Action | Purpose |
|-----|--------|---------|
| **Typos** | [`crate-ci/typos`](https://github.com/crate-ci/typos) | Spell-check (non-blocking via step-level `continue-on-error`; config: `_typos.toml` — excludes OCR fixtures, rules, specs) |
| **Semantic PR title** | [`amannn/action-semantic-pull-request`](https://github.com/amannn/action-semantic-pull-request) | Enforces Conventional Commit prefix on the PR title for clean squash-merge changelogs |
| **Auto-label by path** | [`actions/labeler@v5`](https://github.com/actions/labeler) | Applies `backend` / `frontend` / `docs` / `specs` / `ci` / `compliance` / `bmr` / `dependencies` labels based on changed files (config: `.github/labeler.yml`) |
| **PR size label** | [`codelytv/pr-size-labeler@v1`](https://github.com/codelytv/pr-size-labeler) | Adds `size/XS .. size/XL` labels (ignores lock files; XL warns but doesn't fail) |

The Typos job uses **step-level** `continue-on-error: true` so the job itself reports green — the codebase ingests pharmaceutical OCR text full of legitimate-looking misspellings (e.g. segmentation headers explicitly match `"Pege"` as a known OCR variant of `"Page"`), so typos is informational only.

## Maintenance workflow (`maintenance.yml`)

Runs **Mondays at 03:00 UTC** (08:30 IST) on cron, plus on-demand via `workflow_dispatch`.

| Job | Action | Purpose |
|-----|--------|---------|
| **Markdown link check** | [`lycheeverse/lychee-action@v2`](https://github.com/lycheeverse/lychee-action) | Crawls `**/*.md` for broken links, 1-week cache; non-fatal (`fail: false`) |
| **Stale issues + PRs** | [`actions/stale@v9`](https://github.com/actions/stale) | Generous defaults: 90-day issue stale + 30-day close; 60-day PR stale + 21-day close; exempts `pinned/security/roadmap/wip` |

## Dependabot

Configured in [`.github/dependabot.yml`](../../.github/dependabot.yml):

- **Weekly** schedule, Mondays at 09:00 Asia/Kolkata
- Three ecosystems: `pip` (backend/), `npm` (frontend/), `github-actions` (/)
- Minor + patch updates are **grouped** so we don't drown in tiny PRs
- Majors stay separate (breaking changes need individual review)

### Ecosystem-blocked majors (ignored)

Four majors are explicitly ignored until the surrounding ecosystem catches up:

| Dependency | Reason | Lift when |
|-----------|--------|-----------|
| `next` | Next.js majors land breaking changes regularly | Manual upgrade |
| `typescript` | TS 6 errors on CSS side-effect imports (TS2882); needs global `declare module '*.css'` shim | Next.js ships official TS 6 support |
| `eslint` | `eslint-config-next@15.x` peer-pins eslint to `^9.0.0` | `eslint-config-next` ships a 10-compatible release |
| `eslint-config-next` | v16 drops `next lint`; needs codemod migration to `eslint` CLI | We do the migration (likely paired with Next.js 16) |

## Permissions model

| Workflow | `permissions` |
|----------|---------------|
| `ci.yml` | `contents: read`, `pull-requests: read` |
| `pr-quality.yml` | `contents: read`, `pull-requests: write` (labeler + size need write) |
| `maintenance.yml` | `contents: read`, `issues: write`, `pull-requests: write` |

All workflows use the default `GITHUB_TOKEN` — no PATs or service-principal secrets.

## Branching & merge strategy

```
Feature / fix branch
  │
  ▼
Pull request → main
  │
  ├── CI: Backend (pytest) + Frontend (tsc + lint + build) must pass
  ├── PR Quality: typos / semantic title / labels (non-gating except semantic title)
  ├── CODEOWNERS auto-requests review (not required)
  ├── Branch must be up to date with main (rebase if behind)
  │
  └── Squash merge → main
```

| Branch | CI runs | Deploy | Purpose |
|--------|---------|--------|---------|
| `main` | ✅ Yes | None (no auto-deploy) | Trunk; squash-merged PRs land here |
| `feature/*`, `fix/*`, `chore/*` | ✅ Yes (on PR) | None | Development work |
| `dependabot/*` | ✅ Yes | None | Auto-opened weekly |

Deployment to staging / production is currently performed manually outside of CI; see [Local Setup](./local-setup.md) for the run commands.

## Workflow files at a glance

```
.github/
├── workflows/
│   ├── ci.yml             # required status checks (backend + frontend)
│   ├── pr-quality.yml     # typos, semantic title, labeler, size
│   └── maintenance.yml    # weekly link-check + stale closer
├── labeler.yml            # actions/labeler config
├── CODEOWNERS             # routes review requests
├── dependabot.yml         # weekly dep updates + ignore rules
├── pull_request_template.md
└── ISSUE_TEMPLATE/
    ├── bug.yml
    ├── feature.yml
    └── config.yml
```

## Related Pages

- [Local Setup](./local-setup.md) — Development environment and environment variables
- [Settings](../backend/configuration/settings.md) — Application configuration system
- [Dependency Injection](../backend/configuration/dependency-injection.md) — How adapters are swapped per environment
- [Contributing Guide](../../CONTRIBUTING.md) — Branch / commit / PR conventions
- [Security Policy](../../SECURITY.md) — Vulnerability disclosure
