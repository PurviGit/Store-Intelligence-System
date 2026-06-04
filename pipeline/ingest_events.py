"""
Feeds events from the detection pipeline into the Store Intelligence API.

Usage:
    python ingest_events.py --input data/events.jsonl --api-url http://localhost:8000
    python ingest_events.py --input data/events.jsonl --api-url http://localhost:8000 --batch-size 500

Flags:
    --input       Path to JSONL events file  (default: data/events.jsonl)
    --api-url     API base URL               (default: http://localhost:8000)
    --batch-size  Events per POST request    (default: 500, max 500)
    --dry-run     Validate only, do not POST
"""
import argparse
import json
import sys
import time
import requests
from pathlib import Path


def ingest(events_path: str, api_url: str, batch_size: int = 500, dry_run: bool = False):
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

    batch_size = min(batch_size, 500)  # API hard limit
    batches = [events[i:i + batch_size] for i in range(0, len(events), batch_size)]
    total_ingested = 0
    total_dupes = 0
    total_errors = 0

    for i, batch in enumerate(batches):
        if dry_run:
            print(f"  [DRY RUN] Batch {i+1}/{len(batches)}: {len(batch)} events")
            total_ingested += len(batch)
            continue

        try:
            resp = requests.post(
                f"{api_url}/events/ingest",
                json={"events": batch},
                timeout=30,
            )
            data = resp.json()
            ingested = data.get("ingested", 0)
            dupes   = data.get("duplicates", 0)
            errors  = data.get("errors", 0)
            total_ingested += ingested
            total_dupes    += dupes
            total_errors   += errors
            status = "OK" if resp.status_code == 200 else f"HTTP {resp.status_code}"
            print(f"  [{status}] Batch {i+1}/{len(batches)}: {ingested} ingested, {dupes} dupes, {errors} errors")
        except requests.exceptions.ConnectionError:
            print(f"  [ERROR] Cannot connect to API at {api_url}")
            print(f"          Make sure the API is running: uvicorn main:app --port 8000")
            sys.exit(1)
        except Exception as exc:
            print(f"  [ERROR] Batch {i+1} failed: {exc}")
            total_errors += len(batch)

    print(f"\nDone: {total_ingested} ingested, {total_dupes} duplicates, {total_errors} errors")
    return total_ingested


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest detection pipeline events into the API")
    parser.add_argument("--input",      default="data/events.jsonl",   help="Path to JSONL events file")
    parser.add_argument("--api-url",    default="http://localhost:8000",help="API base URL")
    parser.add_argument("--batch-size", type=int, default=500,          help="Events per POST (max 500)")
    parser.add_argument("--dry-run",    action="store_true",            help="Validate only, do not POST")
    args = parser.parse_args()
    ingest(args.input, args.api_url, args.batch_size, args.dry_run)
