# aigain — Re-Entry File
*Claude: read this before touching anything.*

---

## What This Is
Enterprise behavioral control layer for AI — team, sub-team, and individual throttles built on Gain

## Re-Entry Phrase
> "Re-entry: aigain"

## Current Status
🔨 Just created — ready to build

## Stack
- Python + Flask, port 5571, host 127.0.0.1
- Dark theme, Inter font, CSS variables
- Logo at /static/logo.png

## File Structure
```
aigain/
├── app.py
├── templates/index.html
├── static/
├── data/
├── requirements.txt
├── Makefile
├── launch.command
├── .env
└── .env.example
```

## How to Run
```bash
cd ~/aigain && make run
```

## GitHub
- Repo: papjamzzz/aigain
- Push: `make m="your message" push`

## What's Done
- [x] Project scaffold + full Flask app built
- [x] Three-tier hierarchy: Org → Team → Individual
- [x] Dashboard with usage stats, team cards, members table, policy limits
- [x] Persistent JSON storage (data/org.json)
- [x] GitHub repo live: papjamzzz/aigain

## What's Next
- [ ] Real API routing — behavioral state applied to actual Claude calls
- [ ] Auth layer (admin vs team lead vs member)
- [ ] Usage tracking per team/member (real token counts)
- [ ] Railway deployment
- [ ] Stripe billing

## Key Technical Decisions
- Single-file Flask app (HTML as string, like Gain)
- Data stored in data/org.json — simple, no DB needed for MVP
- Built on Gain's behavioral state model — same 4 parameters

## Last Session
2026-06-04 — Initial build. Full enterprise UI: org/team/individual throttle hierarchy, dashboard, teams, members, policy pages. Dark theme, Gain aesthetic. Persistent JSON. GitHub live.

---
*Last updated: 2026-06-04*
