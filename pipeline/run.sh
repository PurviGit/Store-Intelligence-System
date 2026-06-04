#!/bin/bash
# One command to process all CCTV clips (Store 1 + Store 2) and feed into API
#
# Usage — pick whichever works on your OS:
#
#   Linux / Mac / Git Bash (inline env):
#     STORE1_DIR="/path/to/Store 1" STORE2_DIR="/path/to/Store 2" bash pipeline/run.sh
#
#   Windows PowerShell (set vars first, then run):
#     $env:STORE1_DIR = "C:\path\to\Store 1"
#     $env:STORE2_DIR = "C:\path\to\Store 2"
#     bash pipeline/run.sh
#
#   CLI arguments (works everywhere):
#     bash pipeline/run.sh --store1 "C:/path/to/Store 1" --store2 "C:/path/to/Store 2"

set -e

# Always resolve project root relative to this script — works on any machine
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Parse optional --store1 / --store2 / --api CLI arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --store1) STORE1_DIR="$2"; shift 2 ;;
    --store2) STORE2_DIR="$2"; shift 2 ;;
    --api)    API_URL="$2";    shift 2 ;;
    *) shift ;;
  esac
done

# Fall back to env vars if CLI args not provided
STORE1_DIR="${STORE1_DIR:-}"
STORE2_DIR="${STORE2_DIR:-}"
API_URL="${API_URL:-http://localhost:9000}"
EVENTS_FILE="$PROJECT_ROOT/data/events.jsonl"

# Convert Windows backslash paths to forward slashes (Git Bash on Windows)
STORE1_DIR="${STORE1_DIR//\\//}"
STORE2_DIR="${STORE2_DIR//\\//}"

echo "=================================================="
echo " Store Intelligence — Detection Pipeline"
echo "=================================================="

# Validate that store directories are provided
if [ -z "$STORE1_DIR" ] || [ -z "$STORE2_DIR" ]; then
    echo ""
    echo "ERROR: Store directories not set. Use one of these methods:"
    echo ""
    echo "  Method 1 — CLI args (works on all platforms):"
    echo '    bash pipeline/run.sh --store1 "C:/path/to/Store 1" --store2 "C:/path/to/Store 2"'
    echo ""
    echo "  Method 2 — PowerShell:"
    echo '    $env:STORE1_DIR = "C:\path\to\Store 1"'
    echo '    $env:STORE2_DIR = "C:\path\to\Store 2"'
    echo '    bash pipeline/run.sh'
    echo ""
    echo "  Method 3 — Linux/Mac/Git Bash (inline):"
    echo '  STORE1_DIR="C:/path/to/Store 1" STORE2_DIR="C:/path/to/Store 2" bash pipeline/run.sh'
    echo ""
    echo "Example (Linux/Mac):"
    echo '  STORE1_DIR="/path/to/Store 1" STORE2_DIR="/path/to/Store 2" bash pipeline/run.sh'
    echo ""
    echo "The Store 1 folder should contain: CAM 1 - zone.mp4, CAM 2 - zone.mp4, CAM 3 - entry.mp4, CAM 5 - billing.mp4"
    echo "The Store 2 folder should contain: entry 1.mp4, entry 2.mp4, zone.mp4, billing_area.mp4"
    exit 1
fi

if [ ! -d "$STORE1_DIR" ]; then
    echo "ERROR: Store 1 directory not found: $STORE1_DIR"
    exit 1
fi

if [ ! -d "$STORE2_DIR" ]; then
    echo "ERROR: Store 2 directory not found: $STORE2_DIR"
    exit 1
fi

echo " Store 1: $STORE1_DIR"
echo " Store 2: $STORE2_DIR"
echo " API:     $API_URL"
echo " Output:  $EVENTS_FILE"
echo "=================================================="

cd "$PROJECT_ROOT"

echo ""
echo "[1/3] Processing CCTV clips for both stores..."
python3 pipeline/detect.py \
    --all-stores \
    --store1-dir "$STORE1_DIR" \
    --store2-dir "$STORE2_DIR" \
    --output "$EVENTS_FILE" \
    --every 3

echo ""
echo "[2/3] Ingesting events into API ($API_URL)..."
python3 pipeline/ingest_events.py \
    --input "$EVENTS_FILE" \
    --api-url "$API_URL" \
    --batch-size 500

echo ""
echo "[3/3] Verifying API..."
curl -sf "$API_URL/stores/STORE_BLR_002/metrics" | python3 -m json.tool | head -10 || echo "API not reachable yet"
curl -sf "$API_URL/health" | python3 -m json.tool | head -8 || true

echo ""
echo "Pipeline complete. Events saved to: $EVENTS_FILE"
