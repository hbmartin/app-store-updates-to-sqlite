# Repository Instructions

## Required quality checks

After every code edit, always run all of these checks from the repository root:

```console
uv run lizard src tests
uv run pyrefly check
uv run ty check
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Do not consider a code edit complete until every check passes.
