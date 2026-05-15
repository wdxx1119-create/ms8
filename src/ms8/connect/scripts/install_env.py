from __future__ import annotations

import shutil


def run() -> dict:
    deps = {
        "python3": shutil.which("python3") or "",
        "ms8": shutil.which("ms8") or "",
    }
    ok = bool(deps["python3"])
    return {"ok": ok, "deps": deps}


def main() -> dict:
    return run()


if __name__ == "__main__":
    print(main())
