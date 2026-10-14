"""Write the OpenAPI spec to stdout or a file: ``uv run python -m avt.cli.openapi openapi.json``.

The frontend generates its TypeScript types from this file, and CI fails if the
committed copy drifts from the code.
"""

from __future__ import annotations

import json
import sys

from avt.api.main import create_app


def main() -> None:
    spec = create_app().openapi()
    payload = json.dumps(spec, indent=2, sort_keys=True) + "\n"
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w", encoding="utf-8") as fh:
            fh.write(payload)
    else:
        sys.stdout.write(payload)


if __name__ == "__main__":
    main()
