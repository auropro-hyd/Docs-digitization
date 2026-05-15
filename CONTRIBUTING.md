# Contributing

This is the working agreement for landing changes in this repo. Read it before opening your first PR — it covers Spec-Kit conventions, branch / commit / PR shape, the test commands the CI gate runs, and how to add new compliance rules.

## TL;DR

1. **Branch off `main`** with a conventional name (`feat/`, `fix/`, `chore/`, `spec/`, `docs/`, `test/`).
2. **Write tests first**, then code; commit small.
3. Open a PR. CI (`Backend (pytest)` + `Frontend (tsc + lint + build)`) must pass; conversation resolution + 0 approvers required. **Admins can't bypass.**
4. Squash-merge; branches auto-delete on merge.
5. For anything bigger than a one-file change, open a [spec dir](./specs/) under `specs/NNN-<feature>/` first — see "Spec-Kit workflow" below.

## Branch / commit / PR conventions

### Branch names

| Prefix | When |
|---|---|
| `feat/` | New user-visible behaviour |
| `fix/` | Bug fix with concrete reproducer |
| `chore/` | Build / tooling / repo hygiene |
| `spec/` | Spec-Kit docs only (no code) |
| `test/` | Test-only additions |
| `docs/` | README / inline doc changes |

Suffix should be kebab-case and self-explanatory: `fix/segmentation-overlap-clamp-and-canonical-types` over `fix/seg-bug`.

### Commit messages

Conventional commits — type + scope + summary, body explains the *why* (not the *what* — the diff covers what):

```
fix(compliance/segmentation): fold enrichment into segment() pipeline

Architectural fix for the class of bugs surfaced by Akhilesh's
2026-05-14 run on 2538105062.pdf:
  - three overlapping BPCR sections (1-3 / 1-10 / 1-19)
  ...
```

Keep the body wrapped at ~72 cols for `git log` readability.

### PR descriptions

The `.github/pull_request_template.md` is the shape:

- **Summary** — 1-3 bullets, what + why, with file links.
- **Test plan** — concrete checklist (`pytest tests/path/test_x.py — N passed` over "tests pass").
- **Out of scope** — what you considered but deferred, with one-liner reasoning.
- **Spec** — link the spec dir if applicable.
- **Migration notes** — for breaking changes / re-runs needed.

## Running locally

### Backend

```bash
cd backend
# First time:
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run dev server (Makefile target sets WeasyPrint DYLD path on macOS):
cd .. && make backend

# Run tests:
cd backend && pytest -q
```

WeasyPrint on macOS needs Homebrew's pango / cairo / gobject:

```bash
brew install pango
```

Then the Makefile's `backend` target prefixes `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`.

### Frontend

```bash
cd frontend
npm install
npm run dev    # localhost:3100
```

Or `make frontend` from repo root.

### Full-stack

```bash
make dev    # starts both
```

## CI gate

Every PR runs:

- **Backend**: `pytest -q --tb=short` against Python 3.13 with WeasyPrint native deps installed.
- **Frontend**: `tsc --noEmit`, `next lint`, `next build` against Node 20.

Both must be green for merge.

## Spec-Kit workflow

For features bigger than ~1 file or behaviour anyone outside the immediate team needs to understand, write a spec first.

```bash
# Scaffold a new spec dir from the templates:
.specify/scripts/bash/create-new-feature.sh --short-name <slug> \
  "One-line feature description"

# Fills in: specs/NNN-<slug>/spec.md (and pre-creates the spec branch).
```

Then:

1. Fill in `spec.md` (user stories with priorities, FRs, success criteria, edge cases).
2. Fill in `research.md` (design choices, alternatives considered).
3. Fill in `data-model.md` (entities, wire shapes).
4. Fill in `plan.md` (constitution check, phases, risks).
5. Fill in `tasks.md` (numbered tasks, one user story per group).
6. Open a spec-only PR for review.
7. Implementation happens on a separate `feat/NNN-...-impl` branch off the spec branch.

See `specs/008-report-format-export/` and `specs/011-segmentation-robust-coverage/` for worked examples.

## Adding a compliance rule

Compliance rules are **data, not code** (Constitution Principle IX — Rule-as-Data). Adding one is a YAML edit.

Files:

- `backend/app/compliance/rules/<agent>_rules.yaml` — the rule definitions per agent (alcoa / gmp / checklist / sop / reconciliation).
- `backend/app/compliance/rules/<agent>_rules.md` — human-readable rationale + examples.
- `backend/app/compliance/rules/document_profiles.yaml` — section_type / document_type vocabulary; required-sections per profile.

After editing:

```bash
# Validate the rule against a real doc:
python -m backend.app.compliance.rules.validate_cli \
  --agent alcoa --rule 27 --doc <doc_id> --pages 3,7 --expect pass

# Run the rule suite end-to-end:
cd backend && pytest tests/compliance/ -q
```

## Coding standards

### Python

- Type hints are required on new functions.
- `ruff` runs in CI via `pyproject.toml`'s linter config.
- Docstrings: imperative mood, 1-2 sentences explaining *why* the function exists. The *what* is in the signature.
- Prefer pure functions for post-process pipeline steps so they're testable in isolation.
- Use `pytest.mark.asyncio` for async tests; no `pytest-asyncio-cooperative` workarounds.

### TypeScript

- No `any` in new code unless absolutely necessary (`// eslint-disable-next-line @typescript-eslint/no-explicit-any` with a one-line justification).
- React keys: composite (`${id}-${idx}`) when the source may emit duplicates — see [#65](https://github.com/auropro-hyd/Docs-digitization/pull/65) for the lesson.
- Component files end with `.tsx`; pure types end with `.ts`.
- Tailwind v4 only; no `@apply` in component-scoped styles.

### Tests

- Co-locate by stage: `tests/compliance/`, `tests/bmr/`, `tests/integration/`, etc.
- Test names start with `test_what_when` — read as a sentence.
- Pin invariants with comments referencing the spec FR / SC / commit they enforce.
- Integration tests use FastAPI's `TestClient`; LLM is stubbed via the `LLMProvider` port.

## Reviewing

- Every PR with code changes triggers a CODEOWNERS review request (auto-routed by domain).
- 0 approvals are required by branch protection — but `Required conversation resolution: ON` means every comment must be marked resolved before merge.
- Squash-merge only. The PR title becomes the squash commit subject; the PR body becomes the body. **Write the PR description as the final commit message** — it shows up in `git log` forever.

## Security

See [SECURITY.md](./SECURITY.md). TL;DR — don't open public GitHub issues for security findings; use Security Advisories or email `anmol@auropro.com`.

## Code of Conduct

See [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md). The Contributor Covenant v2.1.
