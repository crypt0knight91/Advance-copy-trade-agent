"""
dashboard.py — Mobile-first dark Flask dashboard.
Access: http://localhost:8080
Shows: live positions, whale trust scores, today's PnL, audit log tail.
"""
import json, time, threading
from datetime import datetime, timezone
from typing import Optional

import config

try:
    from flask import Flask, jsonify, render_template_string, request
    FLASK_OK = True
except ImportError:
    FLASK_OK = False

# Shared state reference (set by main.py)
_state_ref: Optional[dict] = None

def set_state_ref(ref: dict):
    global _state_ref
    _state_ref = ref

# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD HTML
# ─────────────────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Whale Mirror v2</title>
<style>
:root{--bg:#09090f;--s1:#0f1019;--s2:#161724;--br:#22243a;
  --t1:#eeeef8;--t2:#7e80a6;--t3:#3a3c5a;
  --g:#4ade80;--r:#f87171;--b:#6ee7ff;--y:#fbbf24;--p:#a78bfa;
  --mono:'Courier New',monospace}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
body{background:var(--bg);color:var(--t1);font-family:var(--mono);font-size:12px;
  padding-bottom:60px}
body::before{content:'';position:fixed;inset:0;
  background:repeating-linear-gradient(0deg,rgba(110,231,255,.012) 0,
  rgba(110,231,255,.012) 1px,transparent 1px,transparent 36px),
  repeating-linear-gradient(90deg,rgba(110,231,255,.012) 0,
  rgba(110,231,255,.012) 1px,transparent 1px,transparent 36px);
  pointer-events:none;z-index:0}
.wrap{position:relative;z-index:1;max-width:680px;margin:0 auto;padding:0 12px}

/* header */
.hdr{padding:14px 0 10px;border-bottom:1px solid var(--br);margin-bottom:14px;
  display:flex;justify-content:space-between;align-items:center}
.hdr h1{font-size:14px;font-weight:700;letter-spacing:.06em}
.mode-pill{font-size:9px;padding:3px 8px;border-radius:8px;font-weight:700}
.mode-dry{background:rgba(251,191,36,.12);color:var(--y);border:1px solid rgba(251,191,36,.3)}
.mode-live{background:rgba(248,113,113,.12);color:var(--r);border:1px solid rgba(248,113,113,.3)}
.dot{width:6px;height:6px;border-radius:50%;background:var(--g);
  animation:pulse 2s ease-in-out infinite;display:inline-block;margin-right:6px}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}

/* summary bar */
.sbar{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-bottom:14px}
.scard{background:var(--s1);border:1px solid var(--br);border-radius:8px;
  padding:10px 8px;text-align:center}
.sv{font-size:18px;font-weight:700;display:block;line-height:1}
.sl{font-size:8px;color:var(--t3);text-transform:uppercase;letter-spacing:.1em;margin-top:3px;display:block}

/* section */
.sec{margin-bottom:14px}
.sec-title{font-size:9px;letter-spacing:.2em;text-transform:uppercase;color:var(--t3);
  padding:8px 0 6px;border-bottom:1px solid var(--br);margin-bottom:8px;
  display:flex;justify-content:space-between}
.empty{color:var(--t3);font-size:11px;padding:12px;text-align:center;
  border:1px dashed var(--br);border-radius:6px}

/* position card */
.pos{background:var(--s1);border:1px solid var(--br);border-radius:8px;
  padding:10px 12px;margin-bottom:8px;position:relative}
.pos::before{content:'';position:absolute;left:0;top:0;bottom:0;width:3px;border-radius:8px 0 0 8px}
.pos.pos-up::before{background:var(--g)}
.pos.pos-dn::before{background:var(--r)}
.pos.pos-neu::before{background:var(--t3)}
.pos-top{display:flex;justify-content:space-between;margin-bottom:6px;align-items:center}
.pos-whale{font-size:11px;font-weight:700;color:var(--b)}
.pos-whale a{color:var(--b);text-decoration:none}
.pos-whale a:hover{text-decoration:underline}
.pos-pnl{font-size:14px;font-weight:700}
.pos-up .pos-pnl{color:var(--g)}
.pos-dn .pos-pnl{color:var(--r)}
.pos-neu .pos-pnl{color:var(--t2)}
.pos-mkt{font-size:10px;color:var(--t2);margin-bottom:6px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pos-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:4px}
.pf{font-size:9px;color:var(--t3);text-transform:uppercase}
.pv{font-size:10px;font-weight:700;margin-top:2px}

/* whale score table */
.wtable{width:100%;border-collapse:collapse;font-size:10px}
.wtable th{color:var(--t3);font-weight:400;letter-spacing:.08em;text-transform:uppercase;
  padding:5px 6px;text-align:left;border-bottom:1px solid var(--br)}
.wtable td{padding:6px 6px;border-bottom:1px solid var(--s2);vertical-align:middle}
.wtable tr:hover td{background:var(--s2)}
.wtable tr:last-child td{border-bottom:none}
.trust-bar{display:inline-block;height:4px;border-radius:2px;background:var(--b);opacity:.7}
.active-dot{width:6px;height:6px;border-radius:50%;display:inline-block}
.active-dot.on{background:var(--g)}.active-dot.off{background:var(--r)}

/* audit log */
.alog{font-size:9px;color:var(--t2);background:var(--s1);border:1px solid var(--br);
  border-radius:6px;padding:8px;max-height:180px;overflow-y:auto;line-height:1.7}
.alog-line{border-bottom:1px solid var(--br);padding:2px 0}

/* kill switch */
.kill-btn{width:100%;padding:10px;background:rgba(248,113,113,.12);
  border:1px solid rgba(248,113,113,.3);color:var(--r);border-radius:6px;
  font-size:11px;font-weight:700;cursor:pointer;letter-spacing:.05em;
  font-family:var(--mono);margin-top:10px}
.kill-btn:hover{background:rgba(248,113,113,.25)}

/* refresh badge */
.refresh{font-size:9px;color:var(--t3)}
</style>
</head>
<body>
<div class="wrap">

<div class="hdr">
  <div style="display:flex;align-items:center;gap:8px">
    <span class="dot"></span>
    <h1>WHALE MIRROR v2</h1>
  </div>
  <div style="display:flex;align-items:center;gap:8px">
    <span id="mode-pill" class="mode-pill">...</span>
    <span class="refresh" id="ref-ts">–</span>
  </div>
</div>

<!-- summary -->
<div class="sbar">
  <div class="scard">
    <span class="sv" id="s-pos" style="color:var(--b)">–</span>
    <span class="sl">Positions</span>
  </div>
  <div class="scard">
    <span class="sv" id="s-pnl">–</span>
    <span class="sl">Today PnL</span>
  </div>
  <div class="scard">
    <span class="sv" id="s-dl" style="color:var(--y)">–</span>
    <span class="sl">Daily Loss</span>
  </div>
  <div class="scard">
    <span class="sv" id="s-whales" style="color:var(--p)">–</span>
    <span class="sl">Whales Active</span>
  </div>
</div>

<!-- positions -->
<div class="sec">
  <div class="sec-title">
    <span>Live Positions</span>
    <span id="pos-count" style="color:var(--b)">0/5</span>
  </div>
  <div id="pos-container"></div>
</div>

<!-- whale scores -->
<div class="sec">
  <div class="sec-title"><span>Whale Trust Scores</span></div>
  <div style="overflow-x:auto">
  <table class="wtable">
    <thead><tr>
      <th>Whale</th><th>Trust</th><th>WR 15d</th><th>Trades</th><th></th>
    </tr></thead>
    <tbody id="whale-tbody"></tbody>
  </table>
  </div>
</div>

<!-- audit log -->
<div class="sec">
  <div class="sec-title"><span>Recent Actions</span></div>
  <div class="alog" id="audit-log">Loading...</div>
</div>

<!-- kill switch -->
<button class="kill-btn" onclick="confirmKill()">
  ⚠ EMERGENCY KILL SWITCH — HALT ALL TRADING
</button>

</div>

<script>
let data = {};

function confirmKill() {
  if (confirm('Halt all trading and close positions?')) {
    fetch('/api/kill', {method:'POST'}).then(()=>alert('Kill switch activated.'));
  }
}

function pnlClass(v) {
  if (v > 0) return 'pos-up';
  if (v < 0) return 'pos-dn';
  return 'pos-neu';
}

function fmt(v, prefix='') {
  if (v === null || v === undefined) return '–';
  const n = parseFloat(v);
  if (isNaN(n)) return v;
  const s = (n >= 0 ? '+' : '') + prefix + n.toFixed(4);
  return s;
}

function fmtPct(v) {
  if (v === null || v === undefined) return '–';
  const n = parseFloat(v) * 100;
  return (n >= 0 ? '+' : '') + n.toFixed(1) + '%';
}

function render(d) {
  data = d;
  const mode = d.dry_run ? 'DRY RUN' : 'LIVE';
  const pill = document.getElementById('mode-pill');
  pill.textContent = mode;
  pill.className   = 'mode-pill ' + (d.dry_run ? 'mode-dry' : 'mode-live');
  document.getElementById('ref-ts').textContent =
    new Date().toLocaleTimeString('en-US', {hour12:false});

  // Summary
  const todayPnl = d.today_pnl || 0;
  document.getElementById('s-pos').textContent =
    (d.positions || []).length + '/' + (d.max_pos || 5);
  document.getElementById('s-pnl').textContent =
    (todayPnl >= 0 ? '+' : '') + '$' + Math.abs(todayPnl).toFixed(2);
  document.getElementById('s-pnl').style.color =
    todayPnl >= 0 ? 'var(--g)' : 'var(--r)';
  document.getElementById('s-dl').textContent =
    ((d.daily_loss_pct || 0)*100).toFixed(1) + '%';
  document.getElementById('s-whales').textContent =
    (d.active_whales || 0) + '/' + (d.total_whales || 47);
  document.getElementById('pos-count').textContent =
    (d.positions || []).length + '/' + (d.max_pos || 5);

  // Positions
  const pc = document.getElementById('pos-container');
  if (!d.positions || d.positions.length === 0) {
    pc.innerHTML = '<div class="empty">No open positions</div>';
  } else {
    pc.innerHTML = d.positions.map(p => {
      const cls   = pnlClass(p.pnl_pct);
      const pnlTxt= fmtPct(p.pnl_pct) + ' ($' + (p.pnl_usd||0).toFixed(3) + ')';
      const held  = Math.floor((Date.now()/1000 - p.opened_ts) / 3600);
      const url   = p.profile_url || '#';
      return `<div class="pos ${cls}">
        <div class="pos-top">
          <div class="pos-whale">
            <a href="${url}" target="_blank">@${p.whale_name}</a>
            ${p.is_cluster ? ' 🔥' : ''}
          </div>
          <div class="pos-pnl">${pnlTxt}</div>
        </div>
        <div class="pos-mkt">${p.market_slug}</div>
        <div class="pos-grid">
          <div><div class="pf">Outcome</div><div class="pv">${p.outcome}</div></div>
          <div><div class="pf">Entry</div><div class="pv">${parseFloat(p.entry_price||0).toFixed(3)}</div></div>
          <div><div class="pf">Current</div><div class="pv">${parseFloat(p.cur_price||0).toFixed(3)}</div></div>
          <div><div class="pf">SL</div><div class="pv" style="color:var(--r)">${parseFloat(p.sl_price||0).toFixed(3)}</div></div>
          <div><div class="pf">Size</div><div class="pv">$${parseFloat(p.size_usd||0).toFixed(2)}</div></div>
          <div><div class="pf">Held</div><div class="pv">${held}h</div></div>
        </div>
      </div>`;
    }).join('');
  }

  // Whale scores
  const tb = document.getElementById('whale-tbody');
  tb.innerHTML = (d.whale_scores || []).slice(0, 20).map(w => {
    const barW = Math.round((w.trust_score || 0) * 60);
    const statusDot = w.is_active ?
      '<span class="active-dot on"></span>' :
      '<span class="active-dot off"></span>';
    return `<tr>
      <td><a href="${w.profile_url||'#'}" target="_blank" style="color:var(--b);text-decoration:none">@${w.name}</a></td>
      <td>
        <span style="color:var(--b);font-weight:700">${((w.trust_score||0)*100).toFixed(0)}</span>
        <div class="trust-bar" style="width:${barW}px"></div>
      </td>
      <td>${((w.wr_15d||0)*100).toFixed(0)}%</td>
      <td>${w.trade_count||0}</td>
      <td>${statusDot}</td>
    </tr>`;
  }).join('');

  // Audit log
  const al = document.getElementById('audit-log');
  al.innerHTML = (d.audit_tail || []).map(l =>
    `<div class="alog-line">${l}</div>`
  ).join('') || 'No actions yet.';
  al.scrollTop = al.scrollHeight;
}

async function fetchData() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    render(d);
  } catch(e) {
    console.error('Fetch failed', e);
  }
}

fetchData();
setInterval(fetchData, 5000);
</script>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# FLASK APP
# ─────────────────────────────────────────────────────────────────────────────
def create_app() -> Optional[object]:
    if not FLASK_OK:
        return None

    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False

    @app.route("/")
    def index():
        return render_template_string(HTML)

    @app.route("/api/status")
    def status():
        ref = _state_ref or {}
        return jsonify(ref)

    @app.route("/api/kill", methods=["POST"])
    def kill():
        from protection import kill_bot
        kill_bot()
        return jsonify({"killed": True})

    @app.route("/api/pause_whale", methods=["POST"])
    def pause_whale():
        data = request.get_json(silent=True) or {}
        addr = data.get("addr", "")
        if addr:
            fname = f"{config.PAUSE_PFX}{addr.lower()}"
            with open(fname, "w") as f:
                f.write(addr)
            return jsonify({"paused": addr})
        return jsonify({"error": "no addr"}), 400

    return app


def run_dashboard(state_ref: dict):
    """Run dashboard in background thread."""
    set_state_ref(state_ref)
    app = create_app()
    if not app:
        print("[DASHBOARD] Flask not installed — dashboard disabled.")
        return

    t = threading.Thread(
        target=lambda: app.run(
            host="0.0.0.0", port=config.DASHBOARD_PORT,
            debug=False, use_reloader=False),
        daemon=True, name="dashboard",
    )
    t.start()
    print(f"[DASHBOARD] Running on http://localhost:{config.DASHBOARD_PORT}")
