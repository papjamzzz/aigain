from flask import Flask, jsonify, request, redirect, Response, stream_with_context
from dotenv import load_dotenv
from pathlib import Path
import json, os, time, uuid, hashlib, hmac, secrets
import urllib.request, urllib.error

load_dotenv()
app = Flask(__name__)

DATA_DIR   = Path(__file__).parent / "data"
ORG_FILE   = DATA_DIR / "org.json"
KEYS_FILE  = DATA_DIR / "api_keys.json"
USAGE_FILE = DATA_DIR / "usage_log.json"
DATA_DIR.mkdir(exist_ok=True)

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── Gain behavioral prompt builder ────────────────────────────────────────────

def build_behavioral_prompt(policy: dict) -> str:
    mode      = policy.get("mode", "BUILD")
    intensity = policy.get("intensity", 0.6)
    depth     = policy.get("depth", 0.5)
    room      = policy.get("room", 0.4)

    if mode == "EXPLORE":
        mode_rules = (
            "MODE: EXPLORE\n"
            "- Think out loud. Show full reasoning.\n"
            "- Cover multiple approaches, angles, and trade-offs.\n"
            "- Ask clarifying questions if the problem is ambiguous.\n"
            "- Do NOT write code or make changes unless explicitly asked.\n"
            "- End with open questions or decision points."
        )
    else:
        mode_rules = (
            "MODE: BUILD\n"
            "- Execute immediately. No exploration, no alternatives.\n"
            "- Pick the single best approach and implement it.\n"
            "- Output only what was built. No preamble."
        )

    intensity_rule = (
        "INTENSITY: HIGH — minimal output, direct execution only." if intensity >= 0.7
        else "INTENSITY: MED — concise reasoning, focused output." if intensity >= 0.4
        else "INTENSITY: LOW — verbose reasoning, exploratory tone."
    )

    depth_rule = (
        "DEPTH: HIGH — deeper diagnostic reasoning allowed." if depth >= 0.7
        else "DEPTH: MED — moderate analysis depth." if depth >= 0.4
        else "DEPTH: LOW — surface-level reasoning only."
    )

    room_rule = (
        "VOICE: OPEN — full resonance, thinks out loud." if room >= 0.7
        else "VOICE: STUDIO — professional, measured, clean." if room >= 0.4
        else "VOICE: DIRECT — dead room, output only, zero commentary."
    )

    return "\n".join([mode_rules, intensity_rule, depth_rule, room_rule])


# ── API key management ─────────────────────────────────────────────────────────

def load_keys() -> dict:
    if KEYS_FILE.exists():
        return json.loads(KEYS_FILE.read_text())
    return {}

def save_keys(keys: dict):
    KEYS_FILE.write_text(json.dumps(keys, indent=2))

def generate_key(team_id: str = None) -> str:
    return "ag-" + secrets.token_hex(24)

def ensure_team_keys():
    """Auto-generate one key per team if not already present."""
    org  = load_org()
    keys = load_keys()
    changed = False
    for team in org.get("teams", []):
        has_key = any(v.get("team_id") == team["id"] and v.get("active") for v in keys.values())
        if not has_key:
            key_id = "kid_" + uuid.uuid4().hex[:12]
            keys[key_id] = {
                "key":       generate_key(team["id"]),
                "label":     team["name"] + " Key",
                "team_id":   team["id"],
                "member_id": None,
                "created":   int(time.time()),
                "active":    True,
            }
            changed = True
    if changed:
        save_keys(keys)

def resolve_policy(key_meta: dict, org: dict) -> dict:
    """Return the effective behavioral policy for a given API key."""
    team_id   = key_meta.get("team_id")
    member_id = key_meta.get("member_id")

    # Start with org defaults
    policy = dict(org.get("policy", {}))

    # Layer team policy on top
    if team_id:
        team = next((t for t in org.get("teams", []) if t["id"] == team_id), None)
        if team:
            policy.update(team.get("policy", {}))

    # Layer individual overrides on top
    if member_id:
        member = next((m for m in org.get("members", []) if m["id"] == member_id), None)
        if member:
            if member.get("intensity") is not None:
                policy["intensity"] = member["intensity"]
            if member.get("depth") is not None:
                policy["depth"] = member["depth"]

    return policy


# ── Usage logging ──────────────────────────────────────────────────────────────

def log_usage(key_id: str, team_id: str, member_id: str, model: str,
              input_tokens: int, output_tokens: int):
    try:
        log = json.loads(USAGE_FILE.read_text()) if USAGE_FILE.exists() else []
        log.append({
            "ts":           int(time.time()),
            "key_id":       key_id,
            "team_id":      team_id,
            "member_id":    member_id,
            "model":        model,
            "input_tokens": input_tokens,
            "output_tokens":output_tokens,
        })
        USAGE_FILE.write_text(json.dumps(log[-10000:]))  # keep last 10k entries
    except Exception:
        pass

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
.stat-card{background:var(--panel);border:1px solid var(--border);border-radius:6px;padding:18px 20px 16px;position:relative;overflow:hidden;border-left:3px solid var(--border);}
.stat-card::after{content:'';position:absolute;top:0;right:0;bottom:0;width:60px;pointer-events:none;}
.stat-card.c-accent{border-left-color:var(--accent);}
.stat-card.c-accent::after{background:linear-gradient(270deg,rgba(0,221,212,.04),transparent);}
.stat-card.c-green{border-left-color:var(--green);}
.stat-card.c-green::after{background:linear-gradient(270deg,rgba(52,211,153,.04),transparent);}
.stat-card.c-amber{border-left-color:var(--amber);}
.stat-card.c-amber::after{background:linear-gradient(270deg,rgba(245,158,11,.04),transparent);}
.stat-card.c-purple{border-left-color:var(--purple2);}
.stat-card.c-purple::after{background:linear-gradient(270deg,rgba(167,139,250,.04),transparent);}
.stat-lbl{font-size:8px;font-weight:900;letter-spacing:.2em;text-transform:uppercase;color:var(--text3);margin-bottom:8px;}
.stat-val{font-size:32px;font-weight:800;color:var(--text);letter-spacing:-.03em;font-variant-numeric:tabular-nums;line-height:1;}
.stat-val.green{color:var(--green);}
.stat-val.accent{color:var(--accent);}
.stat-val.amber{color:var(--amber);}
.stat-val.purple{color:var(--purple2);}
.stat-sub{font-size:10px;color:var(--text3);margin-top:6px;}
.stat-bar-wrap{margin-top:10px;height:2px;background:var(--border);border-radius:1px;overflow:hidden;}
.stat-bar-fill{height:100%;border-radius:1px;}

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
.team-card{background:var(--panel);border:1px solid var(--border);border-radius:6px;overflow:hidden;transition:all .2s;}
.team-card:hover{border-color:var(--border2);transform:translateY(-1px);box-shadow:0 4px 20px rgba(0,0,0,.3);}
.team-hdr{padding:14px 18px;display:flex;align-items:center;gap:10px;border-bottom:1px solid var(--border);position:relative;}
.team-hdr-glow{position:absolute;top:0;left:0;right:0;bottom:0;opacity:.04;pointer-events:none;}
.team-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0;box-shadow:0 0 8px currentColor;}
.team-name{font-size:13px;font-weight:800;color:var(--text);letter-spacing:.01em;}
.team-count{margin-left:auto;font-size:9px;font-weight:700;color:var(--text3);background:var(--panel2);padding:2px 8px;border-radius:10px;border:1px solid var(--border2);}
.team-body{padding:16px 18px;display:flex;flex-direction:column;gap:12px;}
.team-mode-row{display:flex;gap:4px;}
.team-mode-btn{flex:1;height:28px;border-radius:3px;border:1px solid var(--border2);background:var(--panel2);color:var(--text3);font-size:8px;font-weight:900;letter-spacing:.12em;text-transform:uppercase;cursor:pointer;transition:all .12s;font-family:'Inter',sans-serif;}
.team-mode-btn.active{background:rgba(0,221,212,.1);border-color:var(--accent);color:var(--accent);box-shadow:inset 0 0 8px rgba(0,221,212,.06);}
.team-fader-row{display:flex;flex-direction:column;gap:5px;}
.team-fader-lbl{display:flex;justify-content:space-between;align-items:center;}
.team-fader-name{font-size:8px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;color:var(--text3);}
.team-fader-val{font-size:10px;font-weight:800;font-variant-numeric:tabular-nums;}
.team-fader{width:100%;height:2px;cursor:pointer;border-radius:1px;}
.team-footer{padding:10px 18px;border-top:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;}
.team-footer-mode{font-size:8px;font-weight:900;letter-spacing:.12em;text-transform:uppercase;padding:2px 8px;border-radius:2px;}
.team-edit-btn{height:24px;padding:0 10px;border-radius:2px;border:1px solid var(--border2);background:transparent;color:var(--text3);font-size:8px;font-weight:900;letter-spacing:.1em;text-transform:uppercase;cursor:pointer;font-family:'Inter',sans-serif;transition:all .1s;}
.team-edit-btn:hover{border-color:var(--accent);color:var(--accent);}
/* ── Activity feed ── */
.activity-section{margin-top:28px;}
.activity-list{background:var(--panel);border:1px solid var(--border);border-radius:6px;overflow:hidden;}
.activity-item{padding:12px 20px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:14px;}
.activity-item:last-child{border-bottom:none;}
.activity-dot{width:6px;height:6px;border-radius:50%;flex-shrink:0;}
.activity-text{font-size:11px;color:var(--text2);flex:1;}
.activity-text strong{color:var(--text);font-weight:700;}
.activity-time{font-size:9px;color:var(--text3);flex-shrink:0;}

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

/* ── CHARTS ── */
.chart-row{display:flex;gap:14px;margin-bottom:14px;}
.chart-card{background:var(--panel);border:1px solid var(--border);border-radius:6px;overflow:hidden;min-width:0;}
.chart-wide{flex:3;}
.chart-narrow{flex:1.4;}
.chart-third{flex:1;}
.chart-hdr{padding:14px 18px 10px;display:flex;align-items:baseline;gap:10px;border-bottom:1px solid var(--border);}
.chart-title{font-size:11px;font-weight:800;color:var(--text);letter-spacing:.01em;}
.chart-sub{font-size:9px;color:var(--text3);margin-left:2px;}
.chart-hdr-btn{margin-left:auto;height:24px;padding:0 10px;border-radius:2px;border:1px solid var(--border2);background:transparent;color:var(--text3);font-size:8px;font-weight:900;letter-spacing:.1em;text-transform:uppercase;cursor:pointer;font-family:'Inter',sans-serif;transition:all .1s;}
.chart-hdr-btn:hover{border-color:var(--accent);color:var(--accent);}
.chart-body{padding:16px;height:200px;position:relative;}
.chart-body-donut{height:200px;display:flex;align-items:center;justify-content:center;}
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
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
  <button class="nav-btn" onclick="showPage('keys')">API Keys</button>
</nav>

<div class="main">

  <!-- ── DASHBOARD ── -->
  <div id="page-dashboard" class="page active">
    <div class="stats-bar" id="stats-bar"></div>
    <div class="chart-row">
      <div class="chart-card chart-wide">
        <div class="chart-hdr"><div class="chart-title">Token Usage — 30 Day Trend</div><div class="chart-sub">Daily tokens consumed across all teams</div></div>
        <div class="chart-body"><canvas id="chart-usage"></canvas></div>
      </div>
      <div class="chart-card chart-narrow">
        <div class="chart-hdr"><div class="chart-title">Usage by Team</div><div class="chart-sub">Share of monthly tokens</div></div>
        <div class="chart-body chart-body-donut"><canvas id="chart-donut"></canvas></div>
      </div>
    </div>
    <div class="chart-row">
      <div class="chart-card chart-third">
        <div class="chart-hdr"><div class="chart-title">Mode Distribution</div><div class="chart-sub">EXPLORE vs BUILD across teams</div></div>
        <div class="chart-body"><canvas id="chart-mode"></canvas></div>
      </div>
      <div class="chart-card chart-third">
        <div class="chart-hdr"><div class="chart-title">Intensity by Team</div><div class="chart-sub">Average throttle level per team</div></div>
        <div class="chart-body"><canvas id="chart-intensity"></canvas></div>
      </div>
      <div class="chart-card chart-third">
        <div class="chart-hdr"><div class="chart-title">Override Rate</div><div class="chart-sub">Individual vs inherited policy</div></div>
        <div class="chart-body chart-body-donut"><canvas id="chart-override"></canvas></div>
      </div>
    </div>
    <div class="chart-row">
      <div class="chart-card" style="flex:1;">
        <div class="chart-hdr"><div class="chart-title">Org-Wide Behavioral Policy</div><div class="chart-sub">Default state applied to all teams unless overridden</div><button class="chart-hdr-btn" onclick="togglePolicyEdit()">Edit</button></div>
        <div class="org-card-body" id="org-controls" style="padding:16px 20px;"></div>
      </div>
    </div>
    <div class="chart-row">
      <div class="chart-card" style="flex:1;">
        <div class="chart-hdr"><div class="chart-title">Recent Activity</div><div class="chart-sub">Behavioral state changes across the org</div></div>
        <div class="activity-list" id="activity-list"></div>
      </div>
    </div>
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

  <!-- ── API KEYS ── -->
  <div id="page-keys" class="page">
    <div class="sec-hdr"><div class="sec-title">API Keys — One Per Team</div></div>
    <div style="background:var(--panel);border:1px solid rgba(0,221,212,.2);border-radius:6px;padding:16px 20px;margin-bottom:20px;border-left:3px solid var(--accent);">
      <div style="font-size:10px;font-weight:800;color:var(--accent);letter-spacing:.12em;text-transform:uppercase;margin-bottom:8px;">Drop-in Anthropic Replacement</div>
      <div style="font-size:11px;color:var(--text2);line-height:1.7;">Replace <code style="background:var(--panel2);padding:1px 5px;border-radius:2px;color:var(--accent);">https://api.anthropic.com</code> with your AiGain endpoint. Pass your team key via <code style="background:var(--panel2);padding:1px 5px;border-radius:2px;color:var(--accent);">x-aigain-key</code> header. Behavioral state is injected automatically per team policy.</div>
      <div style="margin-top:10px;background:var(--panel2);border:1px solid var(--border2);border-radius:4px;padding:10px 14px;font-size:11px;font-family:monospace;color:#A78BFA;">
        client = Anthropic(<br>
        &nbsp;&nbsp;base_url=<span style="color:#34D399">"http://your-aigain-host/v1"</span>,<br>
        &nbsp;&nbsp;api_key=<span style="color:#34D399">"ag-your-team-key"</span>,<br>
        )
      </div>
    </div>
    <div id="team-keys-grid" style="display:flex;flex-direction:column;gap:10px;"></div>
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

<!-- ── ADD KEY MODAL ── -->
<div class="modal-overlay" id="add-key-modal">
  <div class="modal">
    <div class="modal-hdr">
      <div class="modal-title">Generate API Key</div>
      <button class="modal-close" onclick="closeModal('add-key-modal')">✕</button>
    </div>
    <div class="modal-body">
      <div class="form-field"><div class="form-lbl">Label</div><input class="form-input" id="new-key-label" placeholder="e.g. Engineering Integration" maxlength="60"></div>
      <div class="form-field"><div class="form-lbl">Team Assignment</div><select class="form-select" id="new-key-team"><option value="">— Org default —</option></select></div>
    </div>
    <div class="modal-footer">
      <button class="btn" onclick="closeModal('add-key-modal')">Cancel</button>
      <button class="btn primary" onclick="createKey()">Generate</button>
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
  renderActivity();
  setTimeout(renderCharts, 50);
}

function renderActivity(){
  const teamMap = {};
  ORG.teams.forEach(t=>teamMap[t.id]=t);
  const events = [
    {color:'#00DDD4', text:'<strong>Engineering</strong> policy updated — intensity raised to 0.75', time:'2 min ago'},
    {color:'#A78BFA', text:'<strong>Jordan Park</strong> override set — depth 0.80 (was team default)', time:'14 min ago'},
    {color:'#F59E0B', text:'<strong>Research</strong> switched to EXPLORE mode', time:'1 hr ago'},
    {color:'#34D399', text:'<strong>Morgan Lee</strong> override set — intensity 0.40', time:'3 hrs ago'},
    {color:'#00DDD4', text:'Org-wide policy reset — all teams synced to defaults', time:'Yesterday'},
    {color:'#F87171', text:'<strong>Sales</strong> intensity limit flagged — 2 members exceeded max', time:'Yesterday'},
  ];
  const el = document.getElementById('activity-list');
  if(!el) return;
  el.innerHTML = events.map(e=>`
    <div class="activity-item">
      <div class="activity-dot" style="background:${e.color};box-shadow:0 0 6px ${e.color}88;"></div>
      <div class="activity-text">${e.text}</div>
      <div class="activity-time">${e.time}</div>
    </div>
  `).join('');
}

function renderStats(){
  const u = ORG.usage;
  const total = ORG.teams.reduce((a,t)=>a+t.members,0);
  const overrides = ORG.members.filter(m=>m.intensity!=null||m.depth!=null).length;
  const usedPct = Math.min(100, (u.tokens_month / 150000000) * 100).toFixed(0);
  const savePct = Math.min(100, (u.estimated_save / u.cost_month) * 100).toFixed(0);
  document.getElementById('stats-bar').innerHTML = `
    <div class="stat-card c-accent">
      <div class="stat-lbl">Total Members</div>
      <div class="stat-val accent">${total}</div>
      <div class="stat-sub">${ORG.teams.length} teams active</div>
      <div class="stat-bar-wrap"><div class="stat-bar-fill" style="width:${Math.min(100,total/2)}%;background:var(--accent)"></div></div>
    </div>
    <div class="stat-card c-purple">
      <div class="stat-lbl">Tokens This Month</div>
      <div class="stat-val purple">${fmtTokens(u.tokens_month)}</div>
      <div class="stat-sub">$${u.cost_month.toFixed(2)} est. cost</div>
      <div class="stat-bar-wrap"><div class="stat-bar-fill" style="width:${usedPct}%;background:var(--purple2)"></div></div>
    </div>
    <div class="stat-card c-green">
      <div class="stat-lbl">Estimated Savings</div>
      <div class="stat-val green">$${u.estimated_save.toFixed(2)}</div>
      <div class="stat-sub">${savePct}% reduction via throttling</div>
      <div class="stat-bar-wrap"><div class="stat-bar-fill" style="width:${savePct}%;background:var(--green)"></div></div>
    </div>
    <div class="stat-card c-amber">
      <div class="stat-lbl">Active Overrides</div>
      <div class="stat-val amber">${overrides}</div>
      <div class="stat-sub">individual customizations</div>
      <div class="stat-bar-wrap"><div class="stat-bar-fill" style="width:${Math.min(100,overrides*10)}%;background:var(--amber)"></div></div>
    </div>
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
  const hex = t.color;
  const modeColor = t.policy.mode==='EXPLORE' ? '#A78BFA' : '#00DDD4';
  return `
    <div class="team-card" style="border-color:${hex}22;">
      <div class="team-hdr" style="background:linear-gradient(135deg,${hex}0A 0%,transparent 100%);">
        <div class="team-dot" style="background:${hex};color:${hex};box-shadow:0 0 8px ${hex}88;"></div>
        <div class="team-name" style="color:${hex};">${t.name}</div>
        <div class="team-count">${t.members} members</div>
      </div>
      <div class="team-body">
        <div class="team-mode-row">
          ${modes.map(m=>`<button class="team-mode-btn ${t.policy.mode===m?'active':''}" style="${t.policy.mode===m?`border-color:${hex};color:${hex};background:${hex}14;`:''}" onclick="setTeamMode('${t.id}','${m}')">${m}</button>`).join('')}
        </div>
        <div class="team-fader-row">
          <div class="team-fader-lbl"><span class="team-fader-name">Intensity</span><span class="team-fader-val" id="tv-${t.id}-intensity" style="color:${hex}">${fmt(t.policy.intensity)}</span></div>
          <input type="range" class="team-fader" min="0" max="1" step="0.05" value="${t.policy.intensity}" style="accent-color:${hex}" oninput="updateTeamSlider('${t.id}','intensity',this.value)">
        </div>
        <div class="team-fader-row">
          <div class="team-fader-lbl"><span class="team-fader-name">Depth</span><span class="team-fader-val" id="tv-${t.id}-depth" style="color:${hex}">${fmt(t.policy.depth)}</span></div>
          <input type="range" class="team-fader" min="0" max="1" step="0.05" value="${t.policy.depth}" style="accent-color:${hex}" oninput="updateTeamSlider('${t.id}','depth',this.value)">
        </div>
        <div class="team-fader-row">
          <div class="team-fader-lbl"><span class="team-fader-name">Verbosity</span><span class="team-fader-val" id="tv-${t.id}-room" style="color:${hex}">${fmt(t.policy.room)}</span></div>
          <input type="range" class="team-fader" min="0" max="1" step="0.05" value="${t.policy.room}" style="accent-color:${hex}" oninput="updateTeamSlider('${t.id}','room',this.value)">
        </div>
      </div>
      <div class="team-footer" style="background:${hex}06;">
        <span class="team-footer-mode" style="color:${hex};background:${hex}14;border:1px solid ${hex}33;">${t.policy.mode}</span>
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
  await fetch('/api/keys/ensure', {method:'POST'});
  closeModal('add-team-modal');
  document.getElementById('new-team-name').value = '';
  renderTeams();
  loadKeys();
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

// ── Charts ────────────────────────────────────────────────────────────────────

const CHART_DEFAULTS = {
  color: '#D8EAF8',
  grid: 'rgba(22,32,48,.8)',
  tick: '#405870',
};
Chart.defaults.color = CHART_DEFAULTS.color;
Chart.defaults.font.family = 'Inter';
Chart.defaults.font.size = 10;

let _charts = {};

function destroyChart(id){
  if(_charts[id]){ _charts[id].destroy(); delete _charts[id]; }
}

function renderCharts(){
  if(!ORG) return;

  // ── 30-day usage line chart ───────────────────────────────────────────────
  destroyChart('usage');
  const days = Array.from({length:30},(_,i)=>{
    const d = new Date(); d.setDate(d.getDate()-29+i);
    return d.toLocaleDateString('en-US',{month:'short',day:'numeric'});
  });
  const baseUsage = [2.1,2.4,1.8,3.1,3.8,2.9,3.4,4.1,3.7,4.8,4.2,5.1,4.6,5.8,5.2,6.1,5.7,6.8,6.2,7.1,6.5,7.8,7.2,8.1,7.6,8.8,8.2,9.1,8.5,9.6].map(v=>v*1000000);
  const ctx1 = document.getElementById('chart-usage');
  if(ctx1) _charts['usage'] = new Chart(ctx1, {
    type:'line',
    data:{
      labels: days,
      datasets:[
        {label:'All Teams', data:baseUsage, borderColor:'#00DDD4', backgroundColor:'rgba(0,221,212,.06)', fill:true, tension:.4, pointRadius:0, pointHoverRadius:4, borderWidth:2},
        {label:'Engineering', data:baseUsage.map(v=>v*.18), borderColor:'#00DDD4', backgroundColor:'transparent', fill:false, tension:.4, pointRadius:0, borderWidth:1, borderDash:[4,4]},
        {label:'Support', data:baseUsage.map(v=>v*.28), borderColor:'#A78BFA', backgroundColor:'transparent', fill:false, tension:.4, pointRadius:0, borderWidth:1, borderDash:[4,4]},
        {label:'Research', data:baseUsage.map(v=>v*.10), borderColor:'#F59E0B', backgroundColor:'transparent', fill:false, tension:.4, pointRadius:0, borderWidth:1, borderDash:[4,4]},
        {label:'Sales', data:baseUsage.map(v=>v*.40), borderColor:'#34D399', backgroundColor:'transparent', fill:false, tension:.4, pointRadius:0, borderWidth:1, borderDash:[4,4]},
      ]
    },
    options:{
      responsive:true, maintainAspectRatio:false,
      interaction:{mode:'index', intersect:false},
      plugins:{legend:{display:true, position:'top', labels:{boxWidth:8, padding:16, font:{size:9,weight:'700'}}}, tooltip:{backgroundColor:'#0B1018', borderColor:'#1E2E40', borderWidth:1, padding:10, titleFont:{size:10}, bodyFont:{size:10}}},
      scales:{
        x:{grid:{color:CHART_DEFAULTS.grid}, ticks:{color:CHART_DEFAULTS.tick, maxTicksLimit:8}},
        y:{grid:{color:CHART_DEFAULTS.grid}, ticks:{color:CHART_DEFAULTS.tick, callback:v=>fmtTokens(v)}}
      }
    }
  });

  // ── Team usage donut ──────────────────────────────────────────────────────
  destroyChart('donut');
  const ctx2 = document.getElementById('chart-donut');
  if(ctx2) _charts['donut'] = new Chart(ctx2, {
    type:'doughnut',
    data:{
      labels: ORG.teams.map(t=>t.name),
      datasets:[{data: ORG.teams.map(t=>t.members), backgroundColor: ORG.teams.map(t=>t.color+'CC'), borderColor: ORG.teams.map(t=>t.color), borderWidth:1.5}]
    },
    options:{
      responsive:true, maintainAspectRatio:false, cutout:'68%',
      plugins:{legend:{position:'bottom', labels:{boxWidth:8, padding:10, font:{size:9,weight:'700'}}}, tooltip:{backgroundColor:'#0B1018', borderColor:'#1E2E40', borderWidth:1, padding:10}}
    }
  });

  // ── Mode distribution bar ─────────────────────────────────────────────────
  destroyChart('mode');
  const ctx3 = document.getElementById('chart-mode');
  if(ctx3) _charts['mode'] = new Chart(ctx3, {
    type:'bar',
    data:{
      labels: ORG.teams.map(t=>t.name),
      datasets:[
        {label:'EXPLORE', data: ORG.teams.map(t=>t.policy.mode==='EXPLORE'?t.members:Math.round(t.members*.35)), backgroundColor:'rgba(167,139,250,.7)', borderColor:'#A78BFA', borderWidth:1},
        {label:'BUILD',   data: ORG.teams.map(t=>t.policy.mode==='BUILD'?t.members:Math.round(t.members*.65)), backgroundColor:'rgba(0,221,212,.7)', borderColor:'#00DDD4', borderWidth:1},
      ]
    },
    options:{
      responsive:true, maintainAspectRatio:false,
      plugins:{legend:{position:'top', labels:{boxWidth:8, padding:12, font:{size:9,weight:'700'}}}, tooltip:{backgroundColor:'#0B1018', borderColor:'#1E2E40', borderWidth:1, padding:10}},
      scales:{
        x:{stacked:true, grid:{color:CHART_DEFAULTS.grid}, ticks:{color:CHART_DEFAULTS.tick}},
        y:{stacked:true, grid:{color:CHART_DEFAULTS.grid}, ticks:{color:CHART_DEFAULTS.tick}}
      }
    }
  });

  // ── Intensity by team bar ─────────────────────────────────────────────────
  destroyChart('intensity');
  const ctx4 = document.getElementById('chart-intensity');
  if(ctx4) _charts['intensity'] = new Chart(ctx4, {
    type:'bar',
    data:{
      labels: ORG.teams.map(t=>t.name),
      datasets:[{
        label:'Avg Intensity',
        data: ORG.teams.map(t=>t.policy.intensity),
        backgroundColor: ORG.teams.map(t=>t.color+'99'),
        borderColor: ORG.teams.map(t=>t.color),
        borderWidth:1.5, borderRadius:3,
      }]
    },
    options:{
      responsive:true, maintainAspectRatio:false, indexAxis:'y',
      plugins:{legend:{display:false}, tooltip:{backgroundColor:'#0B1018', borderColor:'#1E2E40', borderWidth:1, padding:10, callbacks:{label:ctx=>'Intensity: '+ctx.raw.toFixed(2)}}},
      scales:{
        x:{min:0, max:1, grid:{color:CHART_DEFAULTS.grid}, ticks:{color:CHART_DEFAULTS.tick, callback:v=>v.toFixed(1)}},
        y:{grid:{display:false}, ticks:{color:CHART_DEFAULTS.tick}}
      }
    }
  });

  // ── Override rate donut ───────────────────────────────────────────────────
  destroyChart('override');
  const overrideCount = ORG.members.filter(m=>m.intensity!=null||m.depth!=null).length;
  const inheritCount  = ORG.members.length - overrideCount;
  const ctx5 = document.getElementById('chart-override');
  if(ctx5) _charts['override'] = new Chart(ctx5, {
    type:'doughnut',
    data:{
      labels:['Inherited','Override'],
      datasets:[{data:[inheritCount, overrideCount], backgroundColor:['rgba(0,221,212,.2)','rgba(245,158,11,.7)'], borderColor:['#00DDD4','#F59E0B'], borderWidth:1.5}]
    },
    options:{
      responsive:true, maintainAspectRatio:false, cutout:'72%',
      plugins:{legend:{position:'bottom', labels:{boxWidth:8, padding:10, font:{size:9,weight:'700'}}}, tooltip:{backgroundColor:'#0B1018', borderColor:'#1E2E40', borderWidth:1, padding:10}}
    }
  });
}

// ── API Key management ─────────────────────────────────────────────────────────

let KEYS = {};

async function loadKeys(){
  const r = await fetch('/api/keys');
  KEYS = await r.json();
  renderKeys();
}

function openAddKey(){
  const sel = document.getElementById('new-key-team');
  if(sel && ORG) sel.innerHTML = '<option value="">— Org default —</option>' + ORG.teams.map(t=>`<option value="${t.id}">${t.name}</option>`).join('');
  document.getElementById('add-key-modal').classList.add('open');
}

async function createKey(){
  const label   = document.getElementById('new-key-label').value.trim() || 'Unnamed key';
  const team_id = document.getElementById('new-key-team').value || null;
  const r = await fetch('/api/keys', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({label, team_id})});
  const data = await r.json();
  closeModal('add-key-modal');
  document.getElementById('new-key-label').value = '';
  alert('Key generated:\\n\\n' + data.key + '\\n\\nCopy this now — it will not be shown again.');
  loadKeys();
}

async function revokeKey(keyId){
  if(!confirm('Revoke this key? It will stop working immediately.')) return;
  await fetch('/api/keys/'+keyId, {method:'DELETE'});
  loadKeys();
}

function renderKeys(){
  if(!ORG) return;
  const grid = document.getElementById('team-keys-grid');
  if(!grid) return;
  grid.innerHTML = ORG.teams.map(team=>{
    const entry = Object.entries(KEYS).find(([,v])=>v.team_id===team.id && v.active);
    const kid   = entry ? entry[0] : null;
    const k     = entry ? entry[1] : null;
    const masked = k ? k.key.slice(0,10)+'••••••••••••••••••••'+k.key.slice(-4) : '—';
    return `
      <div style="background:var(--panel);border:1px solid ${team.color}33;border-radius:6px;padding:16px 20px;display:flex;align-items:center;gap:20px;border-left:3px solid ${team.color};">
        <div style="flex-shrink:0;">
          <div style="font-size:12px;font-weight:800;color:${team.color};margin-bottom:3px;">${team.name}</div>
          <div style="font-size:9px;color:var(--text3);">${team.members} members · ${team.policy.mode}</div>
        </div>
        <code style="flex:1;font-size:11px;color:var(--text3);letter-spacing:.04em;background:var(--panel2);padding:8px 12px;border-radius:3px;border:1px solid var(--border2);">${masked}</code>
        <div style="display:flex;gap:8px;flex-shrink:0;">
          ${k ? `<button class="sec-action" onclick="copyKey('${k.key}')" style="border-color:${team.color}55;color:${team.color};">Copy Key</button>` : ''}
          ${kid && k?.active ? `<button class="team-edit-btn" onclick="revokeKey('${kid}')" style="color:var(--red);border-color:rgba(248,113,113,.3);">Revoke</button>` : ''}
          ${!k ? `<button class="sec-action" onclick="regenerateTeamKey('${team.id}')">Generate</button>` : ''}
        </div>
      </div>
    `;
  }).join('');
}

function copyKey(key){
  navigator.clipboard.writeText(key).then(()=>{
    const btn = event.target;
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(()=>btn.textContent=orig, 1500);
  });
}

async function regenerateTeamKey(teamId){
  const team = ORG.teams.find(t=>t.id===teamId);
  if(!team) return;
  const r = await fetch('/api/keys', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({label: team.name+' Key', team_id: teamId})});
  const data = await r.json();
  loadKeys();
}

function togglePolicyEdit(){
  const body = document.getElementById('org-controls');
  if(body) body.style.display = body.style.display==='none' ? '' : 'none';
}

loadOrg();
loadKeys();
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


# ── API key management routes ──────────────────────────────────────────────────

@app.route('/api/keys', methods=['GET'])
def list_keys():
    return jsonify(load_keys())

@app.route('/api/keys/ensure', methods=['POST'])
def ensure_keys_route():
    ensure_team_keys()
    return jsonify({'ok': True})

@app.route('/api/keys', methods=['POST'])
def create_key():
    data      = request.get_json() or {}
    org       = load_org()
    keys      = load_keys()
    key_value = generate_key(org.get("id", "default"))
    key_id    = "kid_" + uuid.uuid4().hex[:12]
    keys[key_id] = {
        "key":       key_value,
        "label":     data.get("label", "Unnamed key"),
        "team_id":   data.get("team_id"),
        "member_id": data.get("member_id"),
        "created":   int(time.time()),
        "active":    True,
    }
    save_keys(keys)
    return jsonify({"ok": True, "key_id": key_id, "key": key_value})

@app.route('/api/keys/<key_id>', methods=['DELETE'])
def revoke_key(key_id):
    keys = load_keys()
    if key_id in keys:
        keys[key_id]["active"] = False
        save_keys(keys)
    return jsonify({"ok": True})

@app.route('/api/usage', methods=['GET'])
def get_usage():
    try:
        log = json.loads(USAGE_FILE.read_text()) if USAGE_FILE.exists() else []
        return jsonify(log[-500:])
    except Exception:
        return jsonify([])


# ── Proxy endpoint — drop-in Anthropic replacement ────────────────────────────
#
#   Companies point their SDK at:  http://your-aigain-host/v1/messages
#   They pass their AiGain API key in x-aigain-key header (or Authorization)
#   AiGain injects behavioral system prompt then forwards to Anthropic.

@app.route('/v1/messages', methods=['POST'])
def proxy_messages():
    # ── Authenticate ──────────────────────────────────────────────────────────
    ag_key = (
        request.headers.get("x-aigain-key") or
        request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    )
    keys = load_keys()
    key_meta = next(
        (v for v in keys.values() if v.get("key") == ag_key and v.get("active")),
        None
    )
    if not key_meta and ag_key != os.environ.get("AIGAIN_MASTER_KEY", ""):
        return jsonify({"error": "invalid_api_key", "message": "Provide a valid AiGain API key via x-aigain-key header."}), 401

    # ── Resolve behavioral policy ──────────────────────────────────────────────
    org    = load_org()
    policy = resolve_policy(key_meta or {}, org)
    behavioral_prompt = build_behavioral_prompt(policy)

    # ── Modify request body — inject behavioral system prompt ─────────────────
    body = request.get_json(force=True) or {}
    existing_system = body.get("system", "")
    if existing_system:
        body["system"] = behavioral_prompt + "\n\n---\n\n" + existing_system
    else:
        body["system"] = behavioral_prompt

    # ── Forward to Anthropic ───────────────────────────────────────────────────
    if not ANTHROPIC_KEY:
        return jsonify({"error": "no_upstream_key", "message": "ANTHROPIC_API_KEY not configured on AiGain server."}), 500

    upstream_headers = {
        "Content-Type":      "application/json",
        "x-api-key":         ANTHROPIC_KEY,
        "anthropic-version": request.headers.get("anthropic-version", "2023-06-01"),
        "anthropic-beta":    request.headers.get("anthropic-beta", ""),
    }
    upstream_headers = {k: v for k, v in upstream_headers.items() if v}

    payload = json.dumps(body).encode()
    req     = urllib.request.Request(ANTHROPIC_URL, data=payload, headers=upstream_headers, method="POST")

    is_stream = body.get("stream", False)

    try:
        resp = urllib.request.urlopen(req, timeout=120)

        if is_stream:
            def generate():
                try:
                    while True:
                        chunk = resp.read(1024)
                        if not chunk:
                            break
                        yield chunk
                finally:
                    resp.close()
            return Response(
                stream_with_context(generate()),
                status=resp.status,
                content_type=resp.headers.get("Content-Type", "text/event-stream"),
                headers={"X-AiGain-Team": key_meta.get("team_id","") if key_meta else "",
                         "X-AiGain-Mode": policy.get("mode",""),
                         "Cache-Control": "no-cache",
                         "X-Accel-Buffering": "no"},
            )
        else:
            raw  = resp.read()
            data = json.loads(raw)
            # Log usage
            usage = data.get("usage", {})
            log_usage(
                key_id    = next((k for k,v in keys.items() if v.get("key")==ag_key), "unknown"),
                team_id   = key_meta.get("team_id","") if key_meta else "",
                member_id = key_meta.get("member_id","") if key_meta else "",
                model     = data.get("model",""),
                input_tokens  = usage.get("input_tokens", 0),
                output_tokens = usage.get("output_tokens", 0),
            )
            return jsonify(data), resp.status

    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        try:    err_json = json.loads(err_body)
        except: err_json = {"error": err_body}
        return jsonify(err_json), e.code
    except Exception as e:
        return jsonify({"error": "proxy_error", "message": str(e)}), 502


# ── Proxy test endpoint ────────────────────────────────────────────────────────

@app.route('/v1/test', methods=['POST'])
def test_proxy():
    """Quick test: returns the behavioral prompt that would be injected."""
    ag_key   = request.headers.get("x-aigain-key", "")
    keys     = load_keys()
    key_meta = next((v for v in keys.values() if v.get("key") == ag_key), None)
    org      = load_org()
    policy   = resolve_policy(key_meta or {}, org)
    return jsonify({
        "ok":               True,
        "team_id":          key_meta.get("team_id") if key_meta else None,
        "member_id":        key_meta.get("member_id") if key_meta else None,
        "policy":           policy,
        "behavioral_prompt": build_behavioral_prompt(policy),
    })


if __name__ == '__main__':
    ensure_team_keys()
    print('\n┌─────────────────────────────────────┐')
    print('│  AiGain  ·  Enterprise AI Control    │')
    print('│  http://127.0.0.1:5571               │')
    print('└─────────────────────────────────────┘\n')
    port = int(os.environ.get("PORT", 5571))
    app.run(host='0.0.0.0', port=port, debug=False)
