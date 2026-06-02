#!/bin/bash
# One command to process all CCTV clips (Store 1 + Store 2) and feed into API
# Usage:
#   bash pipeline/run.sh
#   STORE1_DIR=/path/to/store1 STORE2_DIR=/path/to/store2 bash pipeline/run.sh
#
# Environment variables:
#   STORE1_DIR  Path to Store 1 video clips directory
#   STORE2_DIR  Path to Store 2 video clips directory
#   API_URL     API base URL (default: http://localhost:8000)

STORE1_DIR="${STORE1_DIR:-C:/Users/Purvi/Downloads/Store 1-20260602T101818Z-3-001ec38db8/Store 1}"
STORE2_DIR="${STORE2_DIR:-C:/Users/Purvi/Downloads/Store 2-20260602T101819Z-3-001099f208/Store 2}"
API_URL="${API_URL:-http://localhost:8000}"
EVENTS_FILE="data/events.jsonl"

echo "═══════════════════════════════════════════════"
echo " Store Intelligence Detection Pipeline"
echo "═══════════════════════════════════════════════"
echo " Store 1: $STORE1_DIR"
echo " Store 2: $STORE2_DIR"
echo " API:     $API_URL"

cd /app/pipeline 2>/dev/null || cd "$(dirname "$0")/.."

echo ""
echo "[1/3] Processing CCTV clips for both stores..."
python3 pipeline/detect.py \
    --all-stores \
    --store1-dir "$STORE1_DIR" \
    --store2-dir "$STORE2_DIR" \
    --output "$EVENTS_FILE" \
    --every 5

echo ""
echo "[2/3] Ingesting events into API ($API_URL)..."
python3 pipeline/ingest_events.py --input "$EVENTS_FILE" --api-url "$API_URL" --batch-size 500

echo ""
echo "[3/3] Verifying..."
curl -sf "$API_URL/stores/STORE_BLR_002/metrics" | python3 -m json.tool | head -15
curl -sf "$API_URL/health" | python3 -m json.tool | head -10

echo ""
echo "✓ Pipeline complete — events in: $EVENTS_FILE"
