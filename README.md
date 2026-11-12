---
title: Escape Silk Board
emoji: 🛺
colorFrom: indigo
colorTo: amber
sdk: docker
app_port: 7860
pinned: false
---

# Escape Silk Board · FL 2047

A single-file AI text adventure set on Bengaluru's Outer Ring Road. You have ₹500
cash, 4% phone battery, one cold samosa, and 115 minutes to get from Silk Board
to Kempegowda International Airport before check-in closes at 8:55 PM.

The FastAPI backend owns the world state and every guardrail. The frontend is a
single static HTML file, so there is no build step.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Open <http://127.0.0.1:8000>. You can also run:

```bash
.venv/bin/uvicorn main:app --reload
```

## Deploy (free)

### Hugging Face Spaces — recommended

The repo already has a `Dockerfile` and the Space frontmatter in this README.

1. Create a **Docker** Space at huggingface.co/new-space.
2. Add these under **Settings → Variables and secrets**:
   `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` (`deepseek-chat`),
   `LLM_MAX_TOKENS=2000`, `LLM_JSON_MODE=1`, `STATE_SECRET` (any long random string).
3. Push this folder to the Space repo. The container serves on port 7860.
4. Health check: `https://<user>-<space>.hf.space/api/health` → `"llm_mode":"llm"`.

Spaces only sleep after 48h of inactivity, so judges usually get a warm instance.

### Render — fastest

- Build: `pip install -r requirements.txt`
- Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Same environment variables as above.
- The free tier sleeps after 15 minutes; ping it every 10 minutes with a free
  uptime monitor if judges may arrive cold.

### Public-endpoint guards

| Variable | Default | Purpose |
| --- | --- | --- |
| `TURN_RATE_LIMIT` | `30` | `/api/turn` requests per minute per IP |
| `START_RATE_LIMIT` | `15` | `/api/start` requests per minute per IP |

Set either to `0` to disable. The limiter reads `X-Forwarded-For` behind a proxy.

## Test

```bash
.venv/bin/python -m pytest -q
```

The tests cover the offline intent engine, cash/battery/time guardrails, the
12-move cap, HMAC tamper detection, API recovery, and end-to-end start/turn.

## Game rules

- The route has 8 legs (9 checkpoints), from Silk Board to BLR Terminal 1.
- A run ends after **12 moves** or when the clock reaches **115 minutes**.
- One move advances at most **2 legs**. A two-leg jump needs a samosa trade or a
  ₹120+ fare; otherwise progress is cut to one leg.
- Cash is hard-capped at ₹500. A fare above the remaining balance is blocked.
- A quoted fare is not a transaction: money only moves when you name a price or
  tell the driver to go. Counter-offers require your acceptance.
- Phone actions (UPI, cab apps, calls, maps) fail at 0% battery; phone turns
  drain at least 2%, passive turns at most 1%.
- Walking is free but slow; buses are cheap; cabs cost ₹120 for two legs;
  autos open at ₹650 and only move for a serious counter-offer.
- The cold samosa is a one-time two-leg bribe (or a one-leg meal).
- Rohit's shortcut works exactly once.
- Obvious prompt injection or teleportation costs 12 minutes and zero progress.

## Event deck (replayability)

Each run draws deterministic obstacles from `EVENTS` in `main.py`: a wedding
procession, a cloudburst, a fuel strike, a police check that bans pillion rides,
a VIP convoy, a BMTC breakdown, a toll queue, or runway VIP movement. The draw
is seeded by `session_id + turn`, so a run is reproducible and cannot be
re-rolled. The engine applies the effect (extra minutes, a progress cap) and the
narrator weaves the obstacle into the scene; the player sees it in the **⚡ EVENT**
banner before typing. Set `DISABLE_EVENTS=1` to turn the deck off.

## Signature: the shareable pass

The end screen computes a score (time margin, cash, battery, samosa, legs) and a
grade (A+…F), stores a personal best in `localStorage`, and renders the result to
a **1200×630 PNG** — a boarding pass for a win, a "cause of missed flight"
certificate for a loss — with the route trace and run stats. Download, native
share (Web Share API), or copy the text receipt.

## LLM is optional

With no keys configured the game uses a deterministic rules-based narrator, so
it works fully offline. To use an LLM, set one of:

```bash
export GROQ_API_KEY=...
export GEMINI_API_KEY=...
export OPENAI_API_KEY=...
```

Or point at any OpenAI-compatible local server:

```bash
export LLM_BASE_URL=http://127.0.0.1:11434/v1
export LLM_MODEL=your-model
export LLM_API_KEY=optional
```

Useful switches:

| Variable | Default | Purpose |
| --- | --- | --- |
| `STATE_SECRET` | random per process | Set a stable secret if HMAC recovery must survive restarts. |
| `DISABLE_LLM` | unset | Set to `1` to force the offline narrator. |
| `LLM_MODEL` | provider default | Model name for the chosen provider. |
| `LLM_MAX_TOKENS` | `500` | Narrator response cap. |
| `LLM_JSON_MODE` | `1` | Set to `0` if the provider rejects `response_format`. |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Bind address for `python main.py`. |

## Files

- `main.py` — FastAPI app, game engine, HMAC state signing, optional LLM caller.
- `static/index.html` — the complete UI (Tailwind CDN, split-flap SFX, no build).
- `test_guardrails.py` — deterministic anti-cheat/guardrail tests.
- `test_game.py` — intent-engine and API round-trip tests.
