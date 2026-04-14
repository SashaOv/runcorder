# Runcorder

An always-on flight recorder for Python scripts: live watch line while it runs, compact Markdown report when it crashes or gets stuck.

See [docs/user.md](docs/user.md).

## Development

Runcorder uses [uv](https://docs.astral.sh/uv/) for environment management. Requires Python 3.13+.

### Set up

```bash
uv sync
```

### Test

```bash
uv run pytest
```

### Build

```bash
uv build
```

Artifacts land in `dist/` (wheel and sdist).