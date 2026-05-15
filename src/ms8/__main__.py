"""Module entrypoint for python -m ms8."""

import sys

from .cli import main

if __name__ == "__main__":
    code = int(main())
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except OSError:
        # Best-effort flush at process exit; do not mask unrelated exceptions.
        pass
    raise SystemExit(code)
