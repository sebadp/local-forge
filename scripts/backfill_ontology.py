#!/usr/bin/env python3
"""Offline backfill script: populate the entity ontology from existing database data.

Usage:
    .venv/bin/python scripts/backfill_ontology.py [--db data/localforge.db]
"""

import argparse
import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def main(db_path: str) -> None:
    from app.database.db import init_db
    from app.ontology.backfill import run_full_backfill
    from app.ontology.registry import EntityRegistry

    conn, _ = await init_db(db_path)
    registry = EntityRegistry(conn)
    counts = await run_full_backfill(conn, registry)
    logger.info("Backfill complete: %s", counts)
    await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill ontology graph from existing data")
    parser.add_argument("--db", default="data/localforge.db", help="Path to SQLite database")
    args = parser.parse_args()
    asyncio.run(main(args.db))
