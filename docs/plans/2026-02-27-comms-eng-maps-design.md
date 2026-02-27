# Design: Comms Cleanup, Human Comms, Spectator Eng Board, Lobby Map Editor

Date: 2026-02-27

## 1. Comms Message Cleanup

### Problem
Bot messages are noisy: emojis, verbose text, long bot names, too many messages that duplicate game events.

### Changes
- Strip ALL emojis from bot_chat `msg` field in server.py
- Display role as short tag: `[RO]`, `[CAP]`, `[FM]`, `[ENG]` instead of full bot name
- Condense message text to essential info (e.g. "Tracking 206 positions, ~4 sectors")
- Remove `_emit_ro_bot_commentary()` at turn start (duplicates pre-turn report)
- Remove public bot_chat messages that duplicate game events already in the log:
  - Move direction (already `direction_announced`)
  - Dive (already `dive_announced`)
  - Surface (already `surface_announced`)
  - Torpedo fire (already `torpedo_fired`)
  - Drone result (already `drone_result`)
  - Sonar activation (already `sonar_announced`)
  - Sonar response (already `sonar_result`)
- Keep ONLY team-internal bot_chat: RO reports, FM charges, engineer marks, placement

### Files
- `server.py`: Remove/simplify bot_chat emissions, strip emojis from msg strings
- `static/js/spectator.js`: Update bot_chat handler to use short role tags
- `static/js/captain.js`, `engineer.js`, `first_mate.js`, `radio_operator.js`: Same handler update

## 2. Human Player Quick Action Comms

### Problem
Human players cannot communicate with bot teammates. No input mechanism exists.

### Design
Role-specific quick action buttons above the comms log in each player view.

**Captain:**
- "Request position report" -> RO generates fresh report
- "Priority: [system]" -> cycles through torpedo/mine/sonar/drone/stealth, sets FM charge priority
- "Protect: [system]" -> tells Engineer which system to keep available

**Radio Operator:**
- "Report positions" -> sends current analysis to Captain
- "High confidence" / "Low confidence" -> status indicator

**First Mate:**
- "Status report" -> sends current system charges to Captain

**Engineer:**
- "Recommend directions" -> sends direction analysis to Captain

### Implementation
- New `player_comms` SocketIO event from client to server
- Server routes through TeamComms to appropriate bot inbox
- Bot processes on next turn cycle
- Message appears in team's comms log via `_emit_bot_chat_team_only`

### Files
- `server.py`: New `on_player_comms` handler, routes to TeamComms
- `comms.py`: Add `post_from_human(role, msg_type, data)` method
- `bots.py`: Each bot's `process_inbox()` handles human commands
- `templates/captain.html`, `radio_operator.html`, `first_mate.html`, `engineer.html`: Add button HTML
- `static/js/captain.js`, etc.: Add button click handlers emitting `player_comms`

## 3. Spectator Engineering Board Redesign

### Problem
Spectator uses simplified 4-column horizontal layout with 15px dots. Engineer view uses rich 2x2 grid with 42px nodes, circuit badges, reactor dividers, SVG traces. They look nothing alike.

### Design
Replace spectator `renderEngBoard()` with mini 2x2 layout matching engineer view:
- 2x2 grid: W/N top row, S/E bottom row
- ~22px nodes (scaled down from 42px to fit 220px panel)
- 3 circuit nodes (with C1/C2/C3 badges) + divider + 3 extra nodes per direction
- Radiation symbol on reactor nodes
- Same color scheme and marked state glow
- SVG circuit traces connecting across sections

### Files
- `static/js/spectator.js`: Rewrite `renderEngBoard()` to generate 2x2 structure
- `static/css/spectator.css`: Add `.mini-eng-2x2` styles (scaled-down engineer.css)

## 4. Lobby Map Editor

### Problem
Hardcoded 15x15 map with sector_size=8 gives unequal sectors (8+7). No customization.

### Design
Host-only map editor in lobby with live preview. Port from reference at `D:\OneDrive\1Documents\4Websites\Games\CaptainSonarWebsite`.

**UI Controls (host-only):**
- Rows slider: 5-30 (default 15)
- Cols slider: 5-30 (default 15)
- Sector Width slider: 2-10 (default 5)
- Sector Height slider: 2-10 (default 5)
- Island Count slider: 1 to 10% of map area
- Max Island Size slider: 1-5 (default 2)
- "Shuffle Islands" button

**Sector equality:** Sliders auto-constrain dimensions to be divisible by sector size.

**Live preview:** Canvas showing grid lines, sector boundaries with labels, island positions.

**Island generation algorithm (from reference):**
- Random positions avoiding edges
- Variable sizes (15% chance of larger, 40% hollow corners for variety)
- No overlaps

**Backend:**
- `maps.py`: New `generate_map()` function replacing hardcoded map
- Server receives map settings via `map_settings` event, stores in game dict
- `on_start_game`: Generates map from settings instead of loading preset
- All views already receive map dimensions via template variables

### Files
- `maps.py`: Add `generate_map()`, `generate_islands()`, keep `get_sector()` / `get_col_labels()`
- `server.py`: Handle `map_settings` event, use generated map at game start
- `templates/lobby.html`: Add map editor controls and preview canvas
- `static/js/lobby.js`: Slider handlers, preview rendering, island generation, validation
- `static/css/lobby.css`: Map editor styles
