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
.brand{font-family:'Abril Fatface',serif;display:flex;align-items:baseline;gap:0;line-height:1;}
.brand-ai{font-size:15px;letter-spacing:.06em;color:rgba(160,200,255,.55);-webkit-text-fill-color:rgba(160,200,255,.55);}
.brand-gain{font-size:34px;letter-spacing:.04em;background:linear-gradient(130deg,#00E8FF,#A0C8FF,#D946EF);-webkit-background-clip:text;-webkit-text-fill-color:transparent;filter:drop-shadow(0 0 10px rgba(0,200,255,.5)) drop-shadow(0 0 24px rgba(217,70,239,.3));}
.hdr-tag{font-size:8px;font-weight:900;letter-spacing:.2em;text-transform:uppercase;color:var(--text3);border:1px solid var(--border2);padding:3px 8px;border-radius:2px;}
.hdr-right{margin-left:auto;display:flex;align-items:center;gap:10px;}
.hdr-org{font-size:11px;font-weight:700;color:var(--text2);letter-spacing:.04em;}
.hdr-plan{font-size:8px;font-weight:900;letter-spacing:.16em;text-transform:uppercase;color:var(--accent);border:1px solid rgba(0,221,212,.3);padding:3px 8px;border-radius:2px;background:rgba(0,221,212,.05);}
.hdr-usage{font-size:9px;font-weight:700;color:var(--text3);letter-spacing:.06em;}

/* ── HERO SIGNAL ── */
.hero-signal{width:100%;height:140px;display:block;flex-shrink:0;position:relative;overflow:hidden;}
.hero-signal svg{width:100%;height:100%;}
.hero-signal-label{position:absolute;left:20px;bottom:14px;font-size:8px;font-weight:900;letter-spacing:.28em;text-transform:uppercase;color:rgba(0,221,212,.35);pointer-events:none;}
.hero-gain-overlay{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);font-family:'Abril Fatface',serif;font-size:80px;letter-spacing:.08em;background:linear-gradient(130deg,#00E8FF 0%,#A0C8FF 50%,#D946EF 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;filter:drop-shadow(0 0 30px rgba(0,200,255,.35)) drop-shadow(0 0 60px rgba(217,70,239,.2));opacity:.92;pointer-events:none;white-space:nowrap;}

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

/* ── FAQ ── */
.faq-btn{width:30px;height:30px;border-radius:50%;border:1px solid rgba(0,221,212,.4);background:rgba(0,221,212,.07);color:var(--accent);font-size:14px;font-weight:800;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .15s;line-height:1;flex-shrink:0;font-family:'Inter',sans-serif;}
.faq-btn:hover{background:rgba(0,221,212,.18);border-color:var(--accent);box-shadow:0 0 12px rgba(0,221,212,.25);}
.faq-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:300;}
.faq-overlay.open{display:block;}
.faq-panel{position:fixed;right:-520px;top:0;bottom:0;width:480px;background:var(--panel);border-left:2px solid var(--accent);z-index:301;transition:right .26s cubic-bezier(.4,0,.2,1);display:flex;flex-direction:column;box-shadow:-8px 0 40px rgba(0,0,0,.8);}
.faq-panel.open{right:0;}
.faq-hd{padding:16px 20px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;flex-shrink:0;background:#040810;}
.faq-title{font-family:'Abril Fatface',serif;font-size:22px;color:var(--accent);text-shadow:0 0 20px rgba(0,221,212,.35);}
.faq-close{width:26px;height:26px;border:1px solid var(--border2);background:transparent;cursor:pointer;font-size:13px;color:var(--text2);border-radius:50%;transition:background .12s;display:flex;align-items:center;justify-content:center;font-weight:700;}
.faq-close:hover{background:var(--panel2);color:var(--accent);}
.faq-body{padding:24px 22px;flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:22px;}
.faq-s{}
.faq-s-title{font-size:8px;font-weight:900;letter-spacing:.24em;text-transform:uppercase;color:var(--accent);margin-bottom:8px;text-shadow:0 0 8px rgba(0,200,192,.3);}
.faq-p{font-size:12px;line-height:1.75;color:var(--text2);margin-bottom:6px;}
.faq-p strong{color:var(--text);font-weight:700;}
.faq-divider{height:1px;background:var(--border);margin:2px 0;}
.faq-track{margin-bottom:8px;padding:10px 14px;background:#060A0F;border-radius:4px;border-left:3px solid var(--accent);}
.faq-track-name{font-size:10px;font-weight:800;color:var(--accent);margin-bottom:4px;letter-spacing:.06em;}
.faq-track-desc{font-size:11px;color:var(--text2);line-height:1.6;}
.faq-code{font-family:'Courier New',monospace;font-size:10px;background:#040608;padding:8px 12px;border-radius:3px;color:var(--accent);margin:6px 0;display:block;border:1px solid var(--border);line-height:1.6;}
.faq-tag{display:inline-block;font-size:8px;font-weight:900;letter-spacing:.12em;text-transform:uppercase;padding:2px 7px;border-radius:2px;margin-right:4px;margin-bottom:4px;}
.faq-tag.build{color:var(--accent);background:rgba(0,221,212,.1);border:1px solid rgba(0,221,212,.25);}
.faq-tag.explore{color:var(--purple2);background:rgba(167,139,250,.1);border:1px solid rgba(167,139,250,.25);}

/* ── CTRL DOCK ── */
.ctrl-dock{position:fixed;bottom:0;left:0;right:0;z-index:150;background:linear-gradient(180deg,rgba(3,5,9,.97) 0%,#020407 100%);border-top:2px solid rgba(0,221,212,.18);backdrop-filter:blur(24px);display:flex;flex-direction:column;height:46vh;transition:height .3s cubic-bezier(.4,0,.2,1);}
.ctrl-dock.collapsed{height:44px;}
.ctrl-dock-hdr{height:44px;flex-shrink:0;display:flex;align-items:center;padding:0 20px;gap:12px;border-bottom:1px solid rgba(0,221,212,.1);cursor:pointer;user-select:none;}
.ctrl-dock-hdr::before{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(0,221,212,.4),rgba(217,70,239,.2),transparent);}
.dock-title{font-size:8px;font-weight:900;letter-spacing:.28em;text-transform:uppercase;color:rgba(0,221,212,.6);}
.dock-mode-tag{font-size:8px;font-weight:900;letter-spacing:.14em;text-transform:uppercase;padding:2px 8px;border-radius:2px;border:1px solid rgba(0,221,212,.3);color:var(--accent);background:rgba(0,221,212,.06);}
.dock-midi-tag{font-size:8px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--text3);border:1px solid var(--border);padding:2px 8px;border-radius:2px;transition:all .2s;}
.dock-midi-tag.active{color:#34D399;border-color:rgba(52,211,153,.4);background:rgba(52,211,153,.08);}
.dock-lock-btn{margin-left:auto;height:26px;padding:0 12px;border-radius:2px;border:1px solid rgba(245,158,11,.4);background:rgba(245,158,11,.06);color:#F59E0B;font-size:8px;font-weight:900;letter-spacing:.14em;text-transform:uppercase;cursor:pointer;font-family:'Inter',sans-serif;transition:all .15s;display:flex;align-items:center;gap:5px;}
.dock-lock-btn.unlocked{border-color:rgba(0,221,212,.4);background:rgba(0,221,212,.07);color:var(--accent);}
.dock-lock-btn:hover{opacity:.8;}
.dock-collapse-btn{width:26px;height:26px;border-radius:2px;border:1px solid var(--border2);background:transparent;color:var(--text3);font-size:11px;cursor:pointer;display:flex;align-items:center;justify-content:center;font-family:'Inter',sans-serif;transition:all .15s;flex-shrink:0;}
.dock-collapse-btn:hover{border-color:var(--accent);color:var(--accent);}
.ctrl-dock-body{flex:1;display:grid;grid-template-columns:1fr 1fr 1fr;min-height:0;overflow:hidden;}
.ctrl-dock.collapsed .ctrl-dock-body{opacity:0;pointer-events:none;}
.ag-ch{display:flex;flex-direction:column;padding:16px 20px 12px;border-right:1px solid var(--border);position:relative;}
.ag-ch:last-child{border-right:none;}
.ag-ch-hdr{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:10px;flex-shrink:0;}
.ag-ch-lbl{font-size:9px;font-weight:900;letter-spacing:.22em;text-transform:uppercase;}
.ag-ch-sub{font-size:8px;font-weight:600;letter-spacing:.06em;color:var(--text3);}
.ag-ch-val{font-size:22px;font-weight:900;font-variant-numeric:tabular-nums;font-family:'Inter',sans-serif;line-height:1;letter-spacing:-.02em;}
.t1 .ag-ch-lbl{color:var(--accent);}
.t2 .ag-ch-lbl{color:var(--purple2);}
.t3 .ag-ch-lbl{color:var(--magenta2);}
.t1 .ag-ch-val{color:var(--accent);text-shadow:0 0 20px rgba(0,221,212,.4);}
.t2 .ag-ch-val{color:var(--purple2);text-shadow:0 0 20px rgba(167,139,250,.4);}
.t3 .ag-ch-val{color:var(--magenta2);text-shadow:0 0 20px rgba(240,171,255,.4);}
.ag-fader-rail{flex:1;display:flex;justify-content:center;min-height:0;padding:4px 0;}
.ag-fader-track{width:calc(100% - 16px);height:100%;background:rgba(2,6,14,.94);border-radius:5px;position:relative;cursor:ns-resize;touch-action:none;overflow:hidden;transition:box-shadow .15s;}
.t1 .ag-fader-track{border:1px solid rgba(0,180,172,.22);box-shadow:inset 0 0 0 1px rgba(0,180,172,.05),inset 0 8px 28px rgba(0,0,0,.92),0 0 0 1px rgba(0,0,0,.6);}
.t2 .ag-fader-track{border:1px solid rgba(139,92,246,.22);box-shadow:inset 0 0 0 1px rgba(139,92,246,.05),inset 0 8px 28px rgba(0,0,0,.92),0 0 0 1px rgba(0,0,0,.6);}
.t3 .ag-fader-track{border:1px solid rgba(217,70,239,.22);box-shadow:inset 0 0 0 1px rgba(217,70,239,.05),inset 0 8px 28px rgba(0,0,0,.92),0 0 0 1px rgba(0,0,0,.6);}
.ag-fader-fill{position:absolute;bottom:0;left:0;right:0;pointer-events:none;transition:height .04s linear;}
.t1 .ag-fader-fill{background:linear-gradient(0deg,rgba(0,148,140,.94) 0%,rgba(0,200,192,.72) 40%,rgba(0,232,224,.46) 75%,rgba(100,255,250,.18) 100%);box-shadow:0 0 24px rgba(0,200,192,.4),0 0 60px rgba(0,180,175,.12),inset 0 0 32px rgba(0,160,155,.08);}
.t2 .ag-fader-fill{background:linear-gradient(0deg,rgba(88,28,180,.94) 0%,rgba(120,64,224,.72) 40%,rgba(155,104,248,.46) 75%,rgba(210,185,255,.18) 100%);box-shadow:0 0 24px rgba(139,92,246,.4),0 0 60px rgba(139,92,246,.12),inset 0 0 32px rgba(100,58,200,.08);}
.t3 .ag-fader-fill{background:linear-gradient(0deg,rgba(180,28,160,.94) 0%,rgba(217,70,200,.72) 40%,rgba(240,130,230,.46) 75%,rgba(255,200,248,.18) 100%);box-shadow:0 0 24px rgba(217,70,239,.4),0 0 60px rgba(217,70,239,.12),inset 0 0 32px rgba(180,40,200,.08);}
.ag-fader-thumb{position:absolute;width:100%;height:4px;left:0;cursor:ns-resize;z-index:3;touch-action:none;border-radius:2px;pointer-events:none;}
.t1 .ag-fader-thumb{background:linear-gradient(90deg,transparent,rgba(0,228,220,.6) 18%,rgba(190,255,252,.94) 50%,rgba(0,228,220,.6) 82%,transparent);box-shadow:0 0 12px rgba(0,220,212,1),0 0 28px rgba(0,200,192,.65);}
.t2 .ag-fader-thumb{background:linear-gradient(90deg,transparent,rgba(158,128,250,.6) 18%,rgba(224,198,255,.94) 50%,rgba(158,128,250,.6) 82%,transparent);box-shadow:0 0 12px rgba(167,139,250,1),0 0 28px rgba(139,92,246,.65);}
.t3 .ag-fader-thumb{background:linear-gradient(90deg,transparent,rgba(240,130,230,.6) 18%,rgba(255,210,252,.94) 50%,rgba(240,130,230,.6) 82%,transparent);box-shadow:0 0 12px rgba(240,171,255,1),0 0 28px rgba(217,70,239,.65);}
.ag-fader-track.dragging .ag-fader-fill{transition:none;}
.ctrl-dock.locked .ag-fader-track{cursor:not-allowed;opacity:.55;}
.ctrl-dock.locked .ag-fader-thumb{opacity:.4;}
.ag-ch-accent{position:absolute;top:0;left:0;right:0;height:2px;}
.t1 .ag-ch-accent{background:linear-gradient(90deg,transparent,var(--accent),transparent);box-shadow:0 0 8px rgba(0,221,212,.5);}
.t2 .ag-ch-accent{background:linear-gradient(90deg,transparent,var(--purple2),transparent);box-shadow:0 0 8px rgba(139,92,246,.5);}
.t3 .ag-ch-accent{background:linear-gradient(90deg,transparent,var(--magenta2),transparent);box-shadow:0 0 8px rgba(217,70,239,.5);}

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
  <div class="brand"><span class="brand-ai">Ai</span><span class="brand-gain">GAIN</span></div>
  <div class="hdr-tag">Enterprise</div>
  <div class="hdr-right">
    <div class="hdr-org" id="hdr-org-name">—</div>
    <div class="hdr-plan">Enterprise Plan</div>
    <div class="hdr-usage" id="hdr-usage">—</div>
    <button class="faq-btn" onclick="openFaq()" title="What is AiGain?">?</button>
  </div>
</header>

<!-- ── HERO SIGNAL STRIP ── -->
<div class="hero-signal">
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 140" preserveAspectRatio="xMidYMid slice">
    <defs>
      <linearGradient id="ag-bg" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#06090E"/>
        <stop offset="50%" stop-color="#040810"/>
        <stop offset="100%" stop-color="#030507"/>
      </linearGradient>
      <linearGradient id="ag-scan" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0%" stop-color="#00C8C0" stop-opacity="0"/>
        <stop offset="35%" stop-color="#00C8C0" stop-opacity="0.08"/>
        <stop offset="65%" stop-color="#00C8C0" stop-opacity="0.08"/>
        <stop offset="100%" stop-color="#00C8C0" stop-opacity="0"/>
      </linearGradient>
      <filter id="ag-glow-teal" x="-8%" y="-80%" width="116%" height="260%">
        <feGaussianBlur in="SourceGraphic" stdDeviation="3" result="blur"/>
        <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
      </filter>
      <filter id="ag-glow-purple" x="-4%" y="-60%" width="108%" height="220%">
        <feGaussianBlur in="SourceGraphic" stdDeviation="2.5" result="blur"/>
        <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
      </filter>
      <filter id="ag-glow-magenta" x="-4%" y="-60%" width="108%" height="220%">
        <feGaussianBlur in="SourceGraphic" stdDeviation="2.2" result="blur"/>
        <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
      </filter>
    </defs>
    <!-- Background -->
    <rect width="1200" height="140" fill="url(#ag-bg)"/>
    <!-- Grid lines horizontal -->
    <line x1="0" y1="28"  x2="1200" y2="28"  stroke="#162030" stroke-width="0.5"/>
    <line x1="0" y1="70"  x2="1200" y2="70"  stroke="#1E2E40" stroke-width="0.8"/>
    <line x1="0" y1="112" x2="1200" y2="112" stroke="#162030" stroke-width="0.5"/>
    <!-- Grid lines vertical -->
    <line x1="150"  y1="0" x2="150"  y2="140" stroke="#162030" stroke-width="0.5"/>
    <line x1="300"  y1="0" x2="300"  y2="140" stroke="#162030" stroke-width="0.5"/>
    <line x1="450"  y1="0" x2="450"  y2="140" stroke="#162030" stroke-width="0.5"/>
    <line x1="600"  y1="0" x2="600"  y2="140" stroke="#1E2E40" stroke-width="0.8"/>
    <line x1="750"  y1="0" x2="750"  y2="140" stroke="#162030" stroke-width="0.5"/>
    <line x1="900"  y1="0" x2="900"  y2="140" stroke="#162030" stroke-width="0.5"/>
    <line x1="1050" y1="0" x2="1050" y2="140" stroke="#162030" stroke-width="0.5"/>
    <!-- Oscilloscope pulse (teal) -->
    <polyline points="0,70 30,70 42,34 54,106 66,18 78,122 90,46 102,94 114,70 160,70" stroke="#00DDD4" stroke-width="2" fill="none" opacity="0.8" filter="url(#ag-glow-teal)"/>
    <!-- Sine wave (purple) -->
    <path d="M160,70 C210,70 230,18 280,18 C330,18 350,122 400,122 C450,122 470,18 520,18 C570,18 590,122 640,122 C690,122 710,70 770,70 C830,70 850,18 900,18 C950,18 970,122 1020,122 C1070,122 1090,18 1140,18 C1170,18 1185,70 1200,70" stroke="#8B5CF6" stroke-width="2.2" fill="none" opacity="0.55" filter="url(#ag-glow-purple)"/>
    <!-- Magenta accent wave -->
    <path d="M0,70 C70,70 90,42 160,42 C240,42 260,98 340,98 C420,98 440,42 520,42 C600,42 620,98 700,98 C780,98 800,70 880,70 C960,70 980,38 1060,38 C1130,38 1160,70 1200,70" stroke="#D946EF" stroke-width="1.5" fill="none" opacity="0.35" filter="url(#ag-glow-magenta)"/>
    <!-- Scan line -->
    <rect x="0" y="66" width="1200" height="8" fill="url(#ag-scan)"/>
    <!-- Intersection dots -->
    <circle cx="160"  cy="70" r="3"   fill="#00C8C0" opacity="0.8"/>
    <circle cx="640"  cy="98" r="2.5" fill="#8B5CF6" opacity="0.65"/>
    <circle cx="1060" cy="38" r="2.2" fill="#D946EF" opacity="0.55"/>
    <!-- Bottom border -->
    <rect x="0" y="137" width="1200" height="3" fill="#00C8C0" opacity="0.15"/>
    <!-- Left origin -->
    <line x1="0" y1="0" x2="0" y2="140" stroke="#00C8C0" stroke-width="2.5" opacity="0.25"/>
  </svg>
  <div class="hero-gain-overlay">GAIN</div>
  <div class="hero-signal-label">behavioral signal</div>
</div>

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
<!-- ── CTRL DOCK ── -->
<div class="ctrl-dock locked collapsed" id="ctrl-dock">
  <div class="ctrl-dock-hdr" onclick="handleDockHdrClick(event)">
    <div class="dock-title">Org Policy Control</div>
    <div class="dock-mode-tag" id="dock-mode-tag">BUILD</div>
    <div class="dock-midi-tag" id="dock-midi-tag">MIDI —</div>
    <button class="dock-lock-btn" id="dock-lock-btn" onclick="event.stopPropagation();toggleDockLock()">⚷ LOCKED</button>
    <button class="dock-collapse-btn" id="dock-collapse-btn" onclick="event.stopPropagation();toggleDock()">▲</button>
  </div>
  <div class="ctrl-dock-body">
    <div class="ag-ch t1">
      <div class="ag-ch-accent"></div>
      <div class="ag-ch-hdr">
        <div>
          <div class="ag-ch-lbl">Intensity</div>
          <div class="ag-ch-sub">Drive · Effort</div>
        </div>
        <div class="ag-ch-val" id="dock-val-intensity">0.60</div>
      </div>
      <div class="ag-fader-rail">
        <div class="ag-fader-track" id="dft-intensity">
          <div class="ag-fader-fill" id="dff-intensity"></div>
          <div class="ag-fader-thumb" id="dfth-intensity"></div>
        </div>
      </div>
    </div>
    <div class="ag-ch t2">
      <div class="ag-ch-accent"></div>
      <div class="ag-ch-hdr">
        <div>
          <div class="ag-ch-lbl">Depth</div>
          <div class="ag-ch-sub">Reasoning · Thinking</div>
        </div>
        <div class="ag-ch-val" id="dock-val-depth">0.50</div>
      </div>
      <div class="ag-fader-rail">
        <div class="ag-fader-track" id="dft-depth">
          <div class="ag-fader-fill" id="dff-depth"></div>
          <div class="ag-fader-thumb" id="dfth-depth"></div>
        </div>
      </div>
    </div>
    <div class="ag-ch t3">
      <div class="ag-ch-accent"></div>
      <div class="ag-ch-hdr">
        <div>
          <div class="ag-ch-lbl">Verbosity</div>
          <div class="ag-ch-sub">Room · Voice</div>
        </div>
        <div class="ag-ch-val" id="dock-val-room">0.40</div>
      </div>
      <div class="ag-fader-rail">
        <div class="ag-fader-track" id="dft-room">
          <div class="ag-fader-fill" id="dff-room"></div>
          <div class="ag-fader-thumb" id="dfth-room"></div>
        </div>
      </div>
    </div>
  </div>
</div>

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

<!-- ── FAQ PANEL ── -->
<div class="faq-overlay" id="faq-overlay" onclick="closeFaq()"></div>
<div class="faq-panel" id="faq-panel">
  <div class="faq-hd">
    <span class="faq-title">AiGAIN</span>
    <button class="faq-close" onclick="closeFaq()">✕</button>
  </div>
  <div class="faq-body">

    <div class="faq-s">
      <div class="faq-s-title">What is AiGain?</div>
      <p class="faq-p">AiGain is a <strong>behavioral control layer</strong> that sits between your organization and its AI usage. It controls how Claude thinks — not just what you ask it, but the cognitive mode it operates in when responding.</p>
      <p class="faq-p">Every employee using AI today gets the same default behavior regardless of their role, their task, or what the company actually needs. AiGain fixes that.</p>
    </div>

    <div class="faq-divider"></div>

    <div class="faq-s">
      <div class="faq-s-title">The Problem It Solves</div>
      <p class="faq-p">A support agent needs an AI that <strong>explores empathetically</strong> and asks clarifying questions. An engineer needs one that <strong>executes immediately</strong> and ships code. A researcher needs one that <strong>goes deep</strong> and surfaces trade-offs.</p>
      <p class="faq-p">Without AiGain, all three get the same model behavior. Tokens burn. Quality suffers. The company has no visibility and no control.</p>
    </div>

    <div class="faq-divider"></div>

    <div class="faq-s">
      <div class="faq-s-title">How It Works</div>
      <p class="faq-p">AiGain acts as a <strong>drop-in proxy</strong> for the Anthropic API. Your existing Claude integration points to AiGain instead of Anthropic directly. One line of code changes.</p>
      <code class="faq-code">
        # Before<br>
        base_url = "https://api.anthropic.com"<br><br>
        # After<br>
        base_url = "https://aigain-production.up.railway.app/v1"
      </code>
      <p class="faq-p">Every API call passes through AiGain. It looks up the team key, resolves the behavioral policy for that team, injects the right system prompt instructions, then forwards the request to Anthropic. The response comes back unchanged.</p>
    </div>

    <div class="faq-divider"></div>

    <div class="faq-s">
      <div class="faq-s-title">Behavioral Modes</div>
      <div class="faq-track" style="border-left-color:var(--accent);">
        <div class="faq-track-name"><span class="faq-tag build">BUILD</span>Execute mode</div>
        <div class="faq-track-desc">Claude picks the best approach and implements it immediately. No alternatives presented. No thinking out loud. Output only. Ideal for engineering teams shipping code.</div>
      </div>
      <div class="faq-track" style="border-left-color:var(--purple2);">
        <div class="faq-track-name"><span class="faq-tag explore">EXPLORE</span>Analysis mode</div>
        <div class="faq-track-desc">Claude covers multiple approaches, surfaces trade-offs, asks clarifying questions, and ends with decision points. Ideal for support, research, and strategy work.</div>
      </div>
    </div>

    <div class="faq-divider"></div>

    <div class="faq-s">
      <div class="faq-s-title">The Control Hierarchy</div>
      <p class="faq-p">Behavioral policy flows top-down, with each level able to override the one above:</p>
      <div class="faq-track" style="border-left-color:var(--amber);">
        <div class="faq-track-name" style="color:var(--amber);">Org Policy</div>
        <div class="faq-track-desc">Company-wide defaults. Every team starts here unless overridden. Set the floor and ceiling — min/max intensity, allowed modes, governance rules.</div>
      </div>
      <div class="faq-track" style="border-left-color:var(--green);">
        <div class="faq-track-name" style="color:var(--green);">Team Policy</div>
        <div class="faq-track-desc">Each team gets its own behavioral preset. Engineering gets BUILD. Support gets EXPLORE. Research gets deep depth and high verbosity. One API key per team.</div>
      </div>
      <div class="faq-track" style="border-left-color:var(--purple2);">
        <div class="faq-track-name" style="color:var(--purple2);">Individual Override</div>
        <div class="faq-track-desc">Senior engineers can unlock higher intensity. Specific roles can get tighter scope. Tracked and visible to admins.</div>
      </div>
    </div>

    <div class="faq-divider"></div>

    <div class="faq-s">
      <div class="faq-s-title">What Gets Tracked</div>
      <p class="faq-p"><strong>Token usage</strong> per team — see exactly where your AI budget is going.<br>
      <strong>Behavioral states</strong> — every policy change is logged with timestamp.<br>
      <strong>Active overrides</strong> — see which individuals are running outside team defaults.<br>
      <strong>Estimated savings</strong> — tokens saved by throttling teams that don't need max intensity.</p>
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
  updateDockFromOrg();
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

function openFaq(){ document.getElementById('faq-overlay').classList.add('open'); document.getElementById('faq-panel').classList.add('open'); }
function closeFaq(){ document.getElementById('faq-overlay').classList.remove('open'); document.getElementById('faq-panel').classList.remove('open'); }

function togglePolicyEdit(){
  const body = document.getElementById('org-controls');
  if(body) body.style.display = body.style.display==='none' ? '' : 'none';
}

loadOrg();
loadKeys();

// ── CTRL DOCK ────────────────────────────────────────────────────────────────

const DOCK_FIELDS = ['intensity', 'depth', 'room'];
const DOCK_CC = {0: 'intensity', 1: 'depth', 3: 'room'};
const THUMB_H = 4;
let dockLocked = true;
let dockCollapsed = true;
let dockDragging = new Set();
let dockSaveTimer = null;

function setDockFader(field, val) {
  val = Math.max(0, Math.min(1, val));
  const track = document.getElementById('dft-' + field);
  const fill  = document.getElementById('dff-' + field);
  const thumb = document.getElementById('dfth-' + field);
  const valEl = document.getElementById('dock-val-' + field);
  if (!track || !fill || !thumb) return;
  const h = track.offsetHeight || 1;
  const pct = val * 100;
  fill.style.height  = pct + '%';
  thumb.style.bottom = 'calc(' + pct + '% - ' + (THUMB_H/2) + 'px)';
  if (valEl) valEl.textContent = val.toFixed(2);
}

function updateDockFromOrg() {
  if (!window.ORG) return;
  const p = ORG.policy || {};
  DOCK_FIELDS.forEach(f => setDockFader(f, p[f] || 0));
  const modeEl = document.getElementById('dock-mode-tag');
  if (modeEl) modeEl.textContent = p.mode || 'BUILD';
}

function dockSave() {
  clearTimeout(dockSaveTimer);
  dockSaveTimer = setTimeout(() => {
    if (!window.ORG) return;
    patch({policy: ORG.policy});
    renderOrgControls();
    renderStats();
  }, 300);
}

function toggleDock() {
  dockCollapsed = !dockCollapsed;
  const dock = document.getElementById('ctrl-dock');
  const btn  = document.getElementById('dock-collapse-btn');
  dock.classList.toggle('collapsed', dockCollapsed);
  if (btn) btn.textContent = dockCollapsed ? '▲' : '▼';
  document.body.style.paddingBottom = dockCollapsed ? '44px' : '46vh';
  if (!dockCollapsed) {
    requestAnimationFrame(() => DOCK_FIELDS.forEach(f => {
      const p = (ORG && ORG.policy) ? ORG.policy : {};
      setDockFader(f, p[f] || 0);
    }));
  }
}

function handleDockHdrClick(e) {
  if (e.target.closest('.dock-lock-btn') || e.target.closest('.dock-collapse-btn')) return;
  toggleDock();
}

function toggleDockLock() {
  dockLocked = !dockLocked;
  const dock = document.getElementById('ctrl-dock');
  const btn  = document.getElementById('dock-lock-btn');
  dock.classList.toggle('locked', dockLocked);
  if (btn) { btn.textContent = dockLocked ? '⚷ LOCKED' : '⚷ UNLOCKED'; btn.classList.toggle('unlocked', !dockLocked); }
  if (!dockLocked && dockCollapsed) toggleDock();
}

// Pointer drag handlers
(function initDockFaders() {
  DOCK_FIELDS.forEach(field => {
    const track = document.getElementById('dft-' + field);
    if (!track) return;
    function onDown(e) {
      if (dockLocked) return;
      e.preventDefault(); e.stopPropagation();
      try { track.setPointerCapture(e.pointerId); } catch(_) {}
      dockDragging.add(field);
      track.classList.add('dragging');
      const p = (ORG && ORG.policy) ? ORG.policy : {};
      let cur = p[field] || 0;
      let prevY = e.clientY;
      function onMove(ev) {
        const r = Math.max(20, track.offsetHeight);
        const fine = ev.shiftKey ? 0.2 : 1;
        cur = Math.max(0, Math.min(1, cur + (-(ev.clientY - prevY) / r) * fine));
        prevY = ev.clientY;
        setDockFader(field, cur);
        if (ORG && ORG.policy) ORG.policy[field] = Math.round(cur * 1000) / 1000;
        dockSave();
      }
      function onUp() {
        dockDragging.delete(field);
        track.classList.remove('dragging');
        track.removeEventListener('pointermove', onMove);
        track.removeEventListener('pointerup', onUp);
        track.removeEventListener('pointercancel', onUp);
      }
      track.addEventListener('pointermove', onMove);
      track.addEventListener('pointerup', onUp);
      track.addEventListener('pointercancel', onUp);
    }
    track.addEventListener('pointerdown', onDown);
    track.addEventListener('dblclick', () => {
      if (dockLocked) return;
      if (ORG && ORG.policy) ORG.policy[field] = 0.5;
      setDockFader(field, 0.5);
      dockSave();
    });
  });
})();

// WebMidi
(function initMidi() {
  if (!navigator.requestMIDIAccess) return;
  navigator.requestMIDIAccess().then(midi => {
    const tag = document.getElementById('dock-midi-tag');
    function connectInputs() {
      let connected = false;
      for (const input of midi.inputs.values()) {
        connected = true;
        input.onmidimessage = function(e) {
          const [status, cc, rawVal] = e.data;
          if ((status & 0xF0) !== 0xB0) return;
          const field = DOCK_CC[cc];
          if (!field) return;
          const val = rawVal / 127;
          if (ORG && ORG.policy) ORG.policy[field] = Math.round(val * 1000) / 1000;
          setDockFader(field, val);
          dockSave();
        };
      }
      if (tag) { tag.textContent = connected ? 'MIDI ●' : 'MIDI —'; tag.classList.toggle('active', connected); }
    }
    connectInputs();
    midi.onstatechange = connectInputs;
  }).catch(() => {});
})();

// Body padding so content doesn't hide behind dock
document.body.style.paddingBottom = '44px';
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
