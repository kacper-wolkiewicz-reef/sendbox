"""Allow running the package as `python -m sendbox`."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
