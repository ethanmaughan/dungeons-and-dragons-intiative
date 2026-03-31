# Foray — Game Design Document

## What is Foray?

Foray is a web-based turn-based RPG powered by a fully autonomous AI Game Master. Players create characters, join campaigns, and play through adventures entirely in the browser — no human GM required.

All art, maps, and campaign stories are human-created. The AI executes them live, responding to player actions in real-time. Think of the AI as a stage director: the playwright wrote the script, the artist built the sets — the AI performs it for a live audience.

## Core Design Philosophy

1. **Illusion of control** — Players feel they drive the story, but the AI always steers toward a goal, a twist, or a revelation.
2. **Consistent narrative structure** — Every campaign has a call to adventure, an antagonist, and an end goal. Always.
3. **Keep it simple** — If you can't explain a mechanic in one sentence, cut it.
4. **Human-made, AI-enhanced** — All art, maps, and stories are created by humans. The AI runs the game, it does not generate content.

## Creative Principles

- No AI-generated art. All maps, portraits, and scene illustrations are drawn or commissioned by humans.
- No AI-generated stories. All campaigns, NPCs, and dialogue frameworks are written by humans.
- Players upload their own character portraits.
- The AI stays within defined parameters for each area and story — like parameters for code.

## Game Mechanics

### Dice System
- **d20 + modifier** for all checks (ability checks, saving throws, attack rolls)
- Familiar, proven, easy to understand

### Leveling
- **Levels 1–20** with class features
- Classic XP-based progression
- No skill trees, no meta-currencies — just straightforward leveling

### Reputation System
- Invisible to the player — no "Reputation: 47" bar
- AI tracks how NPCs, factions, and towns perceive you based on your actions
- Players discover their reputation through world reactions: guards let you pass, merchants give discounts, NPCs refuse to talk
- Actions from session 1 ripple into session 10

### Adaptive Antagonist
- The villain is not static — they respond to player strategy
- Players snuck in quietly? The villain tightens security
- Players recruited allies? The villain targets them
- The story pushes back intelligently

### Persistent World Memory
- The AI never forgets
- NPCs remember past conversations and promises
- Consequences carry across sessions
- No other tabletop system can do this consistently

## What Makes Foray Unique

1. **No GM required** — Removes the #1 barrier to playing tabletop RPGs
2. **The world remembers everything** — AI-tracked reputation and long-term consequences
3. **The antagonist adapts** — Villains respond to player behavior dynamically
4. **Authored content, autonomously delivered** — Human stories + AI execution
5. **Visual maps with real-time token movement** — Hand-drawn maps with player tokens for combat and exploration
6. **Human-made art and stories** — In a sea of AI-generated content, Foray is authored with creative integrity

## The Pitch

> Foray is the first RPG where the world remembers, the villain adapts, and you never need a GM.

## Platform

- Web-based (works in any browser, any device)
- Future: possible native app or game client if the concept validates

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python + FastAPI |
| Database | SQLite + SQLAlchemy |
| Frontend | HTML + Jinja2 + HTMX + Tailwind CSS |
| AI | Claude API (production) / Ollama (local development) |
| Real-time | FastAPI WebSockets (for multiplayer) |

## Roadmap

### Completed
- [x] AI Game Master with narrative responses
- [x] Player accounts (login/register)
- [x] Campaign creation
- [x] In-game character creation (GM-guided + quick form)
- [x] Server-side dice rolling engine
- [x] Action tag system (GM embeds mechanical tags, server resolves them)
- [x] Combat system with initiative and monster stat blocks
- [x] Character profiles with avatar upload
- [x] Text-to-speech narration
- [x] Sidebar with character sheet, party status, navigation

### Next
- [ ] Deploy to web (hosting + domain: foraygames.com)
- [x] Rebrand UI to "Foray"
- [x] Remove official campaign references
- [ ] Multiplayer (WebSockets, shared sessions)
- [ ] Subscription/payment system
- [ ] Visual battle map with token movement
- [ ] Original campaign content
- [ ] Reputation system implementation
- [ ] Adaptive antagonist system
- [ ] Sound effects and ambient music
- [ ] Mobile-responsive design

### Future — Community Marketplace
- [ ] **The Foray Workshop** — a marketplace where creators upload and share content
- [ ] Campaigns — community-written adventures with full story parameters
- [ ] Maps — hand-drawn battle maps and world maps
- [ ] Sprites/Tokens — character and monster art for the battle map
- [ ] Music/Audio — ambient tracks, sound effects, battle themes
- [ ] Rating/review system for uploaded content
- [ ] Creator profiles and attribution
- [ ] Free + paid content (creators can set a price, Foray takes a small cut)

## Legal

This product uses game mechanics from the System Reference Document (SRD 5.2), available under the Creative Commons Attribution 4.0 International License (CC-BY-4.0).

Attribution: This product includes content from the SRD 5.2, used under the Creative Commons Attribution 4.0 License.

## Contributing

This is a private project. Contact the repository owner for access.
