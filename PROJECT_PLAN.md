# Captain Sonar Web Game — Project Plan

**Goal:** A complete, polished, 8-player turn-based Captain Sonar game in the browser.
**Stack:** Python / Flask + Flask-SocketIO (backend) · Vanilla JS + CSS (frontend, no build step)
**Destination:** `D:\OneDrive\1Documents\Claude Code\captain-sonar\`
**Repo:** New GitHub repo pushed via stored PAT

---

## STATUS KEY
- ✅ Done
- 🔄 In progress
- ⬜ Not started
- ❌ Blocked

---

## Phase 0 — Scaffolding
| # | Task | Status | Notes |
|---|------|--------|-------|
| 0.1 | Create directory structure | ✅ | `templates/`, `static/css/`, `static/js/` |
| 0.2 | `requirements.txt` | ✅ | flask, flask-socketio, eventlet |
| 0.3 | `.gitignore` | ✅ | |
| 0.4 | `maps.py` | ✅ | Map Alpha 15×15, `get_sector()`, `get_col_labels()` |
| 0.5 | `game_state.py` | ✅ | Full game logic: placement, movement, weapons, engineering, turns |

---

## Phase 1 — Backend Server
| # | Task | Status | Notes |
|---|------|--------|-------|
| 1.1 | `server.py` — HTTP routes | ⬜ | `/` index · `/lobby` · `/play` |
| 1.2 | `server.py` — Lobby socket events | ⬜ | join, set_team, set_role, ready, start |
| 1.3 | `server.py` — Placement socket events | ⬜ | place_sub |
| 1.4 | `server.py` — Captain socket events | ⬜ | move, surface, dive, fire_torpedo, place_mine, detonate_mine, sonar, drone, stealth, end_turn |
| 1.5 | `server.py` — Engineer socket events | ⬜ | mark_node |
| 1.6 | `server.py` — First Mate socket events | ⬜ | charge_system |
| 1.7 | `server.py` — Broadcast helpers | ⬜ | emit to role, team, opponent |
| 1.8 | `server.py` — Input validation | ⬜ | All inputs sanitised before hitting game_state |

---

## Phase 2 — HTML Templates
| # | File | Status | Key Elements |
|---|------|--------|-------------|
| 2.1 | `templates/index.html` | ⬜ | Name input · Create Game / Join Game · Game code input |
| 2.2 | `templates/lobby.html` | ⬜ | Player list · Team picker · Role picker · Ready button · Start button (host) |
| 2.3 | `templates/captain.html` | ⬜ | 15×15 map grid · Compass movement · Weapon buttons · System charge display · Notes area |
| 2.4 | `templates/first_mate.html` | ⬜ | 6 system panels with charge dots · Activate buttons · Health tracker |
| 2.5 | `templates/engineer.html` | ⬜ | 4-section circuit board · Clickable nodes · Circuit connection lines · Radiation warning |
| 2.6 | `templates/radio_operator.html` | ⬜ | 15×15 tracking grid · Draw/erase toolbar · Notes |

---

## Phase 3 — CSS (Dark Naval Theme)
| # | File | Status | Key Rules |
|---|------|--------|-----------|
| 3.1 | `static/css/base.css` | ⬜ | CSS vars (navy, blue team, red team) · Reset · Fonts · Buttons · Overlays |
| 3.2 | `static/css/captain.css` | ⬜ | Map grid · Dot styles · Trail lines · Sub marker · Mine/torpedo highlights |
| 3.3 | `static/css/first_mate.css` | ⬜ | System panels · Charge dots (empty / filling / full) · Color coding R/G/Y |
| 3.4 | `static/css/engineer.css` | ⬜ | Circuit board layout · Node circles · Color coding · Marked state · Direction labels |
| 3.5 | `static/css/radio_operator.css` | ⬜ | Tracking grid · Draw overlay · Toolbar |

---

## Phase 4 — JavaScript
| # | File | Status | Key Functions |
|---|------|--------|---------------|
| 4.1 | `static/js/socket_client.js` | ⬜ | Shared socket init · `joinRoom()` · Generic event helpers |
| 4.2 | `static/js/captain.js` | ⬜ | Map render · Placement mode · Move buttons · Trail drawing · Mine/torpedo targeting · Stealth moves · Sonar/Drone modal · Turn lock/unlock |
| 4.3 | `static/js/first_mate.js` | ⬜ | Charge dot rendering · System status display · Activate buttons · Health display |
| 4.4 | `static/js/engineer.js` | ⬜ | Board render · Node click handler · Direction highlight on captain move · Circuit animation · Damage alerts |
| 4.5 | `static/js/radio_operator.js` | ⬜ | SVG draw layer · Freehand drawing · Log announced directions |

---

## Phase 5 — Integration & Visual Check
| # | Task | Status | Notes |
|---|------|--------|-------|
| 5.1 | Install deps in venv | ⬜ | `pip install -r requirements.txt` |
| 5.2 | Start server | ⬜ | `python server.py` |
| 5.3 | Open all 8 role tabs in browser | ⬜ | Use preview tool |
| 5.4 | Visual check — lobby | ⬜ | All 8 players join, assign roles, start |
| 5.5 | Visual check — placement | ⬜ | Both captains place subs |
| 5.6 | Visual check — game turn | ⬜ | Move → engineer marks → FM charges → use weapon → end turn |
| 5.7 | Fix any obvious bugs | ⬜ | |

---

## Phase 6 — GitHub
| # | Task | Status | Notes |
|---|------|--------|-------|
| 6.1 | `git init` | ⬜ | |
| 6.2 | Create repo via GitHub API | ⬜ | Name: `captain-sonar` |
| 6.3 | Push initial commit | ⬜ | |

---

## Phase 7 — Polish & Bug Fixes
| # | Task | Status | Notes |
|---|------|--------|-------|
| 7.1 | Responsive layout | ⬜ | Works on iPad-size screens (player tablets) |
| 7.2 | Game log / event feed | ⬜ | Show last N events in sidebar |
| 7.3 | Reconnect handling | ⬜ | Player can rejoin if disconnected |
| 7.4 | Game over screen | ⬜ | Winner banner |
| 7.5 | Sound/vibration hints | ⬜ | Optional, low priority |

---

## Socket Event Contract

### Client → Server
```
join_room         {game_id}
lobby_join        {game_id, name}
set_team          {game_id, name, team}
set_role          {game_id, name, role}
player_ready      {game_id, name, ready}
start_game        {game_id}

place_sub         {game_id, row, col}

captain_move      {game_id, direction}
captain_surface   {game_id}
captain_dive      {game_id}
captain_torpedo   {game_id, row, col}
captain_mine_place {game_id, row, col}
captain_mine_det  {game_id, mine_index}
captain_sonar     {game_id, ask_row, ask_col, ask_sector}
captain_drone     {game_id, sector}
captain_stealth   {game_id, moves:[directions]}
captain_end_turn  {game_id}

engineer_mark     {game_id, direction, index}
first_mate_charge {game_id, system}
```

### Server → Client (targeted by room/team/role/sid)
```
lobby_state       {players:[{name,team,role,ready}], host, game_id}
game_started      {map, turn_order}
placement_start   {}
sub_placed        {team}                          → all
game_phase        {current_team}                  → all
turn_start        {team}                           → all

direction_announced {team, direction}             → all (radio ops use this)
moved_self        {row, col, trail}               → own captain
sub_placed_ack    {row, col}

torpedo_fired     {team, row, col}                → all
explosion         {row, col, hits:[{team,dmg,health}]}  → all
mine_placed_ack   {}                              → own captain
mine_detonated    {team, row, col}                → all
sonar_result      {row_match, col_match, sector_match}  → own captain only
drone_result      {in_sector}                     → own captain only
sonar_announced   {team}                          → all
drone_announced   {team, sector}                  → all
stealth_announced {team, steps}                   → all (enemy hears steps but not direction)

engineer_update   {team, board}                   → own team engineer
first_mate_update {team, systems}                 → own team first mate
engineering_damage {team, cause, damage, health}  → all
charge_update     {team, system, charge, max}     → own captain + first mate

damage            {team, amount, health, cause}   → all
surface_announced {team, sector}                  → all
game_over         {winner, loser}                 → all
error             {msg}                           → sender only
```

---

## Turn Flow (Turn-Based)

```
ACTIVE TEAM CAPTAIN:
  Option A — Move
    1. Captain presses direction (N/S/E/W)
    2. Server validates + moves sub
    3. Server emits: direction_announced → all, moved_self → captain
    4. Engineer gets direction highlight; clicks a node → engineer_mark
    5. First Mate clicks a system → first_mate_charge
    6. Captain optionally uses weapons/sonar/drone
    7. Captain presses End Turn

  Option B — Surface
    1. Captain presses Surface
    2. Server: 1 damage, clear trail, announce sector to all
    3. Skip engineer/FM actions
    4. Captain presses Dive (re-enters submarine mode)
    5. Captain presses End Turn

ENEMY TEAM:
  - Radio Operator sees direction_announced → draws on tracking board
  - If sonar/drone used: enemy captain responds in their UI
```

---

## Game Rules Reference (Turn-Based)

### Systems (charged by First Mate, activated by Captain)
| System | Charges | Effect |
|--------|---------|--------|
| Torpedo | 3 | Fire at target ≤4 Manhattan dist. Direct=2dmg, Adjacent=1dmg |
| Mine | 3 | Place on adjacent cell. Detonate anytime for same effect |
| Sonar | 3 | Ask row/col/sector → server reveals which 1 of 3 is true |
| Drone | 4 | Ask sector → server confirms yes/no |
| Stealth | 5 | Move 0–4 cells silently (no direction announced) |

### Engineering Board
- 4 directions × 6 nodes (yellow, red, green, radiation)
- Engineer marks 1 node per captain move (in that direction's column)
- 3 circuits spanning directions → when complete, nodes clear, no damage
- Direction full (6/6) → 1 hull damage + clear direction
- All 6 radiation nodes filled → 1 hull damage + clear radiation

### Health
- Start: 4 HP per submarine
- 0 HP → eliminated, other team wins
- Surface: −1 HP (voluntary)
- Explosion: −2 HP direct hit, −1 HP adjacent
- Engineering damage: −1 HP per trigger

---

## File Tree (Target)
```
captain-sonar/
├── PROJECT_PLAN.md       ← this file
├── requirements.txt
├── .gitignore
├── maps.py
├── game_state.py
├── server.py
├── templates/
│   ├── index.html
│   ├── lobby.html
│   ├── captain.html
│   ├── first_mate.html
│   ├── engineer.html
│   └── radio_operator.html
└── static/
    ├── css/
    │   ├── base.css
    │   ├── captain.css
    │   ├── first_mate.css
    │   ├── engineer.css
    │   └── radio_operator.css
    └── js/
        ├── socket_client.js
        ├── captain.js
        ├── first_mate.js
        ├── engineer.js
        └── radio_operator.js
```

---

## Done Criteria
- [ ] 8 players can join, pick teams (blue/red, 4 each), pick roles (1 of each per team)
- [ ] Host can start game
- [ ] Both captains place submarines on the map
- [ ] Turn-based play: captain moves → engineer marks → FM charges → captain uses systems → end turn
- [ ] All 5 systems work correctly (torpedo, mine, drone, sonar, stealth)
- [ ] Surface mechanic works (damage + trail clear + sector announce)
- [ ] Engineering board triggers damage on direction overload and radiation overload
- [ ] Circuit clearing works on engineer board
- [ ] Game ends when a sub reaches 0 HP, winner displayed
- [ ] Game looks polished (dark naval theme, clear role UIs)
- [ ] Code pushed to GitHub
