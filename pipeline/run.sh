#!/bin/bash
# One command to process all CCTV clips and feed into API
# Usage: ./run.sh [clips_dir] [api_url]

CLIPS_DIR="${1:-/videos}"
API_URL="${2:-http://localhost:8000}"
EVENTS_FILE="data/events.jsonl"

echo "=== Store Intelligence Pipeline ==="
echo "Clips dir: $CLIPS_DIR"
echo "API URL:   $API_URL"

cd /app/pipeline

echo ""
echo "Step 1: Processing CCTV clips..."
python detect.py --clips-dir "$CLIPS_DIR" --output "$EVENTS_FILE" --every 5

echo ""
echo "Step 2: Ingesting events into API..."
python ingest_events.py --events "$EVENTS_FILE" --api "$API_URL"

echo ""
echo "=== Pipeline complete ==="
