# PokerLab

**PokerLab** is a poker platform that combines interactive play, game-playing AI, probability and game theory.
The first release, **v0.1**, is a fully playable game of **heads-up No-Limit Texas Hold'em** against a computer opponent. It runs on a rules-accurate Python engine behind a React interface.

> Virtual chips only. PokerLab does not involve real-money gambling.

![PokerLab table](docs/screenshot.jpg)

## Features (v0.1)

**Game engine**
- Standard 52-card deck with seeded Fisher–Yates shuffling. Cards are dealt one at a time from the left of the button, with burn cards.
- Full No-Limit betting: fold, check, call, bet, raise and all-in.
- Correct heads-up positions: the button posts the small blind and acts first preflop, then last on every later street.
- Minimum-raise rules. An all-in for less than a full raise does **not** reopen betting for players who have already acted.
- Raises are capped at what an opponent can actually call. Uncalled bets are returned.
- Main and side pots, split pots, and odd-chip distribution.
- Edge cases: short all-in blinds, all-in runouts, and automatic board runout once no further betting is possible.
- Fast 5–7 card hand evaluator (no brute-force combinations), cross-checked against brute force in the tests.
- Each hand emits an event stream (blinds, actions, streets, reveal, showdown, awards), which the UI animates.

**Computer opponent**
- Rule-based agent: Chen-formula preflop strength, a made-hand and draw heuristic postflop, pot odds, value bets, occasional slow-plays and bluffs.
- Every decision passes through a legality guard. The tests play thousands of hands to confirm the agent never makes an illegal move.

**Interface**
- Felt table with animated dealing, card flips, chip stacks, a dealer button, action bubbles and a turn indicator. The opponent shows a "thinking" pause before it acts.
- Betting controls: pot-fraction presets, slider, numeric input, ± big-blind nudges, and keyboard shortcuts (`F` / `C` / `R`, `Space` for the next hand).
- Live hand history, your current made hand ("Two Pair, Aces and Fours"), showdown reveals and winner banners.
- Automatic progression to the next hand with a countdown. You can turn it off and choose an animation speed.
- Game-over screen when a player busts, plus restart and new-game setup (name, stack depth, blinds).
- Responsive layout from phone to desktop, in a dark theme.

**v0.2.1 — Visual redesign**
- Redesigned cards with vector suit glyphs (no emoji or font substitution), framed court cards and a bordered card back.
- Casino-style chips with edge inserts, stacked in columns for larger pots.
- Seats rebuilt as nameplates over fanned hole cards. The action tag and made hand sit in a tag row under each nameplate.
- Textured felt with a betting line, a brass rail inlay, and a portrait-friendly table shape on phones.
- Hand history shows board and shown cards as mini cards.
- Bundled Inter and Playfair Display fonts, a unified dark surface system, and a reworked betting dock.

**v0.2.2 — Animations and interactions**
- Cards are dealt one at a time from a deck on the felt. Your cards land face down and turn over, and the flop lands face down before turning left to right.
- Chips fly from stacks to bets, from bets into the pot, back to the owner when a bet is uncalled, and from the pot to the winner. Stack counts roll to their new value.
- Each action has its own feedback: check, call, bet/raise, all-in and fold.
- At showdown the opponent's cards flip, the five cards that make the winning hand light up, and the rest are muted.
- Cards are collected between hands, and stale action tags clear when a betting round ends.
- Animation speed follows the speed setting. Instant speed and the OS "reduce motion" preference skip movement.

**v0.2.3 — Sound effects and customization**
- Subtle sounds for dealing, card turns, chips, checks, bets and raises, all-ins, folds, collecting bets and pushing the pot. Each sound is timed to its animation.
- Every sound is synthesised in the browser with the Web Audio API, so there are no audio files or licences involved. Small random variations and rate limits keep repeats from sounding mechanical or piling up.
- Audio starts only after your first click, tap or key press, as browsers require.
- Mute button in the header, plus a volume slider (with a preview sample) in the settings menu.
- Three table themes: classic green, midnight blue and burgundy.
- Sound, volume, theme, animation speed and auto-deal are saved in the browser and survive a refresh. Invalid saved values fall back to the defaults.

**v0.2.4 — Stability, accessibility and UX**
- Keyboard shortcuts no longer fire while you type in a field or while a dialog is open, and auto-deal waits while a dialog covers the table.
- If a request fails, the table re-syncs with the server. A lost game (for example after a server restart) offers a new game, and an unreachable server offers Retry.
- API hardening: per-session locking, client seeds disabled by default (`POKERLAB_ALLOW_SEED`), idle games expire, and finished games are evicted before live ones.
- The whole game can be played from the keyboard. Dialogs trap and restore focus, focus returns to the betting controls each turn, and every control shows a visible focus ring.
- Screen readers: your turn and options are announced, and cards in the hand history, stacks, bets and the dealer button all have text.
- Touch: larger tap targets on phones, no zoom when tapping inputs or tapping buttons quickly, and higher-contrast small text.

## Quick start

Requirements: **Python 3.11+** and **Node.js 20.19+** (CI uses Node 24).

```bash
make install   # creates backend/.venv and installs frontend packages
make dev       # API on :8000 and UI on :5173
```

Then open <http://localhost:5173>.

<details>
<summary>Without make</summary>

```bash
# backend
cd backend
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pokerlab          # http://127.0.0.1:8000

# frontend (second terminal)
cd frontend
npm install
npm run dev                            # http://localhost:5173 (proxies /api to :8000)
```
</details>

Configuration is optional. See [`.env.example`](.env.example).

## Testing

```bash
make test      # pytest + vitest
make lint      # ruff + TypeScript
```

- **Backend (pytest, 100+ tests):** deck and evaluator correctness, betting rules, illegal actions, side pots and odd chips, short-stack edge cases, AI legality, sessions, API robustness (locking, seeds, eviction), and full games played over HTTP. Randomized fuzz tests play thousands of 2–6 player hands and check chip conservation after every action.
- **Frontend (Vitest + Testing Library):** betting controls, seats and hidden cards, sizing helpers, animations, accessibility (focus, dialogs, announcements), connection failures and keyboard shortcuts, the sound engine (against a fake AudioContext) and event-to-sound timing, saved settings, and app flows with a mocked API (deal → act → showdown → next hand, errors, game over).
- **CI:** GitHub Actions runs lint, tests and a production build on every push and pull request (Python 3.11–3.13).

## Architecture

```
backend/pokerlab/
├── cards.py          Card, Rank, Suit, Deck            ← shared by every variant
├── evaluator.py      5–7 card hand ranking             ← shared by every variant
├── engine/
│   ├── actions.py    Action, LegalActions, errors      ← generic betting vocabulary
│   ├── pots.py       side-pot layering, pot splitting  ← generic
│   └── holdem.py     HoldemHand state machine + events ← variant-specific
├── ai/
│   ├── base.py       Agent interface, Observation (no hidden info), legality guard
│   ├── strength.py   preflop/postflop strength heuristics
│   ├── heuristic.py  basic rule-based opponent
│   └── random_agent.py
├── session.py        heads-up match: hand sequencing, AI turns, per-seat views, log
└── api/              FastAPI app, schemas, session store

frontend/src/
├── hooks/usePokerGame.ts   API calls + step-by-step animation playback
├── hooks/useGameSounds.ts  plays table sounds from the same event stream
├── components/             Table, Seat, PlayingCard, ChipStack, ChipFlights, ActionBar, HandLog, SettingsMenu, modals
└── lib/                    API client, betting and card helpers, motion timing, sound engine and cues
```

**How a turn works.** The server holds all game state. Each request (`POST /api/sessions/{id}/actions`) applies the human's action, then lets the AI act until it is the human's turn again or the hand ends. The response contains the final state plus a list of `steps`. Each step is an engine event paired with a state snapshot taken right after it, so the UI can replay the AI's moves, new streets and showdowns one at a time. Opponent hole cards are left out of every snapshot until they are shown down.

**Extensibility.**
- New variants reuse cards, the evaluator, actions and pot logic, and add their own hand state machine.
- New AI strategies implement `Agent.decide(observation, legal_actions)`.
- `HoldemHand` already supports more than two players, ready for multi-way tables.

### API

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/health` | Health check |
| `POST` | `/api/sessions` | Create a match (`starting_stack`, `small_blind`, `big_blind`, `player_name`; `seed` only when the server sets `POKERLAB_ALLOW_SEED`) |
| `GET` | `/api/sessions/{id}` | Current state (from the human's point of view) |
| `POST` | `/api/sessions/{id}/hands` | Deal the next hand |
| `POST` | `/api/sessions/{id}/actions` | `{"action": "fold"\|"check"\|"call"\|"bet"\|"raise"\|"all_in", "amount"?: raise-to total}` |
| `DELETE` | `/api/sessions/{id}` | End a match |

Each response includes `steps`: one event plus a state snapshot for every change, which the UI replays to drive its animations. Showdown events list each player's best five cards (`best`), and `pot_awarded` events list the winning cards (`cards`).

Illegal actions return `400` with an explanation. Out-of-turn or out-of-state requests return `409`. Interactive docs are served at `/docs`.

## Roadmap

- **v0.2 — Visual polish:** v0.2.1 visual redesign, v0.2.2 animations, v0.2.3 sound and themes, and v0.2.4 stability and accessibility (done).
- **v0.3 — Smarter AI:** Monte Carlo equity, better bet sizing, difficulty levels, basic opponent modeling.
- **v0.4 — Analytics:** persistent hand history (SQLite), replays, performance stats, AI-vs-AI simulations.
- **Later:** multiplayer, tournaments, Omaha / Stud / Draw, CFR agents, reinforcement learning, strategy dashboards.

## License

MIT
