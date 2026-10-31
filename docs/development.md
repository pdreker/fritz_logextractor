# Development

```bash
uv sync --all-groups
uv run ruff check . && uv run ruff format --check .
uv run ty check
uv run pytest
```

Conventions, the design and what the captures of real boxes showed are in
[AGENTS.md](https://github.com/pdreker/fritz_logextractor/blob/main/AGENTS.md).

## Documentation

The site is built with [MkDocs](https://www.mkdocs.org/) and
[Material for MkDocs](https://squidfunk.github.io/mkdocs-material/); [mike](https://github.com/jimporter/mike)
keeps one copy per version on the `gh-pages` branch.

```bash
uv run mkdocs serve            # preview at http://127.0.0.1:8000
uv run mkdocs build --strict   # what CI checks on each pull request
```

GitHub Actions publishes the site:

| Trigger | Version | Workflow |
|---|---|---|
| push to `main` | `dev` | `docs.yaml` |
| release (release-please) | `<major>.<minor>`, alias `latest` | `release.yaml` |

The site's root leads to `latest`, or to `dev` as long as there is no release.
