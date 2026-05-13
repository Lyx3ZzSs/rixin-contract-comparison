# Repository Guidelines

## Project Structure & Module Organization

This repository currently contains only repository metadata: `LICENSE`, `.gitignore`, and this guide. The `.gitignore` is Python-oriented, so new code should follow a conventional Python layout unless the project direction changes.

Recommended structure:

- `src/rixin_contract_comparison/` for application or library code.
- `tests/` for automated tests, mirroring source modules where practical.
- `docs/` for design notes, user-facing documentation, and examples.
- `scripts/` for repeatable utilities such as data setup or report generation.

Keep generated files, virtual environments, caches, build output, and local secrets out of version control.

## Build, Test, and Development Commands

No package manager or test runner is configured yet. Until one is added, use these basic checks:

- `git status --short` shows pending changes.
- `python -m compileall src tests` checks Python syntax once `src/` and `tests/` exist.

When adding Python project metadata, prefer `pyproject.toml` and document the final commands here:

- `python -m pytest` runs the test suite.
- `python -m ruff check .` runs lint checks.
- `python -m ruff format .` formats Python files.

## Coding Style & Naming Conventions

Use 4-space indentation for Python. Name modules and packages with `snake_case`, classes with `PascalCase`, and functions, variables, and test helpers with `snake_case`. Keep modules focused and avoid committing notebook checkpoints, local databases, coverage output, or `.env` files.

Prefer type hints for public functions and cross-module data structures. Use short comments only where intent is not obvious.

## Testing Guidelines

Place tests under `tests/`. Name test files `test_<module>.py` and test functions `test_<behavior>()`. Keep tests deterministic and avoid relying on local files outside the repository.

For future coverage, prioritize contract comparison parsing, normalization, diff logic, and edge cases around missing or malformed input.

## Commit & Pull Request Guidelines

Git history currently contains only `Initial commit`, so no detailed convention has been established. Use concise, imperative commit subjects such as `Add comparison parser` or `Document test workflow`.

Pull requests should include a short purpose statement, a summary of changed behavior, test results, and known limitations. Link related issues when available. Include screenshots or sample output only for rendered reports or user-visible CLI output.

## Security & Configuration Tips

Do not commit contracts containing sensitive business, customer, or personal data unless they are approved test fixtures. Store secrets in local environment files that remain ignored by Git.
