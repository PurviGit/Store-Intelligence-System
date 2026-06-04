"""
Live Web Dashboard — Flask + polling every 5s.
Shows real-time store metrics from the Intelligence API.
Includes live event feed, animated detection demo, and pipeline visualization.
Access at: http://localhost:3000
"""
import os
import time
import requests
from flask import Flask, render_template_string, jsonify, request as flask_request

app = Flask(__name__)
API_URL   = os.getenv("API_URL",   "http://localhost:9000")
DATA_DATE = os.getenv("DATA_DATE", "2026-04-10")

_cache = {}
CACHE_TTL = 8  # seconds


def _fetch(url: str, timeout: int = 5):
    try:
        return requests.get(url, timeout=timeout).json()
    except Exception as e:
        return {"error": str(e)}


def _store_from_request():
    return flask_request.args.get("store", "STORE_BLR_002")


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Store Intelligence — Live Dashboard</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg: #0a0a14; --surface: #13132a; --border: #252550;
  --accent: #7c3aed; --accent-light: #a78bfa; --green: #6ee7b7;
  --yellow: #fbbf24; --red: #f87171; --text: #e0e0f0; --muted: #6b7280;
}
body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }

/* ── Detection Demo ─────────────────────── */
.demo-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media(max-width:900px){ .demo-row { grid-template-columns:1fr; } }
.demo-panel { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 14px; }
.demo-panel h3 { font-size:.8rem; color:var(--accent-light); margin-bottom:10px; letter-spacing:.06em; text-transform:uppercase; }
.floor-plan { width:100%; aspect-ratio:16/9; background:#090920; border-radius:6px; border:1px solid var(--border); position:relative; overflow:hidden; }
.zone-rect { position:absolute; border:1px solid var(--border); border-radius:4px; display:flex; align-items:center; justify-content:center; font-size:.55rem; color:var(--muted); text-align:center; transition:background .4s; }
.zone-rect.active { border-color:var(--accent-light); color:var(--accent-light); }
.person-dot { position:absolute; width:10px; height:10px; border-radius:50%; background:var(--green); border:2px solid #fff; transform:translate(-50%,-50%); transition:left .8s ease, top .8s ease; z-index:5; }
.person-dot.staff { background:var(--yellow); }
.video-info { font-size:.72rem; color:var(--muted); margin-top:8px; line-height:1.6; }
.video-info b { color:var(--text); }
.pipeline-step { display:flex; align-items:center; gap:8px; padding:6px 0; border-bottom:1px solid var(--border); font-size:.72rem; }
.pipeline-step:last-child { border:none; }
.step-dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
.step-active { background:var(--green); box-shadow:0 0 6px var(--green); }
.step-idle { background:var(--border); }
.step-label { color:var(--muted); }
.step-val { margin-left:auto; color:var(--text); font-weight:600; }

/* ── Header ─────────────────────────────── */
header {
  background: var(--surface); padding: 12px 24px;
  border-bottom: 2px solid var(--accent);
  display: flex; justify-content: space-between; align-items: center;
  position: sticky; top: 0; z-index: 100;
}
header h1 { font-size: 1.1rem; color: var(--accent-light); display: flex; align-items: center; gap: 8px; }
.pulse { width: 8px; height: 8px; background: var(--green); border-radius: 50%; animation: pulse 2s infinite; }
@keyframes pulse { 0%,100%{ opacity:1; transform:scale(1); } 50%{ opacity:.5; transform:scale(1.4); } }
.header-right { display: flex; gap: 16px; align-items: center; }
#live-status { font-size: 0.78rem; color: var(--green); }
.store-sel {
  background: var(--border); color: var(--accent-light);
  border: 1px solid var(--accent); border-radius: 6px;
  padding: 4px 10px; font-size: 0.8rem; cursor: pointer;
}

/* ── Layout ──────────────────────────────── */
.main { padding: 16px 24px; display: flex; flex-direction: column; gap: 16px; }

/* ── Cards ───────────────────────────────── */
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 18px 20px; }
.card-title { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); margin-bottom: 10px; }

/* ── KPI Strip ───────────────────────────── */
.kpi-strip { display: grid; grid-template-columns: repeat(5,1fr); gap: 12px; }
.kpi { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px; }
.kpi-label { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); margin-bottom: 6px; }
.kpi-value { font-size: 2rem; font-weight: 700; color: var(--accent-light); line-height: 1; }
.kpi-sub { font-size: 0.72rem; color: var(--muted); margin-top: 4px; }

/* ── North Star Banner ───────────────────── */
.ns-banner {
  background: linear-gradient(135deg,#1a1040,#13132a);
  border: 1px solid var(--accent); border-radius: 10px;
  padding: 14px 20px; display: flex; gap: 24px; align-items: center; flex-wrap: wrap;
}
.ns-item { display: flex; flex-direction: column; gap: 2px; }
.ns-label { font-size: 0.62rem; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); }
.ns-value { font-size: 1rem; font-weight: 600; color: var(--accent-light); }
.ns-divider { width: 1px; height: 32px; background: var(--border); }
.ns-action {
  margin-left: auto; padding: 8px 14px;
  background: #3a1060; border: 1px solid var(--accent); border-radius: 8px;
  font-size: 0.78rem; color: var(--accent-light);
}

/* ── Three-col: funnel + heatmap + anomalies ── */
.three-col { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }

/* ── Funnel ──────────────────────────────── */
.funnel-stage { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.fn-label { width: 90px; font-size: 0.78rem; color: var(--accent-light); flex-shrink: 0; }
.fn-bar-wrap { flex: 1; background: var(--border); border-radius: 4px; height: 20px; overflow: hidden; }
.fn-bar { height: 100%; background: linear-gradient(90deg, var(--accent), var(--accent-light)); border-radius: 4px; transition: width .8s; }
.fn-stat { width: 80px; text-align: right; font-size: 0.75rem; flex-shrink: 0; }
.fn-drop { color: var(--red); font-size: 0.68rem; }
.fn-pct  { color: var(--green); font-size: 0.68rem; }

/* ── Heatmap ─────────────────────────────── */
.hm-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.hm-cell { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 12px; text-align: center; }
.hm-zone { font-size: 0.62rem; color: var(--muted); text-transform: uppercase; letter-spacing: .5px; margin-bottom: 4px; }
.hm-score { font-size: 1.6rem; font-weight: 700; line-height: 1; }
.hm-dwell { font-size: 0.65rem; color: var(--muted); margin-top: 3px; }
.hm-bar { background: var(--border); border-radius: 2px; height: 3px; margin-top: 6px; }
.hm-bar-fill { height: 3px; border-radius: 2px; transition: width .8s; }

/* ── Anomalies ───────────────────────────── */
.anom { border-radius: 8px; padding: 9px 12px; margin-bottom: 7px; border-left: 3px solid var(--muted); }
.anom.CRITICAL { border-color: var(--red);    background: #1a0808; }
.anom.WARN     { border-color: var(--yellow); background: #1a1408; }
.anom.INFO     { border-color: #60a5fa;       background: #08101a; }
.anom-type  { font-size: 0.78rem; font-weight: 600; }
.anom-action{ font-size: 0.68rem; color: var(--muted); margin-top: 2px; }

/* ── Two-col charts ──────────────────────── */
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.chart-area { height: 110px; display: flex; align-items: flex-end; gap: 3px; }
.bc { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 2px; }
.bc-fill { width: 100%; border-radius: 2px 2px 0 0; transition: height .7s; min-height: 2px; }
.bc-lbl { font-size: 0.58rem; color: var(--muted); }
.bc-val { font-size: 0.6rem; color: var(--muted); }

/* ── Pipeline Demo + Live Feed ───────────── */
.demo-row { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }

/* Pipeline animation */
.pipeline-viz {
  display: flex; align-items: center; gap: 0; padding: 14px 0; overflow: hidden;
}
.pipe-stage {
  flex: 1; text-align: center; padding: 10px 6px;
  background: var(--bg); border: 1px solid var(--border); border-radius: 8px; position: relative;
}
.pipe-stage-icon { font-size: 1.4rem; }
.pipe-stage-name { font-size: 0.62rem; color: var(--accent-light); margin-top: 4px; font-weight: 600; text-transform: uppercase; letter-spacing: .5px; }
.pipe-stage-sub { font-size: 0.58rem; color: var(--muted); margin-top: 2px; }
.pipe-arrow {
  width: 28px; flex-shrink: 0; text-align: center; font-size: 0.9rem;
  color: var(--accent); position: relative;
}
.pipe-packet {
  position: absolute; top: 50%; left: 0; transform: translateY(-50%);
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--green);
  animation: flow 2.4s linear infinite;
}
.pipe-packet:nth-child(2) { animation-delay: 0.8s; }
.pipe-packet:nth-child(3) { animation-delay: 1.6s; }
@keyframes flow {
  0%   { left: 0;   opacity: 0; }
  10%  { opacity: 1; }
  90%  { opacity: 1; }
  100% { left: 100%; opacity: 0; }
}

/* Detection demo canvas */
#demo-canvas { width: 100%; border-radius: 8px; border: 1px solid var(--border); display: block; background: #080810; }

/* Live event feed */
.event-feed { max-height: 210px; overflow-y: auto; }
.event-row {
  display: flex; gap: 8px; align-items: center;
  padding: 5px 0; border-bottom: 1px solid #1a1a2e; font-size: 0.72rem;
  animation: fadeIn .4s ease;
}
@keyframes fadeIn { from { opacity:0; transform: translateX(-6px); } to { opacity:1; transform: none; } }
.ev-badge {
  font-size: 0.6rem; padding: 2px 6px; border-radius: 10px;
  font-weight: 600; white-space: nowrap; flex-shrink: 0;
}
.ev-ENTRY   { background: #1a3a1a; color: var(--green); }
.ev-EXIT    { background: #3a1a1a; color: var(--red); }
.ev-ZONE_ENTER  { background: #1a1a3a; color: #93c5fd; }
.ev-ZONE_EXIT   { background: #2a1a2a; color: #c4b5fd; }
.ev-ZONE_DWELL  { background: #1a2a1a; color: #86efac; }
.ev-BILLING_QUEUE_JOIN    { background: #2a1a0a; color: var(--yellow); }
.ev-BILLING_QUEUE_ABANDON { background: #3a1a0a; color: #fb923c; }
.ev-REENTRY { background: #1a2a3a; color: #7dd3fc; }
.ev-visitor { color: var(--muted); flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ev-conf { color: var(--muted); font-size: 0.6rem; flex-shrink: 0; }
.ev-time { color: var(--muted); font-size: 0.6rem; flex-shrink: 0; width: 58px; text-align: right; }

/* ── Revenue row ─────────────────────────── */
.rev-row { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }

/* ── Recommendations ─────────────────────── */
.reco-row { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }
.reco { border-radius: 10px; padding: 12px 16px; border: 1px solid var(--border); background: var(--surface); }
.reco-label { font-size: 0.62rem; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); margin-bottom: 6px; }
.reco-text { font-size: 0.82rem; line-height: 1.4; }

#footer { padding: 10px 24px 18px; font-size: 0.68rem; color: var(--muted); }
</style>
</head>
<body>

<!-- ── Header ───────────────────────────────────────────── -->
<header>
  <h1>
    <span class="pulse"></span>
    Store Intelligence — Live Dashboard
  </h1>
  <div class="header-right">
    <select class="store-sel" id="store-sel" onchange="switchStore(this.value)">
      <option value="">Loading stores…</option>
    </select>
    <span id="live-status">Connecting…</span>
  </div>
</header>

<div class="main">

  <!-- ── North Star Banner ──────────────────── -->
  <div class="ns-banner">
    <div class="ns-item">
      <div class="ns-label">Conversion Rate (North Star)</div>
      <div class="ns-value" id="ns-conv">—</div>
    </div>
    <div class="ns-divider"></div>
    <div class="ns-item">
      <div class="ns-label">Drop-off Before Billing</div>
      <div class="ns-value" id="ns-drop">—</div>
    </div>
    <div class="ns-divider"></div>
    <div class="ns-item">
      <div class="ns-label">Hottest Zone</div>
      <div class="ns-value" id="ns-zone">—</div>
    </div>
    <div class="ns-divider"></div>
    <div class="ns-item">
      <div class="ns-label">Total Events Processed</div>
      <div class="ns-value" id="ns-events">—</div>
    </div>
    <div class="ns-action" id="ns-action">💡 Loading…</div>
  </div>

  <!-- ── KPI Strip ──────────────────────────── -->
  <div class="kpi-strip">
    <div class="kpi">
      <div class="kpi-label">Unique Visitors</div>
      <div class="kpi-value" id="k-vis">—</div>
      <div class="kpi-sub">Customers (staff excluded)</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Conversion Rate</div>
      <div class="kpi-value" id="k-conv">—</div>
      <div class="kpi-sub">Visitors who purchased</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Queue Depth (Peak)</div>
      <div class="kpi-value" id="k-queue" style="color:var(--yellow)">—</div>
      <div class="kpi-sub">Max at billing today</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Abandonment Rate</div>
      <div class="kpi-value" id="k-abandon" style="color:var(--red)">—</div>
      <div class="kpi-sub">Left billing before purchase</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Active Anomalies</div>
      <div class="kpi-value" id="k-anom" style="color:var(--red)">—</div>
      <div class="kpi-sub">Requires attention now</div>
    </div>
  </div>

  <!-- ── Revenue Row ──────────────────────── -->
  <div class="rev-row">
    <div class="card">
      <div class="card-title">💰 Total Revenue Today</div>
      <div style="font-size:1.8rem;font-weight:700;color:var(--green)" id="r-rev">—</div>
      <div style="font-size:0.72rem;color:var(--muted);margin-top:4px"><span id="r-txns">—</span> transactions · avg basket <span id="r-basket" style="color:var(--accent-light)">—</span></div>
    </div>
    <div class="card">
      <div class="card-title">📈 Revenue per Visitor</div>
      <div style="font-size:1.8rem;font-weight:700;color:var(--green)" id="r-rpv">—</div>
      <div style="font-size:0.72rem;color:var(--muted);margin-top:4px">Total revenue ÷ unique visitors</div>
    </div>
    <div class="card">
      <div class="card-title">🔧 Store Health</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:4px">
        <div><div style="font-size:0.62rem;color:var(--muted)">Camera Status</div><div id="h-cams" style="font-size:0.9rem;font-weight:600">—</div></div>
        <div><div style="font-size:0.62rem;color:var(--muted)">Database</div><div id="h-db" style="font-size:0.9rem;font-weight:600">—</div></div>
        <div><div style="font-size:0.62rem;color:var(--muted)">Data Confidence</div><div id="h-conf" style="font-size:0.9rem;font-weight:600">—</div></div>
        <div><div style="font-size:0.62rem;color:var(--muted)">Feed Mode</div><div id="h-mode" style="font-size:0.9rem;font-weight:600">—</div></div>
      </div>
    </div>
  </div>

  <!-- ── Funnel + Heatmap + Anomalies ─────── -->
  <div class="three-col">

    <div class="card">
      <div class="card-title">Conversion Funnel (4-Stage)</div>
      <div id="funnel-body"></div>
      <div style="margin-top:10px;font-size:0.7rem;color:var(--muted)">
        Overall: <span id="fn-overall" style="color:var(--green);font-weight:600">—</span>
      </div>
    </div>

    <div class="card">
      <div class="card-title">Zone Heatmap (0–100)</div>
      <div class="hm-grid" id="hm-grid"></div>
      <div style="margin-top:8px;font-size:0.65rem;color:var(--muted)">
        Confidence: <span id="hm-conf" style="color:var(--green)">—</span>
      </div>
    </div>

    <div class="card">
      <div class="card-title">Active Anomalies</div>
      <div id="anom-body"><p style="font-size:0.78rem;color:var(--muted)">No anomalies detected</p></div>
    </div>

  </div>

  <!-- ── Charts Row ─────────────────────── -->
  <div class="two-col">
    <div class="card">
      <div class="card-title">📊 Transactions by Hour</div>
      <div class="chart-area" id="txn-chart"></div>
    </div>
    <div class="card">
      <div class="card-title">📍 Zone Engagement (CCTV Event Count)</div>
      <div class="chart-area" id="zone-chart"></div>
    </div>
  </div>

  <!-- ── Pipeline Demo + Live Event Feed ─── -->
  <div class="demo-row">

    <!-- Pipeline visualization + detection demo -->
    <div class="card">
      <div class="card-title">🎬 Live Detection Pipeline</div>

      <!-- Animated pipeline flow -->
      <div class="pipeline-viz" style="margin-bottom:12px">
        <div class="pipe-stage">
          <div class="pipe-stage-icon">📹</div>
          <div class="pipe-stage-name">CCTV Clips</div>
          <div class="pipe-stage-sub">1080p · 15fps</div>
        </div>
        <div class="pipe-arrow">
          →
          <div class="pipe-packet"></div>
          <div class="pipe-packet"></div>
          <div class="pipe-packet"></div>
        </div>
        <div class="pipe-stage" id="ps-detect">
          <div class="pipe-stage-icon">🔍</div>
          <div class="pipe-stage-name">YOLOv8n</div>
          <div class="pipe-stage-sub">Person detect</div>
        </div>
        <div class="pipe-arrow">
          →
          <div class="pipe-packet"></div>
          <div class="pipe-packet"></div>
          <div class="pipe-packet"></div>
        </div>
        <div class="pipe-stage" id="ps-track">
          <div class="pipe-stage-icon">🎯</div>
          <div class="pipe-stage-name">IoU Track</div>
          <div class="pipe-stage-sub">Re-ID · Groups</div>
        </div>
        <div class="pipe-arrow">
          →
          <div class="pipe-packet"></div>
          <div class="pipe-packet"></div>
          <div class="pipe-packet"></div>
        </div>
        <div class="pipe-stage" id="ps-api">
          <div class="pipe-stage-icon">⚡</div>
          <div class="pipe-stage-name">Events API</div>
          <div class="pipe-stage-sub">8 event types</div>
        </div>
        <div class="pipe-arrow">
          →
          <div class="pipe-packet"></div>
          <div class="pipe-packet"></div>
          <div class="pipe-packet"></div>
        </div>
        <div class="pipe-stage" id="ps-dash">
          <div class="pipe-stage-icon">📊</div>
          <div class="pipe-stage-name">Dashboard</div>
          <div class="pipe-stage-sub">Live metrics</div>
        </div>
      </div>

      <!-- Simulated CCTV detection canvas -->
      <canvas id="demo-canvas" height="180"></canvas>
      <div style="margin-top:6px;font-size:0.65rem;color:var(--muted)">
        ↑ Simulated retail CCTV scene — YOLOv8n bounding boxes, trajectory tracking, zone mapping
      </div>
    </div>

    <!-- Live event feed -->
    <div class="card">
      <div class="card-title">
        ⚡ Live Event Stream
        <span id="feed-count" style="color:var(--accent-light);margin-left:8px">—</span>
      </div>
      <div class="event-feed" id="event-feed">
        <p style="font-size:0.78rem;color:var(--muted)">Loading events…</p>
      </div>
      <div style="margin-top:8px;font-size:0.65rem;color:var(--muted)">
        Showing most recent events · auto-refreshing every 5s
      </div>
    </div>

  </div>

  <!-- ── Detection Demo + Pipeline Status ─── -->
  <div class="demo-row">

    <!-- Animated store floor plan — persons shown as moving dots from live events -->
    <div class="demo-panel">
      <h3>📹 Live Detection View — Store Floor Plan</h3>
      <div class="floor-plan" id="floor-plan">
        <!-- Zone rectangles populated by JS from zone layout -->
        <div class="zone-rect" id="fz-ENTRY" style="left:42%;top:0;width:16%;height:14%;background:rgba(124,58,237,.1)">ENTRY</div>
        <div class="zone-rect" id="fz-BILLING" style="left:75%;top:70%;width:22%;height:28%;background:rgba(239,68,68,.08)">BILLING</div>
        <div class="zone-rect" id="fz-MINIMALIST" style="left:2%;top:15%;width:22%;height:35%;background:rgba(110,231,183,.06)">MINIMALIST</div>
        <div class="zone-rect" id="fz-SALM" style="left:26%;top:15%;width:22%;height:35%;background:rgba(110,231,183,.06)">SALM / TFS</div>
        <div class="zone-rect" id="fz-EB_KOREAN" style="left:2%;top:55%;width:22%;height:30%;background:rgba(167,139,250,.06)">EB KOREAN</div>
        <div class="zone-rect" id="fz-SKINCARE"  style="left:26%;top:55%;width:22%;height:30%;background:rgba(167,139,250,.06)">SKINCARE</div>
        <!-- Person dots injected by JS -->
      </div>
      <div class="video-info">
        <b>How to view actual CCTV clips:</b> Run
        <code style="background:#1a1a3a;padding:1px 5px;border-radius:3px;">
          STORE1_DIR="path/Store 1" STORE2_DIR="path/Store 2" bash pipeline/run.sh
        </code>
        — the pipeline processes clips and feeds events into the API in real time.
        The floor plan above shows <b>active visitor positions</b> derived from live events.
      </div>
    </div>

    <!-- Pipeline health + stage counters -->
    <div class="demo-panel">
      <h3>⚙️ Pipeline Status</h3>
      <div id="pipeline-steps">
        <div class="pipeline-step">
          <div class="step-dot step-active" id="ps-dot-cctv"></div>
          <span class="step-label">CCTV Input</span>
          <span class="step-val" id="ps-cctv">2 stores · 8 cameras</span>
        </div>
        <div class="pipeline-step">
          <div class="step-dot step-active" id="ps-dot-detect"></div>
          <span class="step-label">YOLOv8n Detection</span>
          <span class="step-val" id="ps-detect">@5fps effective</span>
        </div>
        <div class="pipeline-step">
          <div class="step-dot step-active" id="ps-dot-track"></div>
          <span class="step-label">IoU Tracker + Re-ID</span>
          <span class="step-val" id="ps-track">HSV histogram matching</span>
        </div>
        <div class="pipeline-step">
          <div class="step-dot" id="ps-dot-events"></div>
          <span class="step-label">Events Ingested</span>
          <span class="step-val" id="ps-events">—</span>
        </div>
        <div class="pipeline-step">
          <div class="step-dot" id="ps-dot-entry"></div>
          <span class="step-label">ENTRY events</span>
          <span class="step-val" id="ps-entry">—</span>
        </div>
        <div class="pipeline-step">
          <div class="step-dot" id="ps-dot-zone"></div>
          <span class="step-label">ZONE_DWELL events</span>
          <span class="step-val" id="ps-zone">—</span>
        </div>
        <div class="pipeline-step">
          <div class="step-dot" id="ps-dot-billing"></div>
          <span class="step-label">BILLING events</span>
          <span class="step-val" id="ps-billing">—</span>
        </div>
        <div class="pipeline-step">
          <div class="step-dot" id="ps-dot-reentry"></div>
          <span class="step-label">REENTRY caught</span>
          <span class="step-val" id="ps-reentry">—</span>
        </div>
      </div>
    </div>
  </div>

  <!-- ── Business Recommendations ─────────── -->
  <div class="reco-row">
    <div class="reco" style="border-left:3px solid var(--red)">
      <div class="reco-label">🔴 Urgent Action</div>
      <div class="reco-text" id="reco-1" style="color:#fca5a5">Loading…</div>
    </div>
    <div class="reco" style="border-left:3px solid var(--yellow)">
      <div class="reco-label">🟡 Opportunity</div>
      <div class="reco-text" id="reco-2" style="color:#fcd34d">Loading…</div>
    </div>
    <div class="reco" style="border-left:3px solid var(--green)">
      <div class="reco-label">✅ Performing Well</div>
      <div class="reco-text" id="reco-3" style="color:var(--green)">Loading…</div>
    </div>
  </div>

</div>

<div id="footer">Last updated: never · Purplle Tech Challenge 2026 — Store Intelligence System</div>

<!-- ── Scripts ──────────────────────────────────────────── -->
<script>
// ── Helpers ──────────────────────────────────────────────
function heatColor(s) {
  return s >= 80 ? '#ef4444' : s >= 50 ? '#f59e0b' : s >= 20 ? '#6ee7b7' : '#4b5563';
}
function fmt(n, dec=1) { return n != null ? Number(n).toFixed(dec) : '—'; }
function inr(n) { return n > 0 ? '₹' + Math.round(n).toLocaleString('en-IN') : '—'; }

function barChart(id, data, key, color) {
  const el = document.getElementById(id);
  if (!el || !data?.length) return;
  const max = Math.max(...data.map(d => d[key] ?? 0), 1);
  el.innerHTML = data.map(d => {
    const h = Math.round((d[key] / max) * 100);
    const lbl = (d.label || d.zone_id || '').replace(':00','').replace('_',' ');
    return `<div class="bc">
      <div class="bc-val">${d[key] > 0 ? d[key] : ''}</div>
      <div class="bc-fill" style="height:${h}px;background:${color}"></div>
      <div class="bc-lbl">${lbl.slice(0,8)}</div>
    </div>`;
  }).join('');
}

// ── Canvas Detection Demo ─────────────────────────────────
const canvas = document.getElementById('demo-canvas');
const ctx = canvas.getContext('2d');

// Simulated persons walking through store zones
const ZONES = [
  { name: 'ENTRY',    x: 0.05,  y: 0.0,  w: 0.15, h: 1.0,  color: '#7c3aed33' },
  { name: 'SKINCARE', x: 0.2,   y: 0.0,  w: 0.25, h: 1.0,  color: '#6ee7b733' },
  { name: 'HAIRCARE', x: 0.45,  y: 0.0,  w: 0.25, h: 1.0,  color: '#a78bfa33' },
  { name: 'BILLING',  x: 0.72,  y: 0.0,  w: 0.28, h: 1.0,  color: '#fbbf2433' },
];

class SimPerson {
  constructor(id) {
    this.id = id;
    this.x = 0.05 + Math.random() * 0.02;
    this.y = 0.2 + Math.random() * 0.6;
    this.vx = 0.004 + Math.random() * 0.004;
    this.vy = (Math.random() - 0.5) * 0.003;
    this.w = 0.045 + Math.random() * 0.02;
    this.h = 0.22 + Math.random() * 0.08;
    this.conf = 0.82 + Math.random() * 0.17;
    this.isStaff = id === 0;
    this.color = this.isStaff ? '#f59e0b' : '#6ee7b7';
    this.label = this.isStaff ? 'STAFF' : `VIS_${String(id).padStart(3,'0')}`;
    this.active = true;
    this.dwellTimer = 0;
  }
  update() {
    this.x += this.vx;
    this.y += this.vy;
    this.dwellTimer++;
    if (this.y < 0.1) this.vy = Math.abs(this.vy);
    if (this.y + this.h > 0.9) this.vy = -Math.abs(this.vy);
    if (this.x > 1.1) { this.active = false; }
    // Slow down in skincare zone (dwell)
    if (this.x > 0.2 && this.x < 0.45 && this.dwellTimer % 60 < 30) {
      this.vx = 0.001;
    } else {
      this.vx = 0.004 + Math.random() * 0.002;
    }
  }
  draw(cw, ch) {
    const px = this.x * cw, py = this.y * ch;
    const pw = this.w * cw, ph = this.h * ch;
    // Bounding box
    ctx.strokeStyle = this.color;
    ctx.lineWidth = 1.5;
    ctx.strokeRect(px, py, pw, ph);
    // Label background
    ctx.fillStyle = this.color + 'cc';
    const lblW = 80, lblH = 14;
    ctx.fillRect(px, py - lblH, lblW, lblH);
    // Label text
    ctx.fillStyle = '#000';
    ctx.font = 'bold 9px monospace';
    ctx.fillText(`${this.label} ${fmt(this.conf,2)}`, px + 2, py - 3);
    // Confidence bar
    ctx.fillStyle = '#1a1a2e';
    ctx.fillRect(px, py + ph + 1, pw, 3);
    ctx.fillStyle = this.color;
    ctx.fillRect(px, py + ph + 1, pw * this.conf, 3);
    // Trajectory dot trail
    ctx.fillStyle = this.color + '88';
    ctx.beginPath();
    ctx.arc(px + pw/2, py + ph/2, 2, 0, Math.PI*2);
    ctx.fill();
  }
}

let simPersons = [];
let simFrame = 0;
let eventLog = [];

function spawnPerson() {
  if (simPersons.filter(p => p.active).length < 5) {
    simPersons.push(new SimPerson(simPersons.length));
  }
}

function drawDemo() {
  const cw = canvas.width, ch = canvas.height;
  ctx.clearRect(0, 0, cw, ch);

  // Background
  ctx.fillStyle = '#080814';
  ctx.fillRect(0, 0, cw, ch);

  // Draw zones
  ZONES.forEach(z => {
    ctx.fillStyle = z.color;
    ctx.fillRect(z.x * cw, z.y * ch, z.w * cw, z.h * ch);
    ctx.fillStyle = '#ffffff33';
    ctx.font = '9px monospace';
    ctx.fillText(z.name, z.x * cw + 4, ch - 8);
  });

  // Grid lines
  ctx.strokeStyle = '#ffffff11';
  ctx.lineWidth = 1;
  for (let x = 0; x < cw; x += 40) {
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, ch); ctx.stroke();
  }

  // Update + draw persons
  simFrame++;
  if (simFrame % 40 === 0) spawnPerson();
  simPersons = simPersons.filter(p => p.active || simPersons.length > 10);
  simPersons.forEach(p => { if (p.active) { p.update(); p.draw(cw, ch); } });

  // Frame counter
  ctx.fillStyle = '#ffffff44';
  ctx.font = '9px monospace';
  ctx.fillText(`frame: ${simFrame} | detections: ${simPersons.filter(p=>p.active).length} | YOLOv8n conf≥0.35`, 4, 12);

  requestAnimationFrame(drawDemo);
}

// Size canvas to container
function resizeCanvas() {
  canvas.width  = canvas.offsetWidth;
  canvas.height = 180;
}
resizeCanvas();
window.addEventListener('resize', resizeCanvas);
spawnPerson(); spawnPerson();
drawDemo();

// ── Floor Plan Person Dots ────────────────────────────────
// Animate person positions on the floor plan based on live event data
const ZONE_POSITIONS = {
  'ENTRY':     { left:'50%', top:'5%'  },
  'ENTRY_THRESHOLD': { left:'50%', top:'5%' },
  'MINIMALIST':{ left:'13%', top:'32%' },
  'SALM':      { left:'37%', top:'32%' },
  'EB_KOREAN': { left:'13%', top:'70%' },
  'SKINCARE':  { left:'37%', top:'70%' },
  'BILLING':   { left:'86%', top:'84%' },
  'MAKEUP':    { left:'60%', top:'50%' },
  'HAIRCARE':  { left:'60%', top:'30%' },
};
let activeDots = {};  // visitor_id → {el, lastSeen, zone}

function updateFloorPlan(events) {
  const plan = document.getElementById('floor-plan');
  if (!plan) return;
  const now = Date.now();

  events.forEach(e => {
    if (e.is_staff) return;
    const vid = e.visitor_id;
    const zone = e.zone_id || 'ENTRY';
    const pos = ZONE_POSITIONS[zone] || ZONE_POSITIONS['ENTRY'];

    // Highlight zone
    const zEl = document.getElementById('fz-' + zone);
    if (zEl) { zEl.classList.add('active'); setTimeout(() => zEl.classList.remove('active'), 2000); }

    if (!activeDots[vid]) {
      const dot = document.createElement('div');
      dot.className = 'person-dot';
      dot.title = vid;
      plan.appendChild(dot);
      activeDots[vid] = { el: dot, lastSeen: now, zone };
    }
    const d = activeDots[vid];
    d.el.style.left = pos.left;
    // jitter y slightly per visitor so dots don't stack
    const jitter = (Math.abs(vid.charCodeAt(vid.length-1) % 20) - 10);
    d.el.style.top = `calc(${pos.top} + ${jitter}px)`;
    d.lastSeen = now;
    d.zone = zone;

    if (e.event_type === 'EXIT' || e.event_type === 'BILLING_QUEUE_ABANDON') {
      setTimeout(() => {
        if (d.el && d.el.parentNode) d.el.parentNode.removeChild(d.el);
        delete activeDots[vid];
      }, 2000);
    }
  });

  // Remove stale dots (>60s old)
  Object.keys(activeDots).forEach(vid => {
    if (now - activeDots[vid].lastSeen > 60000) {
      if (activeDots[vid].el.parentNode) activeDots[vid].el.parentNode.removeChild(activeDots[vid].el);
      delete activeDots[vid];
    }
  });
}

function updatePipelineStats(metrics, recentEvts) {
  const events = recentEvts?.events ?? [];
  const typeCounts = {};
  events.forEach(e => { typeCounts[e.event_type] = (typeCounts[e.event_type]||0)+1; });

  const total = (metrics?.unique_visitors ?? 0);
  const setVal = (id, val, active) => {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
    const dot = document.getElementById(id.replace('ps-','ps-dot-'));
    if (dot) { dot.className = 'step-dot ' + (active ? 'step-active' : 'step-idle'); }
  };
  setVal('ps-events', `${recentEvts?.total ?? 0} events`, (recentEvts?.total ?? 0) > 0);
  setVal('ps-entry',  `${typeCounts['ENTRY']||0} ENTRY`, (typeCounts['ENTRY']||0)>0);
  setVal('ps-zone',   `${typeCounts['ZONE_DWELL']||0} DWELL`, (typeCounts['ZONE_DWELL']||0)>0);
  setVal('ps-billing',`${(typeCounts['BILLING_QUEUE_JOIN']||0)} JOIN`, (typeCounts['BILLING_QUEUE_JOIN']||0)>0);
  setVal('ps-reentry',`${typeCounts['REENTRY']||0} caught`, (typeCounts['REENTRY']||0)>0);
}

// ── Store switching ───────────────────────────────────────
let currentStore = '';

async function loadStores() {
  try {
    const h = await fetch('/api/store-health').then(r => r.json());
    const allStores = (h.stores || []).filter(s => s.total_events > 0);
    // Always show Store 1 first
    const ORDER = ['STORE_BLR_001', 'STORE_BLR_002'];
    const stores = [...allStores].sort((a, b) => {
      const ia = ORDER.indexOf(a.store_id), ib = ORDER.indexOf(b.store_id);
      return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
    });
    const sel = document.getElementById('store-sel');
    const labels = {
      'STORE_BLR_001': 'Store 1 — Bangalore (CAM 1/2/3/5)',
      'STORE_BLR_002': 'Store 2 — Brigade Bangalore',
    };
    if (!stores.length) {
      sel.innerHTML = '<option>No data — run pipeline first</option>'; return;
    }
    sel.innerHTML = stores.map((s, i) =>
      `<option value="${s.store_id}"${i===0?' selected':''}>${labels[s.store_id]||s.store_id} · ${s.total_events.toLocaleString()} events</option>`
    ).join('');
    currentStore = stores[0].store_id;  // Store 1 is now always first
    document.getElementById('ns-events').textContent =
      stores.reduce((a, s) => a + s.total_events, 0).toLocaleString();
    refresh();
    setInterval(refresh, 5000);
  } catch(e) {
    document.getElementById('live-status').textContent = 'API offline';
    document.getElementById('live-status').style.color = 'var(--red)';
  }
}

function switchStore(id) { if (id) { currentStore = id; refresh(); } }

// ── Main refresh ──────────────────────────────────────────
async function refresh() {
  if (!currentStore) return;
  try {
    const [metrics, funnel, anomalies, heatmap, hourly, zoneVisits, revenue, health, recentEvts] = await Promise.all([
      fetch(`/api/metrics?store=${currentStore}`).then(r=>r.json()),
      fetch(`/api/funnel?store=${currentStore}`).then(r=>r.json()),
      fetch(`/api/anomalies?store=${currentStore}`).then(r=>r.json()),
      fetch(`/api/heatmap?store=${currentStore}`).then(r=>r.json()),
      fetch(`/api/hourly?store=${currentStore}`).then(r=>r.json()),
      fetch(`/api/zone-visits?store=${currentStore}`).then(r=>r.json()),
      fetch(`/api/revenue?store=${currentStore}`).then(r=>r.json()),
      fetch('/api/store-health').then(r=>r.json()),
      fetch(`/api/recent-events?store=${currentStore}`).then(r=>r.json()),
    ]);

    const vis  = metrics.unique_visitors ?? 0;
    const conv = vis > 0 ? fmt((metrics.conversion_rate ?? 0) * 100) + '%' : '0.0%';
    const anom = anomalies.anomaly_count ?? 0;

    // KPIs
    document.getElementById('k-vis').textContent    = vis;
    document.getElementById('k-conv').textContent   = conv;
    document.getElementById('k-queue').textContent  = metrics.current_queue_depth ?? 0;
    document.getElementById('k-abandon').textContent = fmt((metrics.abandonment_rate ?? 0) * 100) + '%';
    document.getElementById('k-anom').textContent   = anom;

    // North Star
    const stage1 = funnel.funnel?.[0]?.visitors || 1;
    const stage3 = funnel.funnel?.[2]?.visitors || 0;
    const dropBilling = fmt((1 - stage3/stage1) * 100) + '%';
    const hotZone = heatmap.zones?.[0]?.zone_id ?? '—';
    document.getElementById('ns-conv').textContent = conv + ' Conversion';
    document.getElementById('ns-drop').textContent = dropBilling + ' before billing';
    document.getElementById('ns-zone').textContent = hotZone;

    // North Star action
    const hasQ  = anomalies.anomalies?.some(a => a.type === 'BILLING_QUEUE_SPIKE');
    const hasDZ = anomalies.anomalies?.some(a => a.type === 'DEAD_ZONE');
    const nsAct = hasQ  ? '🚨 Open extra billing counter — queue spike detected'
                : hasDZ ? '🔍 Redirect staff to dead zone to boost engagement'
                : '✅ Store operating normally — monitor conversion';
    document.getElementById('ns-action').textContent = nsAct;

    // Revenue
    const rev = revenue.total_revenue_inr ?? 0;
    document.getElementById('r-rev').textContent   = inr(rev);
    document.getElementById('r-txns').textContent  = (revenue.transaction_count ?? 0) + ' txns';
    document.getElementById('r-basket').textContent = inr(revenue.avg_basket_inr);
    document.getElementById('r-rpv').textContent    = vis > 0 && rev > 0 ? inr(rev/vis) : '—';

    // Health
    const staleCams = (anomalies.anomalies ?? []).filter(a => a.type === 'STALE_CAMERA_FEED').length;
    const totalCams = 4;
    const camColor = staleCams === 0 ? 'var(--green)' : staleCams < 2 ? 'var(--yellow)' : 'var(--red)';
    document.getElementById('h-cams').innerHTML  = `<span style="color:${camColor}">${totalCams-staleCams}/${totalCams} online</span>`;
    document.getElementById('h-db').innerHTML    = `<span style="color:var(--green)">${health.database ?? 'connected'}</span>`;
    document.getElementById('h-conf').innerHTML  = `<span style="color:var(--green)">${heatmap.data_confidence ?? 'LOW'}</span>`;
    const isHist = (anomalies.anomalies ?? []).some(a => a.details?.mode === 'historical');
    document.getElementById('h-mode').innerHTML  = isHist
      ? '<span style="color:var(--accent-light)">📼 Replay</span>'
      : '<span style="color:var(--green)">🔴 Live</span>';

    // Funnel
    document.getElementById('funnel-body').innerHTML = (funnel.funnel ?? []).map(s => {
      const pct = fmt(s.visitors / Math.max(stage1, 1) * 100, 0);
      return `<div class="funnel-stage">
        <div class="fn-label">${s.stage}. ${s.label}</div>
        <div class="fn-bar-wrap"><div class="fn-bar" style="width:${pct}%"></div></div>
        <div class="fn-stat">
          ${s.visitors}
          <span class="fn-pct">${pct}%</span>
          ${s.stage > 1 && s.drop_off_pct > 0 ? `<br><span class="fn-drop">-${fmt(s.drop_off_pct)}%</span>` : ''}
        </div>
      </div>`;
    }).join('');
    document.getElementById('fn-overall').textContent = funnel.overall_conversion_pct != null
      ? fmt(funnel.overall_conversion_pct, 1) + '% overall' : '—';

    // Heatmap
    document.getElementById('hm-grid').innerHTML = (heatmap.zones ?? []).slice(0, 6).map(z => {
      const c = heatColor(z.frequency_score);
      const dwell = z.avg_dwell_ms > 0 ? fmt(z.avg_dwell_ms/1000, 1)+'s' : '—';
      return `<div class="hm-cell">
        <div class="hm-zone">${z.zone_id}</div>
        <div class="hm-score" style="color:${c}">${z.frequency_score}</div>
        <div class="hm-dwell">${dwell} dwell · ${z.unique_visitors ?? 0} visitors</div>
        <div class="hm-bar"><div class="hm-bar-fill" style="width:${z.frequency_score}%;background:${c}"></div></div>
      </div>`;
    }).join('') || '<p style="font-size:.78rem;color:var(--muted)">No zone data</p>';
    document.getElementById('hm-conf').textContent = heatmap.data_confidence ?? 'LOW';

    // Anomalies
    const staleA = (anomalies.anomalies ?? []).filter(a => a.type === 'STALE_CAMERA_FEED');
    const otherA = (anomalies.anomalies ?? []).filter(a => a.type !== 'STALE_CAMERA_FEED');
    let anomHTML = otherA.map(a =>
      `<div class="anom ${a.severity}">
        <div class="anom-type">[${a.severity}] ${a.type}</div>
        <div class="anom-action">💡 ${a.suggested_action}</div>
      </div>`).join('');
    if (staleA.length > 0) {
      const cams = staleA.map(a => a.details?.camera_id ?? '?').join(', ');
      anomHTML += `<div class="anom CRITICAL">
        <div class="anom-type">[CRITICAL] STALE_CAMERA_FEED × ${staleA.length}</div>
        <div class="anom-action">📷 ${cams} — check connectivity</div>
      </div>`;
    }
    document.getElementById('anom-body').innerHTML = anomHTML ||
      '<p style="font-size:.78rem;color:var(--muted)">No anomalies detected</p>';

    // Charts
    barChart('txn-chart', hourly.hourly ?? [], 'transactions', 'var(--accent)');
    barChart('zone-chart', (zoneVisits.zones ?? []).slice(0, 10), 'events', 'var(--green)');

    // Live event feed
    const evts = recentEvts.events ?? [];
    document.getElementById('feed-count').textContent = `${recentEvts.total ?? 0} events shown`;
    document.getElementById('event-feed').innerHTML = evts.map(e => {
      const t = (e.timestamp || '').slice(11, 19);
      const zone = e.zone_id ? ` → ${e.zone_id}` : '';
      const staff = e.is_staff ? ' [STAFF]' : '';
      return `<div class="event-row">
        <span class="ev-badge ev-${e.event_type}">${e.event_type.replace('_',' ')}</span>
        <span class="ev-visitor">${e.visitor_id}${zone}${staff}</span>
        <span class="ev-conf">${fmt(e.confidence,2)}</span>
        <span class="ev-time">${t}</span>
      </div>`;
    }).join('') || '<p style="font-size:.78rem;color:var(--muted)">No events yet</p>';

    // Floor plan + pipeline stats
    updateFloorPlan(evts);
    updatePipelineStats(metrics, recentEvts);

    // Recommendations
    const queueD  = metrics.current_queue_depth ?? 0;
    const convNum = parseFloat(conv);
    const abandNum = (metrics.abandonment_rate ?? 0) * 100;
    document.getElementById('reco-1').textContent =
      queueD >= 5 ? `Queue depth ${queueD} — open second billing counter now` :
      convNum < 20 ? `Conversion ${conv} — investigate why ${fmt(100-convNum)}% leave without buying` :
      abandNum > 50 ? `${fmt(abandNum)}% abandonment — reduce checkout friction` :
      `All metrics healthy — queue at ${queueD} (normal)`;

    document.getElementById('reco-2').textContent =
      `${hotZone} gets most engagement — prime cross-sell opportunity with bundled offers`;

    document.getElementById('reco-3').textContent =
      convNum >= 30 ? `${conv} conversion exceeds ~20% retail avg — ${hotZone} display working` :
      `${hotZone} driving zone visits — reinforce with targeted promotions`;

    // Live status
    document.getElementById('live-status').textContent = isHist
      ? '📼 Historical — Apr 10, 2026' : '🔴 Live';
    document.getElementById('live-status').style.color = isHist ? 'var(--accent-light)' : 'var(--green)';
    document.getElementById('footer').textContent =
      `Last updated: ${new Date().toLocaleTimeString()} · Purplle Tech Challenge 2026 — Store Intelligence System`;

  } catch(e) {
    document.getElementById('live-status').textContent = '⟳ Reconnecting…';
    document.getElementById('live-status').style.color = 'var(--yellow)';
    console.error(e);
  }
}

loadStores();
</script>
</body>
</html>"""


@app.route("/")
def dashboard():
    return render_template_string(HTML)


@app.route("/api/metrics")
def proxy_metrics():
    sid = _store_from_request()
    try:
        r = requests.get(f"{API_URL}/stores/{sid}/metrics?date={DATA_DATE}", timeout=5)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route("/api/funnel")
def proxy_funnel():
    sid = _store_from_request()
    try:
        r = requests.get(f"{API_URL}/stores/{sid}/funnel?date={DATA_DATE}", timeout=5)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route("/api/anomalies")
def proxy_anomalies():
    sid = _store_from_request()
    try:
        r = requests.get(f"{API_URL}/stores/{sid}/anomalies?date={DATA_DATE}", timeout=5)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route("/api/heatmap")
def proxy_heatmap():
    sid = _store_from_request()
    try:
        r = requests.get(f"{API_URL}/stores/{sid}/heatmap?date={DATA_DATE}", timeout=5)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route("/api/hourly")
def proxy_hourly():
    sid = _store_from_request()
    try:
        r = requests.get(f"{API_URL}/stores/{sid}/hourly?date={DATA_DATE}", timeout=5)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route("/api/zone-visits")
def proxy_zone_visits():
    sid = _store_from_request()
    try:
        r = requests.get(f"{API_URL}/stores/{sid}/zone-visits?date={DATA_DATE}", timeout=5)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route("/api/revenue")
def proxy_revenue():
    sid = _store_from_request()
    try:
        r = requests.get(f"{API_URL}/stores/{sid}/revenue?date={DATA_DATE}", timeout=5)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route("/api/store-health")
def proxy_store_health():
    try:
        r = requests.get(f"{API_URL}/health", timeout=5)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route("/api/recent-events")
def proxy_recent_events():
    sid = _store_from_request()
    try:
        url = f"{API_URL}/events/recent?limit=25"
        if sid:
            url += f"&store_id={sid}"
        r = requests.get(url, timeout=5)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e), "events": [], "total": 0}), 503


@app.route("/api/stores")
def list_stores():
    return jsonify({"stores": [
        {"id": "STORE_BLR_002", "name": "Brigade Bangalore"},
        {"id": "STORE_BLR_001", "name": "Store 1 Bangalore"},
    ]})


if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    print(f"Dashboard running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
