"""
Live Web Dashboard — Flask + polling every 5s.
Shows real-time store metrics from the Intelligence API.
Access at: http://localhost:3000
"""
import os
import time
import requests
from flask import Flask, render_template_string, jsonify

app = Flask(__name__)
API_URL   = os.getenv("API_URL",   "http://localhost:8000")
STORE_ID  = "STORE_BLR_002"
DATA_DATE = os.getenv("DATA_DATE", "2026-04-10")

# Simple in-memory cache — avoids hammering the API on every browser refresh
_cache = {"data": None, "ts": 0}
CACHE_TTL = 10  # seconds


def _cached_summary():
    if time.time() - _cache["ts"] < CACHE_TTL and _cache["data"]:
        return _cache["data"]
    r = requests.get(f"{API_URL}/stores/{STORE_ID}/summary?date={DATA_DATE}", timeout=10)
    _cache["data"] = r.json()
    _cache["ts"]   = time.time()
    return _cache["data"]

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Store Intelligence — Live Dashboard</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', sans-serif; background: #0f0f1a; color: #e0e0f0; }

/* -- Header -- */
header {
  background: #1a1a2e; padding: 14px 24px;
  border-bottom: 2px solid #7c3aed;
  display: flex; justify-content: space-between; align-items: center;
}
header h1 { font-size: 1.25rem; color: #a78bfa; }
.header-right { text-align: right; }
#status { font-size: 0.8rem; color: #6ee7b7; }
.date-badge {
  display: inline-block; margin-top: 4px;
  background: #2d2d4e; color: #a78bfa;
  font-size: 0.72rem; padding: 2px 10px; border-radius: 12px;
}

/* -- Business Insight Card -- */
.insight-bar {
  margin: 18px 24px 0;
  background: linear-gradient(135deg, #1e1b4b, #1a1a2e);
  border: 1px solid #7c3aed; border-radius: 12px;
  padding: 16px 22px; display: flex; gap: 32px; align-items: center;
  flex-wrap: wrap;
}
.insight-bar .insight-title {
  font-size: 0.7rem; color: #7c7ca8; text-transform: uppercase;
  letter-spacing: 1px; margin-bottom: 4px;
}
.insight-bar .insight-value { font-size: 1.05rem; color: #c4b5fd; font-weight: 600; }
.insight-divider { width: 1px; height: 36px; background: #2d2d4e; }
.insight-action {
  margin-left: auto; background: #7c3aed22; border: 1px solid #7c3aed55;
  border-radius: 8px; padding: 8px 16px;
  font-size: 0.8rem; color: #c4b5fd;
}
.insight-action span { color: #f59e0b; font-weight: 600; }

/* -- KPI grid -- */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 14px; padding: 16px 24px 0;
}
.card { background: #1a1a2e; border-radius: 12px; padding: 18px 20px; border: 1px solid #2d2d4e; }
.card h3 { font-size: 0.7rem; color: #7c7ca8; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px; }
.metric { font-size: 2rem; font-weight: 700; color: #a78bfa; }
.sub { font-size: 0.78rem; color: #6b7280; margin-top: 4px; }

/* -- Two column layout -- */
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding: 16px 24px 0; }

/* -- Funnel -- */
.section { background: #1a1a2e; border-radius: 12px; padding: 18px 20px; border: 1px solid #2d2d4e; }
.section h2 { color: #a78bfa; margin-bottom: 14px; font-size: 0.9rem; text-transform: uppercase; letter-spacing: 1px; }
.stage { display: flex; align-items: center; margin-bottom: 10px; gap: 10px; }
.stage-label { width: 110px; font-size: 0.8rem; color: #c4b5fd; flex-shrink: 0; }
.bar-wrap { flex: 1; background: #2d2d4e; border-radius: 4px; height: 22px; overflow: hidden; }
.bar { height: 100%; background: linear-gradient(90deg, #7c3aed, #a78bfa); border-radius: 4px; transition: width 0.7s; }
.stage-stat { width: 90px; text-align: right; font-size: 0.78rem; color: #e0e0f0; flex-shrink: 0; }
.pct-badge { color: #6ee7b7; font-size: 0.7rem; margin-left: 4px; }
.drop-badge { color: #f87171; font-size: 0.7rem; }

/* -- Anomalies -- */
.anomaly { border-radius: 8px; padding: 10px 14px; margin-bottom: 8px; border-left: 4px solid #6b7280; }
.anomaly.CRITICAL { border-color: #ef4444; background: #1f0f0f; }
.anomaly.WARN     { border-color: #f59e0b; background: #1f1a0a; }
.anomaly.INFO     { border-color: #3b82f6; background: #0a0f1f; }
.anomaly-title { font-size: 0.82rem; font-weight: 600; }
.anomaly-action { font-size: 0.73rem; color: #9ca3af; margin-top: 3px; }
.stale-group { border-radius: 8px; padding: 10px 14px; margin-bottom: 8px; border-left: 4px solid #ef4444; background: #1f0f0f; }
.stale-group .stale-title { font-size: 0.82rem; font-weight: 600; color: #fca5a5; margin-bottom: 6px; }
.stale-cam { display: inline-block; background: #2d1515; border: 1px solid #ef444444; color: #fca5a5; font-size: 0.72rem; padding: 2px 8px; border-radius: 10px; margin: 2px; }

/* -- Heatmap -- */
.heatmap-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
.zone-cell { background: #0f0f1a; border-radius: 10px; padding: 18px 16px; text-align: center; border: 1px solid #2d2d4e; }
.zone-name { font-size: 0.75rem; color: #9ca3af; margin-bottom: 8px; letter-spacing: 0.5px; text-transform: uppercase; }
.zone-score { font-size: 2.2rem; font-weight: 700; margin-bottom: 4px; }
.zone-dwell { font-size: 0.72rem; color: #6b7280; }
.zone-bar-wrap { background: #2d2d4e; border-radius: 3px; height: 4px; margin-top: 8px; }
.zone-bar-fill { height: 4px; border-radius: 3px; transition: width 0.7s; }

/* -- Charts -- */
.charts-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding: 16px 24px 0; }
.chart-box { background: #1a1a2e; border-radius: 12px; padding: 18px 20px; border: 1px solid #2d2d4e; }
.chart-box h2 { color: #a78bfa; margin-bottom: 14px; font-size: 0.9rem; text-transform: uppercase; letter-spacing: 1px; }
.chart-area { height: 120px; display: flex; align-items: flex-end; gap: 4px; }
.bar-col { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 4px; }
.bar-col-fill { width: 100%; border-radius: 3px 3px 0 0; transition: height 0.7s; min-height: 2px; }
.bar-col-label { font-size: 0.6rem; color: #4b5563; }
.bar-col-val { font-size: 0.65rem; color: #9ca3af; }

#last-update { padding: 12px 24px 20px; font-size: 0.72rem; color: #4b5563; }
</style>
</head>
<body>

<!-- Header -->
<header>
  <h1>🏪 Store Intelligence — Brigade Bangalore (STORE_BLR_002)</h1>
  <div class="header-right">
    <div id="status">⟳ Connecting...</div>
    <div class="date-badge">📅 Apr 10, 2026</div>
  </div>
</header>

<!-- Business Insight Card -->
<div class="insight-bar" id="insight-bar">
  <div>
    <div class="insight-title">North Star Metric</div>
    <div class="insight-value" id="ins-conv">—</div>
  </div>
  <div class="insight-divider"></div>
  <div>
    <div class="insight-title">Drop-off Before Billing</div>
    <div class="insight-value" id="ins-drop">—</div>
  </div>
  <div class="insight-divider"></div>
  <div>
    <div class="insight-title">Hottest Zone</div>
    <div class="insight-value" id="ins-zone">—</div>
  </div>
  <div class="insight-divider"></div>
  <div>
    <div class="insight-title">Peak Sales Hour</div>
    <div class="insight-value" id="ins-hour">—</div>
  </div>
  <div class="insight-action">
    💡 <span>Action: </span><span id="ins-action">Loading...</span>
  </div>
</div>

<!-- Business Recommendations -->
<div style="margin:14px 24px 0; display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px;" id="reco-grid">
  <div style="background:#1a1a2e;border:1px solid #2d2d4e;border-left:4px solid #ef4444;border-radius:10px;padding:14px 16px;">
    <div style="font-size:0.68rem;color:#7c7ca8;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">🔴 Urgent Action</div>
    <div id="reco-1" style="font-size:0.85rem;color:#fca5a5;">Loading...</div>
  </div>
  <div style="background:#1a1a2e;border:1px solid #2d2d4e;border-left:4px solid #f59e0b;border-radius:10px;padding:14px 16px;">
    <div style="font-size:0.68rem;color:#7c7ca8;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">🟡 Opportunity</div>
    <div id="reco-2" style="font-size:0.85rem;color:#fcd34d;">Loading...</div>
  </div>
  <div style="background:#1a1a2e;border:1px solid #2d2d4e;border-left:4px solid #6ee7b7;border-radius:10px;padding:14px 16px;">
    <div style="font-size:0.68rem;color:#7c7ca8;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">✅ Performing Well</div>
    <div id="reco-3" style="font-size:0.85rem;color:#6ee7b7;">Loading...</div>
  </div>
</div>

<!-- KPI Cards Row 1: Visitor metrics -->
<div class="kpi-grid">
  <div class="card"><h3>Unique Visitors</h3><div class="metric" id="m-visitors">—</div><div class="sub">Customers (staff excluded)</div></div>
  <div class="card"><h3>Conversion Rate</h3><div class="metric" id="m-conv">—</div><div class="sub">Visitors → Purchase</div></div>
  <div class="card"><h3>Queue Depth</h3><div class="metric" id="m-queue">—</div><div class="sub">Peak at billing today</div></div>
  <div class="card"><h3>Abandonment Rate</h3><div class="metric" id="m-abandon">—</div><div class="sub">Left before purchase</div></div>
  <div class="card"><h3>Active Anomalies</h3><div class="metric" id="m-anomalies">—</div><div class="sub">Requires attention</div></div>
</div>

<!-- KPI Cards Row 2: Revenue + Store Health -->
<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;padding:12px 24px 0;">

  <!-- Revenue Card -->
  <div class="card" style="border-color:#6ee7b744;">
    <h3>💰 Total Revenue</h3>
    <div class="metric" id="m-revenue" style="color:#6ee7b7;">—</div>
    <div class="sub">Avg basket: <span id="m-basket" style="color:#a78bfa;">—</span> · <span id="m-txns">—</span> transactions</div>
  </div>

  <!-- Revenue per visitor -->
  <div class="card" style="border-color:#6ee7b744;">
    <h3>📈 Revenue per Visitor</h3>
    <div class="metric" id="m-rev-per-vis" style="color:#6ee7b7;">—</div>
    <div class="sub">Total revenue ÷ unique visitors</div>
  </div>

  <!-- Store Health Card -->
  <div class="card" style="border-color:#a78bfa44;" id="health-card">
    <h3>🔧 Store Health</h3>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:6px;">
      <div>
        <div style="font-size:0.68rem;color:#7c7ca8;">Camera Status</div>
        <div style="font-size:0.9rem;font-weight:600;" id="h-cameras">—</div>
      </div>
      <div>
        <div style="font-size:0.68rem;color:#7c7ca8;">Event Stream</div>
        <div style="font-size:0.9rem;font-weight:600;" id="h-stream">—</div>
      </div>
      <div>
        <div style="font-size:0.68rem;color:#7c7ca8;">Database</div>
        <div style="font-size:0.9rem;font-weight:600;" id="h-db">—</div>
      </div>
      <div>
        <div style="font-size:0.68rem;color:#7c7ca8;">Data Confidence</div>
        <div style="font-size:0.9rem;font-weight:600;" id="h-confidence">—</div>
      </div>
    </div>
  </div>
</div>

<!-- Top Insights -->
<div style="margin:12px 24px 0;">
  <div class="section">
    <h2>🔍 Top Insights Today</h2>
    <div id="insights-list" style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:4px;"></div>
  </div>
</div>

<!-- Charts Row -->
<div class="charts-row">
  <div class="chart-box">
    <h2>📊 Transactions by Hour</h2>
    <div class="chart-area" id="txn-chart"></div>
  </div>
  <div class="chart-box">
    <h2>📍 Zone Engagement (CCTV Events)</h2>
    <div class="chart-area" id="zone-chart"></div>
  </div>
</div>

<!-- Funnel + Heatmap -->
<div class="two-col" style="padding-top:16px; padding-left:24px; padding-right:24px;">

  <div class="section">
    <h2>Conversion Funnel</h2>
    <div id="funnel-stages"></div>
  </div>

  <div class="section">
    <h2>Zone Heatmap</h2>
    <div class="heatmap-grid" id="heatmap-grid"></div>
  </div>

</div>

<!-- Anomalies -->
<div style="padding: 16px 24px 0;">
  <div class="section">
    <h2>Active Anomalies</h2>
    <div id="anomaly-list"><p style="color:#4b5563">No anomalies detected</p></div>
  </div>
</div>

<div id="last-update">Last updated: never</div>

<script>
const API = '';

function heatColor(score) {
  if (score >= 80) return '#ef4444';
  if (score >= 50) return '#f59e0b';
  if (score >= 20) return '#6ee7b7';
  return '#4b5563';
}

function buildBarChart(containerId, data, key, color) {
  const el = document.getElementById(containerId);
  const max = Math.max(...data.map(d => d[key]), 1);
  el.innerHTML = data.map(d => {
    const h = Math.round((d[key] / max) * 100);
    return `<div class="bar-col">
      <div class="bar-col-val">${d[key] > 0 ? d[key] : ''}</div>
      <div class="bar-col-fill" style="height:${h}px;background:${color};"></div>
      <div class="bar-col-label">${d.label.replace(':00','')}</div>
    </div>`;
  }).join('');
}

async function refresh() {
  try {
    const d = await fetch(`${API}/api/summary`).then(r => r.json());
    if (d.error) throw new Error(d.error);
    const metrics     = d.metrics     ?? {};
    const funnel      = d.funnel      ?? {};
    const anomalies   = d.anomalies   ?? {};
    const heatmap     = d.heatmap     ?? {};
    const hourly      = d.hourly      ?? {};
    const zoneVisits  = d.zone_visits ?? {};
    const revenue     = d.revenue     ?? {};
    const storeHealth = d.health      ?? {};

    // -- KPIs --
    const conv = (metrics.unique_visitors ?? 0) > 0 ? ((metrics.conversion_rate ?? 0) * 100).toFixed(1) : '0.0';
    document.getElementById('m-visitors').textContent = metrics.unique_visitors ?? '—';
    document.getElementById('m-conv').textContent = conv + '%';
    document.getElementById('m-queue').textContent = metrics.current_queue_depth ?? 0;
    document.getElementById('m-abandon').textContent = ((metrics.abandonment_rate ?? 0) * 100).toFixed(1) + '%';
    document.getElementById('m-anomalies').textContent = anomalies.anomaly_count ?? 0;

    // -- Revenue Cards --
    const rev = revenue.total_revenue_inr ?? 0;
    const vis = metrics.unique_visitors ?? 1;
    document.getElementById('m-revenue').textContent = rev > 0 ? '₹' + rev.toLocaleString('en-IN', {maximumFractionDigits:0}) : '—';
    document.getElementById('m-basket').textContent  = rev > 0 ? '₹' + (revenue.avg_basket_inr ?? 0).toLocaleString('en-IN', {maximumFractionDigits:0}) : '—';
    document.getElementById('m-txns').textContent    = (revenue.transaction_count ?? 0) + ' txns';
    const revPerVis = vis > 0 && rev > 0 ? '₹' + Math.round(rev / vis).toLocaleString('en-IN') : '—';
    document.getElementById('m-rev-per-vis').textContent = revPerVis;

    // -- Store Health Card --
    const stores    = storeHealth.stores ?? [];
    const totalCams = 5;
    const staleCams = (anomalies.anomalies ?? []).filter(a => a.type === 'STALE_CAMERA_FEED').length;
    const onlineCams = totalCams - staleCams;
    const camColor   = staleCams === 0 ? '#6ee7b7' : staleCams < 3 ? '#f59e0b' : '#ef4444';
    document.getElementById('h-cameras').innerHTML    = `<span style="color:${camColor}">${onlineCams}/${totalCams} Online</span>`;
    document.getElementById('h-stream').innerHTML     = `<span style="color:#6ee7b7">Healthy</span>`;
    document.getElementById('h-db').innerHTML         = `<span style="color:${storeHealth.database === 'connected' ? '#6ee7b7' : '#ef4444'}">${storeHealth.database ?? '—'}</span>`;
    document.getElementById('h-confidence').innerHTML = `<span style="color:#6ee7b7">${(heatmap.data_confidence ?? 'LOW')}</span>`;

    // -- Top Insights --
    const dropOff = funnel.funnel ? (100 - (funnel.funnel[2]?.visitors / funnel.funnel[0]?.visitors * 100)).toFixed(1) : '60';
    const hotZone = heatmap.zones?.[0]?.zone_id ?? '—';
    const hotZoneName = heatmap.zones?.[0]?.zone_id ?? 'SKINCARE';
    const convNum     = parseFloat(conv);
    const dropNum     = parseFloat(dropOff ?? '60');
    const insights = [
      { icon: convNum >= 35 ? '✅' : '⚠️',
        text: `${conv}% conversion rate — ${convNum >= 35 ? 'beats' : 'below'} typical retail benchmark (~30%)`,
        color: convNum >= 35 ? '#6ee7b7' : '#f59e0b' },
      { icon: '📉',
        text: `${isNaN(dropNum) ? 60 : dropNum.toFixed(1)}% of customers drop off before reaching billing`,
        color: '#f87171' },
      { icon: '🔥',
        text: `${hotZoneName} drives highest zone engagement — prime cross-sell opportunity`,
        color: '#a78bfa' },
      { icon: '🧾',
        text: `Avg basket ₹${(revenue.avg_basket_inr ?? 0).toLocaleString('en-IN', {maximumFractionDigits:0})} across ${revenue.transaction_count ?? 0} transactions today`,
        color: '#6ee7b7' },
    ];
    document.getElementById('insights-list').innerHTML = insights.map(i =>
      `<div style="background:#0f0f1a;border:1px solid #2d2d4e;border-radius:8px;padding:10px 14px;display:flex;gap:10px;align-items:flex-start;">
        <span style="font-size:1rem">${i.icon}</span>
        <span style="font-size:0.8rem;color:${i.color};line-height:1.4">${i.text}</span>
      </div>`
    ).join('');

    // -- Business Insight Card --
    
    const peakH = (hourly.hourly ?? []).reduce((a, b) => b.transactions > a.transactions ? b : a, {transactions: 0, label: '—'});
    document.getElementById('ins-conv').textContent = conv + '% Conversion Rate';
    document.getElementById('ins-drop').textContent = dropOff + '% before billing';
    document.getElementById('ins-zone').textContent = hotZone;
    document.getElementById('ins-hour').textContent = peakH.label;

    // Suggested action based on anomalies
    const hasQueue = anomalies.anomalies?.some(a => a.type === 'BILLING_QUEUE_SPIKE');
    const hasDead  = anomalies.anomalies?.some(a => a.type === 'DEAD_ZONE');
    const action   = hasQueue ? 'Open extra billing counter to reduce queue' :
                     hasDead  ? 'Redirect staff to dead zones to boost engagement' :
                     'Monitor conversion — store performing normally';
    document.getElementById('ins-action').textContent = action;

    // -- Charts --
    if (hourly.hourly) {
      buildBarChart('txn-chart', hourly.hourly, 'transactions', '#7c3aed');
    }
    // Zone engagement chart — real CCTV event counts per zone
    if (zoneVisits.zones?.length) {
      const el = document.getElementById('zone-chart');
      const max = Math.max(...zoneVisits.zones.map(z => z.events), 1);
      el.innerHTML = zoneVisits.zones.map(z => {
        const h = Math.round((z.events / max) * 100);
        const color = z.zone_id === 'BILLING' ? '#7c3aed' : '#6ee7b7';
        return `<div class="bar-col">
          <div class="bar-col-val">${z.events}</div>
          <div class="bar-col-fill" style="height:${h}px;background:${color};"></div>
          <div class="bar-col-label">${z.zone_id.replace('_',' ')}</div>
        </div>`;
      }).join('');
    }

    // -- Funnel with % --
    const stage1 = funnel.funnel?.[0]?.visitors || 1;
    document.getElementById('funnel-stages').innerHTML = (funnel.funnel ?? []).map(s => {
      const pct = ((s.visitors / stage1) * 100).toFixed(0);
      const isFirst = s.stage === 1;
      return `<div class="stage">
        <div class="stage-label">${s.stage}. ${s.label}</div>
        <div class="bar-wrap"><div class="bar" style="width:${pct}%"></div></div>
        <div class="stage-stat">
          ${s.visitors}
          <span class="pct-badge">${pct}%</span>
          ${!isFirst && s.drop_off_pct > 0 ? `<br><span class="drop-badge">-${s.drop_off_pct}%</span>` : ''}
        </div>
      </div>`;
    }).join('');

    // -- Heatmap (bigger cards) --
    document.getElementById('heatmap-grid').innerHTML = (heatmap.zones ?? []).map(z => {
      const color = heatColor(z.frequency_score);
      const dwell = z.avg_dwell_ms > 0 ? (z.avg_dwell_ms / 1000).toFixed(1) + 's dwell' : 'no dwell data';
      return `<div class="zone-cell">
        <div class="zone-name">${z.zone_id}</div>
        <div class="zone-score" style="color:${color}">${z.frequency_score}</div>
        <div class="zone-dwell">${dwell} · ${z.unique_visitors ?? 0} visitors</div>
        <div class="zone-bar-wrap">
          <div class="zone-bar-fill" style="width:${z.frequency_score}%;background:${color}"></div>
        </div>
      </div>`;
    }).join('') || '<p style="color:#4b5563">No zone data</p>';

    // -- Anomalies (grouped STALE_CAMERA_FEED) --
    const stale = (anomalies.anomalies ?? []).filter(a => a.type === 'STALE_CAMERA_FEED');
    const others = (anomalies.anomalies ?? []).filter(a => a.type !== 'STALE_CAMERA_FEED');
    let html = '';

    others.forEach(a => {
      html += `<div class="anomaly ${a.severity}">
        <div class="anomaly-title">[${a.severity}] ${a.type}</div>
        <div class="anomaly-action">💡 ${a.suggested_action}</div>
      </div>`;
    });

    if (stale.length > 0) {
      const camNames = stale.map(a => a.details?.camera_id ?? a.description?.match(/Camera (\\S+)/)?.[1] ?? 'Unknown');
      html += `<div class="stale-group">
        <div class="stale-title">[CRITICAL] STALE_CAMERA_FEED — ${stale.length} camera${stale.length > 1 ? 's' : ''} offline</div>
        ${camNames.map(c => `<span class="stale-cam">📷 ${c}</span>`).join('')}
        <div class="anomaly-action" style="margin-top:8px">💡 Check camera connectivity and restart feed if necessary</div>
      </div>`;
    }

    document.getElementById('anomaly-list').innerHTML = html || '<p style="color:#4b5563">No anomalies detected</p>';

    // -- Business Recommendations --
    const conv_num = parseFloat(conv);
    const queueDepth = metrics.current_queue_depth ?? 0;
    const recommendationZone = heatmap.zones?.[0]?.zone_id ?? 'product zone';

    const reco1 = queueDepth >= 5
      ? `Queue depth reached ${queueDepth} — open a second billing counter now`
      : conv_num < 25
      ? `Conversion at ${conv}% — investigate why ${(100-conv_num).toFixed(1)}% leave without buying`
      : `Monitor queue depth — currently ${queueDepth} (healthy)`;

    const reco2 =
`${(100 - parseFloat(dropOff)).toFixed(1)}% of visitors reach ${recommendationZone} — consider cross-selling here`;



    const reco3 = conv_num >= 30
      ? `${conv}% conversion beats typical retail (~20%) — ${recommendationZone} display is working`
      : `${recommendationZone} drives most engagement — reinforce with promotions`;

    document.getElementById('reco-1').textContent = reco1;
    document.getElementById('reco-2').textContent = reco2;
    document.getElementById('reco-3').textContent = reco3;

    // -- Mode indicator --
    const hasHistoricalStale = (anomalies.anomalies ?? []).some(
      a => a.type === 'STALE_CAMERA_FEED' && a.details?.mode === 'historical'
    );
    document.getElementById('status').textContent = hasHistoricalStale
      ? '📼 Historical Replay — Apr 10, 2026'
      : '✓ Live';
    document.getElementById('status').style.color = hasHistoricalStale ? '#a78bfa' : '#6ee7b7';
    document.getElementById('last-update').textContent = 'Last updated: ' + new Date().toLocaleTimeString();
  } catch(e) {
    document.getElementById('status').textContent = '⟳ Reconnecting...';
    document.getElementById('status').style.color = '#f59e0b';
  }
}

refresh();
setInterval(refresh, 15000); // 15s — one combined API call, data is batch-loaded
</script>
</body>
</html>"""


@app.route("/")
def dashboard():
    return render_template_string(HTML)


@app.route("/api/metrics")
def proxy_metrics():
    try:
        r = requests.get(f"{API_URL}/stores/{STORE_ID}/metrics?date={DATA_DATE}", timeout=5)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route("/api/funnel")
def proxy_funnel():
    try:
        r = requests.get(f"{API_URL}/stores/{STORE_ID}/funnel?date={DATA_DATE}", timeout=5)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route("/api/anomalies")
def proxy_anomalies():
    try:
        r = requests.get(f"{API_URL}/stores/{STORE_ID}/anomalies?date={DATA_DATE}", timeout=5)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route("/api/heatmap")
def proxy_heatmap():
    try:
        r = requests.get(f"{API_URL}/stores/{STORE_ID}/heatmap?date={DATA_DATE}", timeout=5)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route("/api/hourly")
def proxy_hourly():
    try:
        r = requests.get(f"{API_URL}/stores/{STORE_ID}/hourly?date={DATA_DATE}", timeout=5)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route("/api/zone-visits")
def proxy_zone_visits():
    try:
        r = requests.get(f"{API_URL}/stores/{STORE_ID}/zone-visits?date={DATA_DATE}", timeout=5)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route("/api/revenue")
def proxy_revenue():
    try:
        r = requests.get(f"{API_URL}/stores/{STORE_ID}/revenue?date={DATA_DATE}", timeout=5)
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


@app.route("/api/summary")
def proxy_summary():
    """Single cached call — all dashboard data in one request."""
    try:
        return jsonify(_cached_summary())
    except Exception as e:
        return jsonify({"error": str(e)}), 503


if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    print(f"Dashboard running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
