"""Remove all rows from the configured synthetic visit register."""

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from backend.db import purge


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="Confirm deletion of all visit rows")
    args = parser.parse_args()
    if not args.yes:
        parser.error("Stop the server and pass --yes to confirm deletion.")
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    path = root / os.getenv("DATABASE_PATH", "data/visits.sqlite3")
    purge(path)
    print("The configured synthetic visit register is empty.")


if __name__ == "__main__":
    main()
