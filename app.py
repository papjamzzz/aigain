from flask import Flask, jsonify, request, redirect
from dotenv import load_dotenv
from pathlib import Path
import json, os, time

load_dotenv()
app = Flask(__name__)

DATA_DIR  = Path(__file__).parent / "data"
ORG_FILE  = DATA_DIR / "org.json"
DATA_DIR.mkdir(exist_ok=True)

# ── Default org structure ──────────────────────────────────────────────────────

DEFAULT_ORG = {
    "name": "Your Organization",
    "plan": "enterprise",
    "policy": {
        "mode":      "BUILD",
        "intensity": 0.6,
        "depth":     0.5,
        "certainty": 0.6,
        "room":      0.4,
        "intensity_min": 0.2, "intensity_max": 1.0,
        "depth_min":     0.2, "depth_max":     1.0,
        "certainty_min": 0.2, "certainty_max": 1.0,
        "room_min":      0.1, "room_max":      1.0,
    },
    "teams": [
        {
            "id": "engineering",
            "name": "Engineering",
            "color": "#00DDD4",
            "members": 14,
            "policy": { "mode": "BUILD", "intensity": 0.75, "depth": 0.65, "certainty": 0.7, "room": 0.3 }
        },
        {
            "id": "support",
            "name": "Customer Support",
            "color": "#A78BFA",
            "members": 22,
            "policy": { "mode": "EXPLORE", "intensity": 0.5, "depth": 0.4, "certainty": 0.5, "room": 0.7 }
        },
        {
            "id": "research",
            "name": "Research",
            "color": "#F59E0B",
            "members": 8,
            "policy": { "mode": "EXPLORE", "intensity": 0.8, "depth": 0.85, "certainty": 0.3, "room": 0.8 }
        },
        {
            "id": "sales",
            "name": "Sales",
            "color": "#34D399",
            "members": 31,
            "policy": { "mode": "BUILD", "intensity": 0.55, "depth": 0.35, "certainty": 0.75, "room": 0.5 }
        },
    ],
    "members": [
        { "id": "m1", "name": "Alex Chen",      "team": "engineering",  "role": "Senior Engineer",    "intensity": None, "depth": None },
        { "id": "m2", "name": "Jordan Park",    "team": "engineering",  "role": "Lead Engineer",      "intensity": 0.9,  "depth": 0.8  },
        { "id": "m3", "name": "Sam Rivera",     "team": "support",      "role": "Support Lead",       "intensity": None, "depth": None },
        { "id": "m4", "name": "Taylor Brooks",  "team": "research",     "role": "Research Analyst",   "intensity": None, "depth": None },
        { "id": "m5", "name": "Morgan Lee",     "team": "sales",        "role": "Account Executive",  "intensity": 0.4,  "depth": None },
    ],
    "usage": {
        "tokens_today":   4820000,
        "tokens_week":    31400000,
        "tokens_month":   118000000,
        "cost_month":     354.00,
        "estimated_save": 89.50,
    }
}

def load_org():
    if ORG_FILE.exists():
        return json.loads(ORG_FILE.read_text())
    ORG_FILE.write_text(json.dumps(DEFAULT_ORG, indent=2))
    return DEFAULT_ORG

def save_org(org):
    ORG_FILE.write_text(json.dumps(org, indent=2))


# ── HTML ───────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>AiGain — Enterprise AI Control</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Abril+Fatface&family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet" media="print" onload="this.media='all'">
<style>
:root{
  --bg:#060A0F;--panel:#0B1018;--panel2:#101820;--border:#162030;--border2:#1E2E40;
  --accent:#00DDD4;--accent2:#10F2E8;--purple:#8B5CF6;--purple2:#A78BFA;
  --amber:#F59E0B;--green:#34D399;--red:#F87171;
  --text:#D8EAF8;--text2:#6A8AA8;--text3:#405870;
  --magenta:#D946EF;--magenta2:#F0ABFF;
}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);background-image:radial-gradient(rgba(0,196,232,.03) 1px,transparent 1px);background-size:28px 28px;font-family:'Inter',sans-serif;color:var(--text);min-height:100vh;}
::-webkit-scrollbar{width:4px;}::-webkit-scrollbar-track{background:var(--panel);}::-webkit-scrollbar-thumb{background:var(--border2);border-radius:2px;}

/* ── HEADER ── */
.hdr{height:64px;display:flex;align-items:center;padding:0 28px;border-bottom:1px solid var(--border);background:#030507;position:sticky;top:0;z-index:200;gap:12px;}
.hdr::before{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(0,221,212,.5),transparent);}
.brand{font-family:'Abril Fatface',serif;font-size:32px;letter-spacing:.04em;background:linear-gradient(130deg,#00E8FF,#A0C8FF,#D946EF);-webkit-background-clip:text;-webkit-text-fill-color:transparent;filter:drop-shadow(0 0 8px rgba(0,200,255,.4)) drop-shadow(0 0 20px rgba(217,70,239,.25));}
.hdr-tag{font-size:8px;font-weight:900;letter-spacing:.2em;text-transform:uppercase;color:var(--text3);border:1px solid var(--border2);padding:3px 8px;border-radius:2px;}
.hdr-right{margin-left:auto;display:flex;align-items:center;gap:10px;}
.hdr-org{font-size:11px;font-weight:700;color:var(--text2);letter-spacing:.04em;}
.hdr-plan{font-size:8px;font-weight:900;letter-spacing:.16em;text-transform:uppercase;color:var(--accent);border:1px solid rgba(0,221,212,.3);padding:3px 8px;border-radius:2px;background:rgba(0,221,212,.05);}
.hdr-usage{font-size:9px;font-weight:700;color:var(--text3);letter-spacing:.06em;}

/* ── NAV ── */
.nav{display:flex;gap:2px;border-bottom:1px solid var(--border);background:var(--panel);padding:0 28px;}
.nav-btn{height:40px;padding:0 16px;border:none;background:none;color:var(--text3);font-size:10px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;cursor:pointer;border-bottom:2px solid transparent;transition:all .15s;font-family:'Inter',sans-serif;}
.nav-btn:hover{color:var(--text2);}
.nav-btn.active{color:var(--accent);border-bottom-color:var(--accent);}

/* ── LAYOUT ── */
.main{padding:28px;max-width:1400px;margin:0 auto;}
.page{display:none;}.page.active{display:block;}

/* ── STATS BAR ── */
.stats-bar{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:28px;}
.stat-card{background:var(--panel);border:1px solid var(--border);border-radius:6px;padding:16px 20px;}
.stat-lbl{font-size:8px;font-weight:900;letter-spacing:.2em;text-transform:uppercase;color:var(--text3);margin-bottom:6px;}
.stat-val{font-size:22px;font-weight:800;color:var(--text);letter-spacing:-.02em;font-variant-numeric:tabular-nums;}
.stat-val.green{color:var(--green);}
.stat-val.accent{color:var(--accent);}
.stat-sub{font-size:10px;color:var(--text3);margin-top:3px;}

/* ── SECTION HEADER ── */
.sec-hdr{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;}
.sec-title{font-size:10px;font-weight:900;letter-spacing:.2em;text-transform:uppercase;color:var(--text2);}
.sec-action{height:30px;padding:0 14px;border-radius:3px;border:1px solid rgba(0,221,212,.35);background:rgba(0,221,212,.06);color:var(--accent);font-size:9px;font-weight:900;letter-spacing:.14em;text-transform:uppercase;cursor:pointer;font-family:'Inter',sans-serif;transition:all .15s;}
.sec-action:hover{background:rgba(0,221,212,.14);border-color:var(--accent);}

/* ── ORG POLICY CARD ── */
.org-card{background:var(--panel);border:1px solid var(--border);border-radius:6px;margin-bottom:28px;overflow:hidden;}
.org-card-hdr{padding:14px 20px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px;background:#040810;}
.org-card-title{font-size:11px;font-weight:900;letter-spacing:.16em;text-transform:uppercase;color:var(--accent);}
.org-card-sub{font-size:10px;color:var(--text3);margin-left:auto;}
.org-card-body{padding:20px;display:grid;grid-template-columns:repeat(4,1fr);gap:20px;}
.ctrl-group{display:flex;flex-direction:column;gap:8px;}
.ctrl-lbl{font-size:8px;font-weight:900;letter-spacing:.16em;text-transform:uppercase;color:var(--text3);}
.ctrl-mode{display:flex;gap:4px;}
.mode-pill{flex:1;height:28px;border-radius:3px;border:1px solid var(--border2);background:var(--panel2);color:var(--text3);font-size:8px;font-weight:900;letter-spacing:.1em;text-transform:uppercase;cursor:pointer;transition:all .12s;font-family:'Inter',sans-serif;}
.mode-pill:hover{border-color:var(--accent);color:var(--accent);}
.mode-pill.active{background:rgba(0,221,212,.12);border-color:var(--accent);color:var(--accent);}
.ctrl-slider{width:100%;accent-color:var(--accent);cursor:pointer;height:3px;}
.ctrl-val{font-size:11px;font-weight:800;color:var(--accent);font-variant-numeric:tabular-nums;text-align:right;}

/* ── TEAM GRID ── */
.team-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px;margin-bottom:28px;}
.team-card{background:var(--panel);border:1px solid var(--border);border-radius:6px;overflow:hidden;transition:border-color .15s;}
.team-card:hover{border-color:var(--border2);}
.team-hdr{padding:14px 18px;display:flex;align-items:center;gap:10px;border-bottom:1px solid var(--border);}
.team-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0;}
.team-name{font-size:12px;font-weight:800;color:var(--text);letter-spacing:.02em;}
.team-count{margin-left:auto;font-size:9px;font-weight:700;color:var(--text3);}
.team-body{padding:16px 18px;display:flex;flex-direction:column;gap:12px;}
.team-mode-row{display:flex;gap:5px;}
.team-mode-btn{flex:1;height:26px;border-radius:2px;border:1px solid var(--border2);background:var(--panel2);color:var(--text3);font-size:8px;font-weight:900;letter-spacing:.1em;text-transform:uppercase;cursor:pointer;transition:all .1s;font-family:'Inter',sans-serif;}
.team-mode-btn.active{border-color:var(--accent);color:var(--accent);background:rgba(0,221,212,.1);}
.team-fader-row{display:flex;flex-direction:column;gap:6px;}
.team-fader-lbl{display:flex;justify-content:space-between;align-items:center;}
.team-fader-name{font-size:8px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;color:var(--text3);}
.team-fader-val{font-size:9px;font-weight:800;color:var(--accent);font-variant-numeric:tabular-nums;}
.team-fader{width:100%;accent-color:var(--accent);height:2px;cursor:pointer;}
.team-footer{padding:10px 18px;border-top:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;}
.team-footer-stat{font-size:9px;color:var(--text3);}
.team-edit-btn{height:24px;padding:0 10px;border-radius:2px;border:1px solid var(--border2);background:transparent;color:var(--text3);font-size:8px;font-weight:900;letter-spacing:.1em;text-transform:uppercase;cursor:pointer;font-family:'Inter',sans-serif;transition:all .1s;}
.team-edit-btn:hover{border-color:var(--accent);color:var(--accent);}

/* ── MEMBERS TABLE ── */
.members-table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--border);border-radius:6px;overflow:hidden;}
.members-table th{padding:10px 16px;text-align:left;font-size:8px;font-weight:900;letter-spacing:.18em;text-transform:uppercase;color:var(--text3);border-bottom:1px solid var(--border);background:#040810;}
.members-table td{padding:12px 16px;border-bottom:1px solid var(--border);font-size:11px;color:var(--text2);}
.members-table tr:last-child td{border-bottom:none;}
.members-table tr:hover td{background:rgba(255,255,255,.015);}
.member-name{font-weight:700;color:var(--text);}
.member-role{font-size:10px;color:var(--text3);}
.member-team-tag{display:inline-block;padding:2px 7px;border-radius:2px;font-size:8px;font-weight:900;letter-spacing:.1em;text-transform:uppercase;}
.override-badge{font-size:8px;font-weight:900;letter-spacing:.1em;text-transform:uppercase;color:var(--amber);border:1px solid rgba(245,158,11,.3);padding:2px 6px;border-radius:2px;}
.inherit-badge{font-size:8px;color:var(--text3);}
.member-val{font-size:11px;font-weight:700;font-variant-numeric:tabular-nums;}

/* ── POLICY PAGE ── */
.policy-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px;}
.policy-card{background:var(--panel);border:1px solid var(--border);border-radius:6px;overflow:hidden;}
.policy-card-hdr{padding:14px 20px;border-bottom:1px solid var(--border);background:#040810;}
.policy-card-title{font-size:10px;font-weight:900;letter-spacing:.18em;text-transform:uppercase;color:var(--text2);}
.policy-card-body{padding:20px;display:flex;flex-direction:column;gap:16px;}
.policy-row{display:flex;align-items:center;justify-content:space-between;gap:16px;}
.policy-row-lbl{font-size:10px;font-weight:700;color:var(--text2);flex-shrink:0;width:120px;}
.policy-row-val{font-size:11px;font-weight:800;color:var(--accent);width:36px;text-align:right;font-variant-numeric:tabular-nums;}
.policy-range{flex:1;accent-color:var(--accent);cursor:pointer;}
.policy-toggle{width:36px;height:20px;border-radius:10px;border:1px solid var(--border2);background:var(--panel2);cursor:pointer;position:relative;transition:all .2s;}
.policy-toggle.on{background:rgba(0,221,212,.2);border-color:var(--accent);}
.policy-toggle::after{content:'';position:absolute;width:14px;height:14px;border-radius:50%;background:var(--text3);top:2px;left:2px;transition:all .2s;}
.policy-toggle.on::after{left:18px;background:var(--accent);}

/* ── MODAL ── */
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:300;}
.modal-overlay.open{display:flex;align-items:center;justify-content:center;}
.modal{background:var(--panel);border:1px solid var(--border2);border-radius:8px;width:480px;max-width:90vw;overflow:hidden;}
.modal-hdr{padding:16px 20px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;background:#040810;}
.modal-title{font-size:11px;font-weight:900;letter-spacing:.18em;text-transform:uppercase;color:var(--accent);}
.modal-close{width:24px;height:24px;border:1px solid var(--border2);background:transparent;color:var(--text3);border-radius:50%;cursor:pointer;font-size:12px;display:flex;align-items:center;justify-content:center;font-weight:700;}
.modal-close:hover{color:var(--accent);border-color:var(--accent);}
.modal-body{padding:20px;display:flex;flex-direction:column;gap:14px;}
.form-field{display:flex;flex-direction:column;gap:6px;}
.form-lbl{font-size:8px;font-weight:900;letter-spacing:.16em;text-transform:uppercase;color:var(--text3);}
.form-input{height:36px;background:var(--panel2);border:1px solid var(--border2);border-radius:3px;color:var(--text);font-size:12px;font-family:'Inter',sans-serif;padding:0 12px;outline:none;transition:border-color .15s;}
.form-input:focus{border-color:var(--accent);}
.form-select{height:36px;background:var(--panel2);border:1px solid var(--border2);border-radius:3px;color:var(--text);font-size:12px;font-family:'Inter',sans-serif;padding:0 12px;outline:none;cursor:pointer;}
.modal-footer{padding:14px 20px;border-top:1px solid var(--border);display:flex;gap:8px;justify-content:flex-end;}
.btn{height:34px;padding:0 18px;border-radius:3px;border:1px solid var(--border2);background:transparent;color:var(--text2);font-size:9px;font-weight:900;letter-spacing:.14em;text-transform:uppercase;cursor:pointer;font-family:'Inter',sans-serif;transition:all .12s;}
.btn:hover{border-color:var(--text2);color:var(--text);}
.btn.primary{border-color:rgba(0,221,212,.5);background:rgba(0,221,212,.08);color:var(--accent);}
.btn.primary:hover{background:rgba(0,221,212,.18);border-color:var(--accent);}

/* ── EMPTY STATE ── */
.empty{text-align:center;padding:48px 20px;color:var(--text3);font-size:11px;}
</style>
</head>
<body>

<header class="hdr">
  <div class="brand">AiGain</div>
  <div class="hdr-tag">Enterprise</div>
  <div class="hdr-right">
    <div class="hdr-org" id="hdr-org-name">—</div>
    <div class="hdr-plan">Enterprise Plan</div>
    <div class="hdr-usage" id="hdr-usage">—</div>
  </div>
</header>

<nav class="nav">
  <button class="nav-btn active" onclick="showPage('dashboard')">Dashboard</button>
  <button class="nav-btn" onclick="showPage('teams')">Teams</button>
  <button class="nav-btn" onclick="showPage('members')">Members</button>
  <button class="nav-btn" onclick="showPage('policy')">Policy</button>
</nav>

<div class="main">

  <!-- ── DASHBOARD ── -->
  <div id="page-dashboard" class="page active">
    <div class="stats-bar" id="stats-bar"></div>
    <div class="sec-hdr"><div class="sec-title">Org-Wide Behavioral Policy</div><div class="org-card-sub" id="org-policy-sub">Applied to all teams unless overridden</div></div>
    <div class="org-card">
      <div class="org-card-hdr">
        <div class="org-card-title">Default Behavioral State</div>
        <div class="org-card-sub">All 75 employees start here</div>
      </div>
      <div class="org-card-body" id="org-controls"></div>
    </div>
    <div class="sec-hdr"><div class="sec-title">Teams</div></div>
    <div class="team-grid" id="team-grid-dashboard"></div>
  </div>

  <!-- ── TEAMS ── -->
  <div id="page-teams" class="page">
    <div class="sec-hdr">
      <div class="sec-title">Team Behavioral Policies</div>
      <button class="sec-action" onclick="openAddTeam()">+ Add Team</button>
    </div>
    <div class="team-grid" id="team-grid-teams"></div>
  </div>

  <!-- ── MEMBERS ── -->
  <div id="page-members" class="page">
    <div class="sec-hdr">
      <div class="sec-title">Members</div>
      <button class="sec-action" onclick="openAddMember()">+ Add Member</button>
    </div>
    <table class="members-table" id="members-table">
      <thead>
        <tr>
          <th>Member</th>
          <th>Team</th>
          <th>Mode</th>
          <th>Intensity</th>
          <th>Depth</th>
          <th>Override</th>
        </tr>
      </thead>
      <tbody id="members-tbody"></tbody>
    </table>
  </div>

  <!-- ── POLICY ── -->
  <div id="page-policy" class="page">
    <div class="sec-hdr"><div class="sec-title">Org-Wide Policy Rules</div></div>
    <div class="policy-grid" id="policy-grid"></div>
  </div>

</div>

<!-- ── ADD TEAM MODAL ── -->
<div class="modal-overlay" id="add-team-modal">
  <div class="modal">
    <div class="modal-hdr">
      <div class="modal-title">Add Team</div>
      <button class="modal-close" onclick="closeModal('add-team-modal')">✕</button>
    </div>
    <div class="modal-body">
      <div class="form-field"><div class="form-lbl">Team Name</div><input class="form-input" id="new-team-name" placeholder="e.g. Product Design" maxlength="40"></div>
      <div class="form-field"><div class="form-lbl">Default Mode</div>
        <select class="form-select" id="new-team-mode">
          <option value="BUILD">BUILD — execute, implement, ship</option>
          <option value="EXPLORE">EXPLORE — analyse, research, question</option>
        </select>
      </div>
      <div class="form-field"><div class="form-lbl">Intensity Default</div><input type="range" class="form-input" id="new-team-intensity" min="0" max="1" step="0.05" value="0.6" style="padding:8px 0;border:none;background:none;accent-color:var(--accent)"></div>
    </div>
    <div class="modal-footer">
      <button class="btn" onclick="closeModal('add-team-modal')">Cancel</button>
      <button class="btn primary" onclick="addTeam()">Create Team</button>
    </div>
  </div>
</div>

<!-- ── ADD MEMBER MODAL ── -->
<div class="modal-overlay" id="add-member-modal">
  <div class="modal">
    <div class="modal-hdr">
      <div class="modal-title">Add Member</div>
      <button class="modal-close" onclick="closeModal('add-member-modal')">✕</button>
    </div>
    <div class="modal-body">
      <div class="form-field"><div class="form-lbl">Full Name</div><input class="form-input" id="new-member-name" placeholder="Jane Smith" maxlength="60"></div>
      <div class="form-field"><div class="form-lbl">Role / Title</div><input class="form-input" id="new-member-role" placeholder="Senior Engineer" maxlength="60"></div>
      <div class="form-field"><div class="form-lbl">Team</div><select class="form-select" id="new-member-team"></select></div>
    </div>
    <div class="modal-footer">
      <button class="btn" onclick="closeModal('add-member-modal')">Cancel</button>
      <button class="btn primary" onclick="addMember()">Add Member</button>
    </div>
  </div>
</div>

<script>
let ORG = null;

async function loadOrg(){
  const r = await fetch('/api/org');
  ORG = await r.json();
  render();
}

function fmt(v){ return v != null ? v.toFixed(2) : '—'; }
function fmtTokens(n){ return n >= 1e6 ? (n/1e6).toFixed(1)+'M' : (n/1e3).toFixed(0)+'K'; }

function render(){
  if(!ORG) return;
  document.getElementById('hdr-org-name').textContent = ORG.name;
  document.getElementById('hdr-usage').textContent = fmtTokens(ORG.usage.tokens_today) + ' tokens today';
  renderStats();
  renderOrgControls();
  renderTeams();
  renderMembers();
  renderPolicy();
}

function renderStats(){
  const u = ORG.usage;
  const total = ORG.teams.reduce((a,t)=>a+t.members,0);
  document.getElementById('stats-bar').innerHTML = `
    <div class="stat-card"><div class="stat-lbl">Total Members</div><div class="stat-val accent">${total}</div><div class="stat-sub">${ORG.teams.length} teams</div></div>
    <div class="stat-card"><div class="stat-lbl">Tokens This Month</div><div class="stat-val">${fmtTokens(u.tokens_month)}</div><div class="stat-sub">$${u.cost_month.toFixed(2)} est. cost</div></div>
    <div class="stat-card"><div class="stat-lbl">Estimated Savings</div><div class="stat-val green">$${u.estimated_save.toFixed(2)}</div><div class="stat-sub">via behavioral throttling</div></div>
    <div class="stat-card"><div class="stat-lbl">Active Overrides</div><div class="stat-val">${ORG.members.filter(m=>m.intensity!=null||m.depth!=null).length}</div><div class="stat-sub">individual customizations</div></div>
  `;
}

function renderOrgControls(){
  const p = ORG.policy;
  document.getElementById('org-controls').innerHTML = `
    <div class="ctrl-group">
      <div class="ctrl-lbl">Default Mode</div>
      <div class="ctrl-mode">
        <button class="mode-pill ${p.mode==='EXPLORE'?'active':''}" onclick="setOrgMode('EXPLORE')">Explore</button>
        <button class="mode-pill ${p.mode==='BUILD'?'active':''}" onclick="setOrgMode('BUILD')">Build</button>
      </div>
    </div>
    <div class="ctrl-group">
      <div class="ctrl-lbl">Intensity <span class="ctrl-val" id="org-intensity-val">${fmt(p.intensity)}</span></div>
      <input type="range" class="ctrl-slider" min="0" max="1" step="0.05" value="${p.intensity}" oninput="updateOrgSlider('intensity',this.value)">
    </div>
    <div class="ctrl-group">
      <div class="ctrl-lbl">Depth <span class="ctrl-val" id="org-depth-val">${fmt(p.depth)}</span></div>
      <input type="range" class="ctrl-slider" min="0" max="1" step="0.05" value="${p.depth}" oninput="updateOrgSlider('depth',this.value)">
    </div>
    <div class="ctrl-group">
      <div class="ctrl-lbl">Verbosity <span class="ctrl-val" id="org-room-val">${fmt(p.room)}</span></div>
      <input type="range" class="ctrl-slider" min="0" max="1" step="0.05" value="${p.room}" oninput="updateOrgSlider('room',this.value)">
    </div>
  `;
}

function teamCardHTML(t, showEdit){
  const modes = ['EXPLORE','BUILD'];
  return `
    <div class="team-card">
      <div class="team-hdr">
        <div class="team-dot" style="background:${t.color}"></div>
        <div class="team-name">${t.name}</div>
        <div class="team-count">${t.members} members</div>
      </div>
      <div class="team-body">
        <div class="team-mode-row">
          ${modes.map(m=>`<button class="team-mode-btn ${t.policy.mode===m?'active':''}" onclick="setTeamMode('${t.id}','${m}')">${m}</button>`).join('')}
        </div>
        <div class="team-fader-row">
          <div class="team-fader-lbl"><span class="team-fader-name">Intensity</span><span class="team-fader-val" id="tv-${t.id}-intensity">${fmt(t.policy.intensity)}</span></div>
          <input type="range" class="team-fader" min="0" max="1" step="0.05" value="${t.policy.intensity}" oninput="updateTeamSlider('${t.id}','intensity',this.value)">
        </div>
        <div class="team-fader-row">
          <div class="team-fader-lbl"><span class="team-fader-name">Depth</span><span class="team-fader-val" id="tv-${t.id}-depth">${fmt(t.policy.depth)}</span></div>
          <input type="range" class="team-fader" min="0" max="1" step="0.05" value="${t.policy.depth}" oninput="updateTeamSlider('${t.id}','depth',this.value)">
        </div>
        <div class="team-fader-row">
          <div class="team-fader-lbl"><span class="team-fader-name">Verbosity</span><span class="team-fader-val" id="tv-${t.id}-room">${fmt(t.policy.room)}</span></div>
          <input type="range" class="team-fader" min="0" max="1" step="0.05" value="${t.policy.room}" oninput="updateTeamSlider('${t.id}','room',this.value)">
        </div>
      </div>
      <div class="team-footer">
        <div class="team-footer-stat">${t.policy.mode} · ${fmt(t.policy.intensity)} intensity</div>
        ${showEdit?`<button class="team-edit-btn" onclick="editTeam('${t.id}')">Edit</button>`:''}
      </div>
    </div>
  `;
}

function renderTeams(){
  const grid = ORG.teams.map(t=>teamCardHTML(t,false)).join('');
  const el1 = document.getElementById('team-grid-dashboard');
  const el2 = document.getElementById('team-grid-teams');
  if(el1) el1.innerHTML = grid;
  if(el2) el2.innerHTML = ORG.teams.map(t=>teamCardHTML(t,true)).join('');
}

function renderMembers(){
  const teamMap = {};
  ORG.teams.forEach(t=>teamMap[t.id]=t);
  document.getElementById('members-tbody').innerHTML = ORG.members.map(m=>{
    const team = teamMap[m.team] || {};
    const hasOverride = m.intensity!=null || m.depth!=null;
    return `
      <tr>
        <td><div class="member-name">${m.name}</div><div class="member-role">${m.role}</div></td>
        <td><span class="member-team-tag" style="background:${team.color}22;color:${team.color};border:1px solid ${team.color}44">${team.name||m.team}</span></td>
        <td>${team.policy?.mode||'—'}</td>
        <td><span class="member-val" style="color:${m.intensity!=null?'var(--amber)':'var(--text3)'}">${m.intensity!=null?fmt(m.intensity):'team'}</span></td>
        <td><span class="member-val" style="color:${m.depth!=null?'var(--amber)':'var(--text3)'}">${m.depth!=null?fmt(m.depth):'team'}</span></td>
        <td>${hasOverride?'<span class="override-badge">Override</span>':'<span class="inherit-badge">Inherits team</span>'}</td>
      </tr>
    `;
  }).join('');
  // populate team select in add-member modal
  const sel = document.getElementById('new-member-team');
  if(sel) sel.innerHTML = ORG.teams.map(t=>`<option value="${t.id}">${t.name}</option>`).join('');
}

function renderPolicy(){
  const p = ORG.policy;
  document.getElementById('policy-grid').innerHTML = `
    <div class="policy-card">
      <div class="policy-card-hdr"><div class="policy-card-title">Intensity Limits</div></div>
      <div class="policy-card-body">
        <div class="policy-row"><div class="policy-row-lbl">Minimum</div><input type="range" class="policy-range" min="0" max="1" step="0.05" value="${p.intensity_min}" oninput="updatePolicyLimit('intensity_min',this.value)"><div class="policy-row-val" id="pl-intensity_min">${fmt(p.intensity_min)}</div></div>
        <div class="policy-row"><div class="policy-row-lbl">Maximum</div><input type="range" class="policy-range" min="0" max="1" step="0.05" value="${p.intensity_max}" oninput="updatePolicyLimit('intensity_max',this.value)"><div class="policy-row-val" id="pl-intensity_max">${fmt(p.intensity_max)}</div></div>
      </div>
    </div>
    <div class="policy-card">
      <div class="policy-card-hdr"><div class="policy-card-title">Depth Limits</div></div>
      <div class="policy-card-body">
        <div class="policy-row"><div class="policy-row-lbl">Minimum</div><input type="range" class="policy-range" min="0" max="1" step="0.05" value="${p.depth_min}" oninput="updatePolicyLimit('depth_min',this.value)"><div class="policy-row-val" id="pl-depth_min">${fmt(p.depth_min)}</div></div>
        <div class="policy-row"><div class="policy-row-lbl">Maximum</div><input type="range" class="policy-range" min="0" max="1" step="0.05" value="${p.depth_max}" oninput="updatePolicyLimit('depth_max',this.value)"><div class="policy-row-val" id="pl-depth_max">${fmt(p.depth_max)}</div></div>
      </div>
    </div>
    <div class="policy-card">
      <div class="policy-card-hdr"><div class="policy-card-title">Verbosity Limits</div></div>
      <div class="policy-card-body">
        <div class="policy-row"><div class="policy-row-lbl">Minimum</div><input type="range" class="policy-range" min="0" max="1" step="0.05" value="${p.room_min}" oninput="updatePolicyLimit('room_min',this.value)"><div class="policy-row-val" id="pl-room_min">${fmt(p.room_min)}</div></div>
        <div class="policy-row"><div class="policy-row-lbl">Maximum</div><input type="range" class="policy-range" min="0" max="1" step="0.05" value="${p.room_max}" oninput="updatePolicyLimit('room_max',this.value)"><div class="policy-row-val" id="pl-room_max">${fmt(p.room_max)}</div></div>
      </div>
    </div>
    <div class="policy-card">
      <div class="policy-card-hdr"><div class="policy-card-title">Governance Rules</div></div>
      <div class="policy-card-body">
        <div class="policy-row"><div class="policy-row-lbl">Allow individual overrides</div><div class="policy-toggle on" onclick="this.classList.toggle('on')"></div></div>
        <div class="policy-row"><div class="policy-row-lbl">Allow team mode changes</div><div class="policy-toggle on" onclick="this.classList.toggle('on')"></div></div>
        <div class="policy-row"><div class="policy-row-lbl">Log all behavioral states</div><div class="policy-toggle on" onclick="this.classList.toggle('on')"></div></div>
        <div class="policy-row"><div class="policy-row-lbl">Enforce org defaults nightly</div><div class="policy-toggle" onclick="this.classList.toggle('on')"></div></div>
      </div>
    </div>
  `;
}

// ── State mutations ────────────────────────────────────────────────────────────

async function patch(data){
  await fetch('/api/org', {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
}

function setOrgMode(mode){
  ORG.policy.mode = mode;
  patch({policy: ORG.policy});
  renderOrgControls();
}

function updateOrgSlider(key, val){
  ORG.policy[key] = parseFloat(val);
  const el = document.getElementById('org-'+key+'-val');
  if(el) el.textContent = parseFloat(val).toFixed(2);
  patch({policy: ORG.policy});
}

function setTeamMode(teamId, mode){
  const team = ORG.teams.find(t=>t.id===teamId);
  if(!team) return;
  team.policy.mode = mode;
  patch({teams: ORG.teams});
  renderTeams();
}

function updateTeamSlider(teamId, key, val){
  const team = ORG.teams.find(t=>t.id===teamId);
  if(!team) return;
  team.policy[key] = parseFloat(val);
  const el = document.getElementById('tv-'+teamId+'-'+key);
  if(el) el.textContent = parseFloat(val).toFixed(2);
  patch({teams: ORG.teams});
}

function updatePolicyLimit(key, val){
  ORG.policy[key] = parseFloat(val);
  const el = document.getElementById('pl-'+key);
  if(el) el.textContent = parseFloat(val).toFixed(2);
  patch({policy: ORG.policy});
}

// ── Navigation ─────────────────────────────────────────────────────────────────

function showPage(name){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b=>b.classList.remove('active'));
  document.getElementById('page-'+name).classList.add('active');
  document.querySelectorAll('.nav-btn').forEach(b=>{
    if(b.textContent.toLowerCase().includes(name)) b.classList.add('active');
  });
}

// ── Modals ─────────────────────────────────────────────────────────────────────

function openAddTeam(){ document.getElementById('add-team-modal').classList.add('open'); }
function openAddMember(){ document.getElementById('add-member-modal').classList.add('open'); }
function closeModal(id){ document.getElementById(id).classList.remove('open'); }

const COLORS = ['#00DDD4','#A78BFA','#F59E0B','#34D399','#F87171','#60A5FA','#FB7185','#A3E635'];

async function addTeam(){
  const name = document.getElementById('new-team-name').value.trim();
  if(!name) return;
  const mode = document.getElementById('new-team-mode').value;
  const intensity = parseFloat(document.getElementById('new-team-intensity').value);
  const color = COLORS[ORG.teams.length % COLORS.length];
  const id = name.toLowerCase().replace(/[^a-z0-9]+/g,'-');
  ORG.teams.push({id, name, color, members:0, policy:{mode, intensity, depth:0.5, certainty:0.5, room:0.5}});
  await patch({teams: ORG.teams});
  closeModal('add-team-modal');
  document.getElementById('new-team-name').value = '';
  renderTeams();
}

async function addMember(){
  const name = document.getElementById('new-member-name').value.trim();
  const role = document.getElementById('new-member-role').value.trim();
  const team = document.getElementById('new-member-team').value;
  if(!name || !team) return;
  const id = 'm' + Date.now();
  ORG.members.push({id, name, role, team, intensity:null, depth:null});
  const t = ORG.teams.find(t=>t.id===team);
  if(t) t.members++;
  await patch({teams: ORG.teams, members: ORG.members});
  closeModal('add-member-modal');
  document.getElementById('new-member-name').value = '';
  document.getElementById('new-member-role').value = '';
  renderTeams();
  renderMembers();
  renderStats();
}

loadOrg();
</script>
</body>
</html>"""


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return HTML

@app.route('/api/org', methods=['GET'])
def get_org():
    return jsonify(load_org())

@app.route('/api/org', methods=['PATCH'])
def patch_org():
    org  = load_org()
    data = request.get_json() or {}
    org.update(data)
    save_org(org)
    return jsonify({'ok': True})

@app.route('/health')
def health():
    return jsonify({'ok': True, 'project': 'aigain'})


if __name__ == '__main__':
    print('\n┌─────────────────────────────────────┐')
    print('│  AiGain  ·  Enterprise AI Control    │')
    print('│  http://127.0.0.1:5571               │')
    print('└─────────────────────────────────────┘\n')
    app.run(host='127.0.0.1', port=5571, debug=False)
