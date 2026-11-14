"""Start the Mock Helper on the local loopback interface."""

import argparse
from pathlib import Path

import uvicorn

from .server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures-dir", type=Path)
    parser.add_argument("--fixture", default="mail_inbox")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        app = create_app(args.fixtures_dir, args.fixture)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Cannot load Mock Helper fixtures: {exc}\n")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
