"""
Feeds events from the detection pipeline into the API.
Usage:
    python ingest_events.py --events data/events.jsonl --api http://localhost:8000
"""
import argparse
import json
import sys
import requests
from pathlib import Path

BATCH_SIZE = 100


def ingest(events_path: str, api_url: str, dry_run: bool = False):
    path = Path(events_path)
    if not path.exists():
        print(f"[ERROR] Events file not found: {events_path}")
        sys.exit(1)

    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))

    print(f"Loaded {len(events)} events from {events_path}")

    batches = [events[i:i + BATCH_SIZE] for i in range(0, len(events), BATCH_SIZE)]
    total_ingested = 0
    total_errors = 0

    for i, batch in enumerate(batches):
        if dry_run:
            print(f"  [DRY RUN] Batch {i+1}/{len(batches)}: {len(batch)} events")
            continue

        try:
            resp = requests.post(
                f"{api_url}/events/ingest",
                json={"events": batch},
                timeout=30,
            )
            data = resp.json()
            ingested = data.get("ingested", 0)
            errors = data.get("errors", 0)
            total_ingested += ingested
            total_errors += errors
            status = "OK" if resp.status_code == 200 else "WARN"
            print(f"  [{status}] Batch {i+1}/{len(batches)}: {ingested} ingested, {errors} errors")
        except Exception as e:
            print(f"  [ERROR] Batch {i+1} failed: {e}")

    print(f"\nTotal: {total_ingested} ingested, {total_errors} errors")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", default="data/events.jsonl")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ingest(args.events, args.api, args.dry_run)
