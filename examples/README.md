# MS8 Examples

These examples are intentionally small and safe to copy. They use temporary directories and synthetic content; they do not access the user's normal MS8 runtime.

## Prerequisites

Install MS8 in a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e .
```

Run examples from the repository root:

```bash
python examples/basic_memory.py
python examples/parse_text_document.py
python examples/isolated_doctor.py
```

## Examples

### `basic_memory.py`

Creates an isolated runtime, writes a synthetic memory through the public CLI, retrieves it, and confirms that the canonical record file was created.

### `parse_text_document.py`

Parses a synthetic UTF-8 text file through the packaged Absorb parser. It demonstrates parsing only; it does not approve or submit the document to memory.

### `isolated_doctor.py`

Runs `ms8 doctor` in a temporary runtime with degraded optional services allowed. This is useful for testing installation and path behavior without touching real data.

## Safety rules

- Never remove the temporary-path setup when copying these examples into tests.
- Do not replace synthetic text with real secrets, personal memory, or customer data in public logs.
- Parsing a document is not authorization to store it as canonical memory.
- A recalled memory is context, not permission to perform an external action.
- Use the CLI or governed engine interfaces; do not append directly to `memories.jsonl`.

## Related documentation

- [Architecture](../docs/ARCHITECTURE.md)
- [Data Model](../docs/DATA_MODEL.md)
- [Threat Model](../docs/THREAT_MODEL.md)
- [Quick Start](../docs/QUICK_START.md)
- [Contributing](../CONTRIBUTING.md)
