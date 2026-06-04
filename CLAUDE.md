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
- [x] Project scaffold created

## What's Next
- [ ] Define core functionality
- [ ] Add logo to static/
- [ ] Wire up first route/feature

## Key Technical Decisions
- localhost only (host=127.0.0.1)

---
*Last updated: 2026-06-04*
