# MS8 Examples

These examples are intentionally small and conservative. They use public CLI behavior or narrowly scoped parsing APIs and avoid a user's real runtime directory by default.

## Prerequisites

Install MS8 in a virtual environment:

```bash
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows PowerShell
# .venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install ms8
```

When running from a source checkout, install the project first:

```bash
python -m pip install -e .
```

## `isolated_cli.py`

Runs a complete basic CLI flow in a temporary MS8 runtime:

- `ms8 --help`
- `ms8 version`
- `ms8 doctor`
- write one memory
- search for that memory

```bash
python examples/isolated_cli.py
```

The temporary directory is removed when the script finishes. To inspect the generated runtime:

```bash
python examples/isolated_cli.py --keep
```

Or choose an explicit test-only directory:

```bash
python examples/isolated_cli.py --base-dir ./tmp/ms8-example
```

Do not point `--base-dir` at an existing production `MS8_HOME`.

## `parse_local_text.py`

Parses a single local `.txt` file through the Absorb parser without submitting it to canonical memory:

```bash
python examples/parse_local_text.py ./notes/example.txt
```

By default the script prints metadata only. A short content preview requires explicit consent:

```bash
python examples/parse_local_text.py ./notes/example.txt --show-preview
```

Parsing is not governance approval and does not create an accepted MS8 memory record.

## Safety rules for examples

- Use synthetic test text, not real credentials or personal records.
- Keep examples inside a temporary or dedicated test directory.
- Do not publish generated runtime directories or logs without reviewing and redacting them.
- Treat parsed document text as untrusted data.
- Do not convert a remembered instruction into external action authority.

## Related documentation

- [Quick Start](../docs/QUICK_START.md)
- [Architecture](../docs/ARCHITECTURE.md)
- [Data Model](../docs/DATA_MODEL.md)
- [Threat Model](../docs/THREAT_MODEL.md)
- [Support](../SUPPORT.md)
