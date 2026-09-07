# How to contribute

## Dependencies

This project uses [uv](https://github.com/astral-sh/uv) for dependency management and `make` as a command runner.

Install all dependencies, including the lint, type-check, test and docs groups:
```bash
uv sync --all-groups
```

Install the git hooks (ruff, zizmor, whitespace fixers) with [prek](https://github.com/j178/prek):
```bash
uv run prek install
```

### Virtual environment

Activate the virtualenv:
```bash
source .venv/bin/activate
```

Or prefix every command with `uv run`, which is what the `make` targets do.

### Local S3

The test suite talks to a real S3-compatible store rather than mocks, so start MinIO before running it:
```bash
make run_infra
```

This brings up MinIO on `localhost:9000` (console on `localhost:9001`, credentials `minioadmin` / `minioadmin`).
Without it every test that needs a bucket is skipped, so a green run that exercised nothing looks the same as a
passing one — check the skip count.

## Running checks

View all available commands:
```bash
make help
```

## Tests and linting

Run tests:
```bash
make test
```

Run a single test, or a subset:
```bash
uv run pytest tests/test_storage.py::test_save_open_roundtrip
uv run pytest -k gzip
```

Run linting and type checking:
```bash
make lint
```

Apply the formatter and the auto-fixable lint rules:
```bash
make format
```

Both linting and tests are required during CI, across Python 3.11-3.14 and Django 5.2, 6.0 and 6.1.

Run the benchmarks the way [CodSpeed](https://codspeed.io/danfimov/django-capo-s3) does:
```bash
uv run pytest tests/ --codspeed
```

Exercise `collectstatic` end to end against the local MinIO:
```bash
make example_collectstatic
```

Running it twice should leave every `last_modified` untouched — that is the skip-unchanged behaviour holding.

## Documentation

Build and serve the docs locally:
```bash
make docs
```

The docs are built with [Zensical](https://zensical.org) from `docs/` and `zensical.toml`, and published to GitHub
Pages on every push to `main`.

## Submitting code

This project follows trunk-based development. Key principles:

- Protected `main` branch requires pull requests
- Create branches named `<type>-<short-description>`, using the same types as the commit convention below, so the
  subject of a change is clear from the branch alone: `feat-cloudfront-response-overrides`,
  `fix-exists-on-collect-static-use-etags-instead-of-head`, `chore-bump-capo-s3`, `docs-contributing-guide`
- Submit pull requests to `main`
- Releases are tracked via `git tag`: pushing a `v*` tag builds and publishes to PyPI, with the version taken from
  the tag itself. Do not edit `__version__` in a pull request

### Commit messages

Commits and pull request titles follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):
```
<type>[optional scope][!]: <description>

[optional body]

[optional footer]
```

The types in use here are `feat`, `fix`, `perf`, `refactor`, `test`, `docs`, `ci`, `build` and `chore`. Write the
description in the imperative mood and say what changes for the user, not which lines moved:
```
fix: exists method should use ETag info instead of head requests during collectstatic
feat(static): skip re-uploading assets whose content is already stored
```

Mark an incompatible change with a `!` before the colon, and explain it in a `BREAKING CHANGE:` footer:
```
feat(storage)!: url() no longer accepts positional parameters

BREAKING CHANGE: pass expire and parameters as keyword arguments.
```

The release notes for each tag are assembled from these messages, so a vague subject line becomes a vague changelog
entry.

### Before submitting

1. Run `make lint` and `make test`
2. Make your changes, following the [development guidelines](https://github.com/danfimov/django-capo-s3/blob/main/AGENTS.md)
3. Add tests for new functionality
4. Update the documentation if needed: `README.md` for the feature list and usage, the matching page under `docs/`,
   and `docs/django_storages_comparison.md` when a change closes a gap that django-storages still has
5. Run `make lint` and `make test` again

## Other contributions

Share the library with others, write articles about your usage, or report the rough edges you hit with your own
S3-compatible provider — the provider matrix in the docs only grows from real reports.
