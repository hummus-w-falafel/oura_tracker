"""Print the current SQLite schema for drift checks against agent docs."""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db


def dump_schema(db_path: Path):
    conn = sqlite3.connect(db_path)
    try:
        tables = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()
        for (table,) in tables:
            print(f"\n{table}")
            print("-" * len(table))
            for col in conn.execute(f"PRAGMA table_info({table})"):
                _, name, col_type, not_null, default, pk = col
                bits = [name, col_type or "ANY"]
                if pk:
                    bits.append("PK")
                if not_null:
                    bits.append("NOT NULL")
                if default is not None:
                    bits.append(f"DEFAULT {default}")
                print(" | ".join(bits))
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Dump SQLite table columns for docs drift checks.")
    parser.add_argument("--db", default=str(db.get_db_path()), help="Path to SQLite DB")
    args = parser.parse_args()
    path = Path(args.db)
    if not path.exists():
        raise SystemExit(f"Database not found: {path}")
    dump_schema(path)


if __name__ == "__main__":
    main()
