# Repository Guidelines

## Project Structure & Module Organization

This repository is a contract-comparison product with a FastAPI backend and a Vite/React frontend. Keep backend behavior compatible with the existing `/api/compare/*` routes unless a change explicitly calls for an API migration. Repository read and list paths must tolerate historical field-extraction task files by skipping them without deleting stored data.

Current structure:

- `backend/app/api*.py` for HTTP adapters only: request parsing, HTTP errors, and response wiring.
- `backend/app/application/` for use-case orchestration such as task creation and background submission.
- `backend/app/infrastructure/` for replaceable adapters such as task persistence, artifact storage, and task execution.
- `backend/app/services/` for document extraction, comparison, risk analysis, PDF artifacts, and report generation.
- `backend/tests/` for backend tests.
- `backend/scripts/` for repeatable local utilities.
- `frontend/src/` for React pages, components, API client, and shared types.
- `frontend/src/**/*.test.*` for frontend tests.
- `docs/` for architecture notes, user-facing documentation, and examples.

Keep generated files, virtual environments, caches, build output, and local secrets out of version control.

## Build, Test, and Development Commands

- `git status --short` shows pending changes.
- `cd backend && python -m compileall app tests` checks backend Python syntax.
- `cd backend && python -m pytest` runs the backend test suite.
- `cd backend && python -m ruff check .` runs backend lint checks.
- `cd backend && python -m ruff format .` formats Python files.
- `cd frontend && npm test` runs frontend tests.
- `cd frontend && npm run build` runs TypeScript and production build checks.

## Coding Style & Naming Conventions

Use 4-space indentation for Python. Name modules and packages with `snake_case`, classes with `PascalCase`, and functions, variables, and test helpers with `snake_case`. Keep modules focused and avoid committing notebook checkpoints, local databases, coverage output, or `.env` files.

Prefer type hints for public functions and cross-module data structures. Use short comments only where intent is not obvious.

## Testing Guidelines

Place backend tests under `backend/tests/`. Name test files `test_<module>.py` and test functions `test_<behavior>()`. Keep tests deterministic and avoid relying on local files outside the repository.

Prioritize contract comparison parsing, normalization, diff logic, task repository behavior, API compatibility, and edge cases around missing or malformed input.

## Commit & Pull Request Guidelines

Git history currently contains only `Initial commit`, so no detailed convention has been established. Use concise, imperative commit subjects such as `Add comparison parser` or `Document test workflow`.

Pull requests should include a short purpose statement, a summary of changed behavior, test results, and known limitations. Link related issues when available. Include screenshots or sample output only for rendered reports or user-visible CLI output.

## Security & Configuration Tips

Do not commit contracts containing sensitive business, customer, or personal data unless they are approved test fixtures. Store secrets in local environment files that remain ignored by Git.
