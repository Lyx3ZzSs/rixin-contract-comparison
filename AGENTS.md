# Repository Guidelines

## Project Structure & Module Organization

This repository is a contract comparison and field extraction MVP with a FastAPI backend and a Vite/React frontend. Keep backend behavior compatible with the existing `/api/compare/*` and `/api/extract/*` routes unless a change explicitly calls for an API migration.

Current structure:

- `app/api*.py` for HTTP adapters only: request parsing, HTTP errors, and response wiring.
- `app/application/` for use-case orchestration such as task creation and background submission.
- `app/infrastructure/` for replaceable adapters such as task persistence, artifact storage, and task execution.
- `app/services/` for document extraction, comparison, risk analysis, PDF artifacts, and report generation.
- `frontend/src/` for React pages, components, API client, and shared types.
- `tests/` for backend tests; `frontend/src/**/*.test.*` for frontend tests.
- `docs/` for architecture notes, user-facing documentation, and examples.
- `scripts/` for repeatable utilities such as quality evaluation.

Keep generated files, virtual environments, caches, build output, and local secrets out of version control.

## Build, Test, and Development Commands

- `git status --short` shows pending changes.
- `python -m compileall app tests` checks backend Python syntax.
- `python -m pytest` runs the test suite.
- `python -m ruff check .` runs lint checks.
- `python -m ruff format .` formats Python files.
- `cd frontend && npm test` runs frontend tests.
- `cd frontend && npm run build` runs TypeScript and production build checks.

## Coding Style & Naming Conventions

Use 4-space indentation for Python. Name modules and packages with `snake_case`, classes with `PascalCase`, and functions, variables, and test helpers with `snake_case`. Keep modules focused and avoid committing notebook checkpoints, local databases, coverage output, or `.env` files.

Prefer type hints for public functions and cross-module data structures. Use short comments only where intent is not obvious.

## Testing Guidelines

Place tests under `tests/`. Name test files `test_<module>.py` and test functions `test_<behavior>()`. Keep tests deterministic and avoid relying on local files outside the repository.

Prioritize contract comparison parsing, normalization, diff logic, task repository behavior, API compatibility, and edge cases around missing or malformed input.

## Commit & Pull Request Guidelines

Git history currently contains only `Initial commit`, so no detailed convention has been established. Use concise, imperative commit subjects such as `Add comparison parser` or `Document test workflow`.

Pull requests should include a short purpose statement, a summary of changed behavior, test results, and known limitations. Link related issues when available. Include screenshots or sample output only for rendered reports or user-visible CLI output.

## Security & Configuration Tips

Do not commit contracts containing sensitive business, customer, or personal data unless they are approved test fixtures. Store secrets in local environment files that remain ignored by Git.
