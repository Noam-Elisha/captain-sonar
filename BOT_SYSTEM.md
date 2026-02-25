# Admiral Radar Bot System Documentation

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Bot Lifecycle](#bot-lifecycle)
3. [The Bot Loop](#the-bot-loop)
4. [Information Asymmetry](#information-asymmetry)
5. [Inter-Bot Communication](#inter-bot-communication)
6. [CaptainBot](#captainbot)
7. [FirstMateBot](#firstmatebot)
8. [EngineerBot](#engineerbot)
9. [RadioOperatorBot](#radiooperatorbot)
10. [Event Dispatch Pipeline](#event-dispatch-pipeline)
11. [Known Limitations & Improvement Areas](#known-limitations--improvement-areas)

---

## Architecture Overview

Bots are **server-side Python classes** (`bots.py`) that run inside the same Flask-SocketIO process as the game engine. Each bot holds only the information its role would have access to in the physical board game — no bot has direct access to the `game_state` dict.

```
┌──────────────────────────────────────────────────────────────┐
│                         server.py                            │
│                                                              │
│  games[game_id]                                              │
│    ├── "game": { ... game_state dict ... }                   │
│    └── "players": {                                          │
│          "Bot-Captain-blue": {                               │
│              "team": "blue",                                 │
│              "role": "captain",                              │
│              "bot": CaptainBot(...)   ◄── bot instance       │
│          },                                                  │
│          "Bot-Engineer-blue": {                              │
│              "bot": EngineerBot(...)                         │
│          },                                                  │
│          ...                                                 │
│        }                                                     │
│                                                              │
│  _run_bot_loop(game_id)                                      │
│    └── polls every 1.2s, executes one bot action per tick    │
│                                                              │
│  _update_ro_bot()     ── feeds public events to RO bot       │
│  _update_captain_bot_sonar/drone/enemy_surfaced()            │
│  _emit_ro_bot_commentary()                                   │
└──────────────────────────────────────────────────────────────┘
```

The key design principle: **bots go through the exact same game_state functions and event dispatch pipeline as human players.** There is no separate bot-only code path for game logic. The only bot-specific code is:
- Decision-making (what action to take)
- Information relay (feeding public events to bot instances)
- The bot loop scheduler

---

## Bot Lifecycle

### 1. Creation (Lobby Phase)

A host clicks "Add Bot" in the lobby UI, triggering the `add_bot` socket event:

```
server.py: on_add_bot(team, role)
  └── _make_bot_player(team, role)
        ├── Instantiates the appropriate bot class (CaptainBot, FirstMateBot, etc.)
        ├── Creates a player entry with bot=<instance>
        └── Adds it to games[game_id]["players"]
```

Bot player names follow the pattern: `Bot-{Role}-{team}` (e.g., `Bot-Captain-blue`).

### 2. Game Start

When the host starts the game:
- CaptainBots auto-place their submarines (calling `bot.choose_placement()`)
- `_schedule_bots(game_id)` launches the background bot loop via `eventlet.spawn_after`

### 3. During Play

The bot loop (`_run_bot_loop`) runs as a background greenlet, polling every 1.2 seconds. Each tick, it executes at most one bot action for the current team's turn.

### 4. Game End

When a team reaches 4 damage, the game phase changes to `"ended"`. The bot loop detects this and stops (`bot_tasks[game_id] = False`).

---

## The Bot Loop

`_run_bot_loop(game_id)` is the central scheduler. It runs continuously and calls `_bot_playing_step(game_id)` each tick.

### Step-by-step execution order in `_bot_playing_step`:

```
Step 0:   DIVE if surfaced
            └── If captain is bot and sub is surfaced and hasn't moved yet, call captain_dive()

Step 0b:  AUTO-RESPOND TO SONAR
            └── If turn is waiting_for="sonar_response" and the enemy captain is a bot,
                call bot.respond_sonar() and feed the response to game_state

Step 1:   CAPTAIN MOVES
            └── If captain is bot and hasn't moved yet:
                ├── bot.decide_action(valid_moves, sub_state) returns:
                │     "surface"         → captain_surface()
                │     "stealth"         → captain_stealth(direction, steps)
                │     ("move", dir)     → captain_move(direction)
                └── Events dispatched, RO bots notified

Step 2:   ENGINEER MARKS
            └── If engineer is bot and captain has moved but engineer hasn't:
                ├── bot.decide_mark(board, direction) returns node index
                └── engineer_mark(direction, index)

Step 3:   FIRST MATE CHARGES
            └── If FM is bot and captain has moved but FM hasn't:
                ├── bot.decide_charge(systems) returns system name
                └── first_mate_charge(system_name)

Step 3.5: CAPTAIN CONSIDERS WEAPON
            └── If captain is bot and turn can end and no system used yet:
                ├── bot.decide_weapon_action(sub_state) returns:
                │     ("torpedo", row, col)      → captain_torpedo(row, col)
                │     ("mine_drop",)             → captain_drop_mine()
                │     ("mine_trigger", row, col)  → captain_trigger_mine(row, col)
                │     ("sonar",)                 → activate_sonar()
                │     ("drone", sector)          → activate_drone(sector)
                │     None                       → skip
                └── Events dispatched accordingly

Step 4:   END TURN
            └── If captain is bot and turn can end, call captain_end_turn()
```

**Important timing detail:** Only ONE action fires per 1.2-second tick. This means a full bot turn (move + engineer mark + FM charge + weapon + end turn) takes 5 ticks = ~6 seconds. This is intentional — it gives the UI time to animate each step and makes the game readable for human observers.

**Early exit:** If the current step requires a human player (e.g., the engineer is human), the function returns `False` and the loop waits for the human to act via socket events.

---

## Information Asymmetry

Each bot class receives only role-appropriate information, matching what a human player would see:

| Bot Role | Knows | Does NOT Know |
|----------|-------|---------------|
| **CaptainBot** | Own position, trail, mines, systems, health. Enemy health. Known enemy sector (from drone/surface). Sonar history. | Enemy exact position, trail, mines, engineering board. |
| **FirstMateBot** | Own systems (charges). Own health. | Enemy anything. Own position. Own engineering state. |
| **EngineerBot** | Own engineering board. Direction to mark. | Enemy anything. Own position. System charges. |
| **RadioOperatorBot** | Enemy's publicly announced directions. Surface sectors. Torpedo/drone events. | Enemy exact position. Own team's state. |

The server enforces this by only passing specific parameters to bot methods:
- `captain_bot.decide_action(valid_moves, sub)` — gets own sub state + computed valid moves
- `engineer_bot.decide_mark(board, direction)` — gets engineering board + which direction section
- `first_mate_bot.decide_charge(systems)` — gets system charge dict
- `radio_operator_bot.record_*()` — gets individual public events

---

## Inter-Bot Communication

**Bots never talk directly to each other.** All information flows through server relay functions that mirror the public announcements in the physical game.

### Communication Flow Diagram

```
CaptainBot (blue) moves NORTH
    │
    ▼
server.py: captain_move("north")
    │
    ├──► _dispatch_events() ──► all human players in room
    │
    ├──► _update_ro_bot(game_id, "blue", "move", direction="north")
    │       └──► RadioOperatorBot (red).record_move("north")
    │
    └──► _broadcast_game_state() ──► personalized state to each player
```

### Relay Functions

| Server Function | Triggers When | Feeds To | Data |
|----------------|---------------|----------|------|
| `_update_ro_bot(move)` | Captain moves | Enemy RO bot | Direction string |
| `_update_ro_bot(surface)` | Captain surfaces | Enemy RO bot | Sector number |
| `_update_ro_bot(torpedo)` | Torpedo fired | Enemy RO bot | Target coordinates |
| `_update_ro_bot(drone)` | Drone used | Enemy RO bot | Queried sector |
| `_update_captain_bot_drone()` | Drone result arrives | Own Captain bot | Sector + boolean |
| `_update_captain_bot_sonar()` | Sonar response arrives | Own Captain bot | Response data |
| `_update_captain_bot_enemy_surfaced()` | Enemy surfaces | Own Captain bot | Sector number |
| `_emit_ro_bot_commentary()` | Turn starts | Own team's chat | Analysis text |

### The Sonar Interaction (Most Complex Flow)

Sonar is the only system requiring a back-and-forth exchange:

```
Team A activates sonar
    │
    ▼
server.py sets waiting_for = "sonar_response" on Team B's turn state
    │
    ├── If Team B captain is human:
    │     └── UI shows sonar response modal, human picks 2 facts
    │
    └── If Team B captain is bot:
          └── _bot_playing_step Step 0b detects waiting_for
              └── bot.respond_sonar(position, sector)
                    ├── Picks 2 random types from {row, col, sector}
                    ├── 50/50: one true, one false
                    └── Returns response dict
              └── captain_respond_sonar(response)
              └── _update_captain_bot_sonar(requesting_team, response)
```

---

## CaptainBot

**File:** `bots.py:50-334` | **The most complex bot — handles movement, weapons, sonar, and stealth.**

### State

```python
self.team          # "blue" or "red"
self.map_name      # "alpha"
self.map_data      # grid + islands + sector info
self.known_enemy_sector  # int or None — set by drone/surface intel
self.sonar_history       # list of sonar response dicts
```

### Placement Strategy

- Blue prefers top-left (sector 1), Red prefers bottom-right (sector 4)
- Picks a random non-island cell in the preferred sector
- Falls back to any valid cell if preferred sector has none

### Movement Decision (`decide_action`)

The captain evaluates moves with a **greedy 1-step lookahead**:

```
1. Get valid_moves list from server (pre-computed, respects trail/islands/mines/bounds)
2. If valid_moves is empty:
     a. Try stealth (if charged) to escape
     b. Otherwise surface (clears trail)
3. If stealth is charged AND only 1-2 valid moves exist:
     a. Plan a stealth move to escape the tight spot
     b. If stealth plan works, return "stealth"
4. For each valid direction, count how many moves would be valid FROM the destination
5. Pick the direction that maximizes future move count (most "open" destination)
6. Return ("move", best_direction)
```

**Weakness:** Only looks 1 step ahead. Can still paint itself into dead-ends that a deeper search would avoid.

### Weapon Decision (`decide_weapon_action`)

Called after the move/engineer/FM steps, before ending the turn:

```
Priority 1: TORPEDO (if charged AND known_enemy_sector exists)
    └── Find closest cell in the known sector within Manhattan range 1-4
    └── Fire at that cell

Priority 2: DRONE (if charged AND no known_enemy_sector)
    └── Pick a random sector (1-4) and scan it

Priority 3: SONAR (if charged)
    └── Activate sonar (triggers interactive response flow)

Default: None (skip weapon, end turn)
```

**Weakness:** Never uses mines offensively. Torpedo targeting picks the *closest* cell in the sector rather than the most likely position. Drone scans random sectors instead of using elimination logic.

### Sonar Response (`respond_sonar`)

When the enemy activates sonar and this bot must respond:

```
1. Pick 2 different types from {row, col, sector}
2. 50/50 coin flip for which is true vs false
3. True value = actual position/sector
4. False value = random different value of the same type
5. Return both as the response
```

**Weakness:** Purely random lie selection. A smarter bot would pick false values that mislead toward a specific wrong area.

### Stealth Planning (`_plan_stealth`)

```
For each of 4 directions:
    Simulate moving 1-4 cells in a straight line
    At each step, check: no islands, no trail, no own mines, in bounds
    Score = (future_valid_moves_at_destination * 10) + steps_taken
Pick the direction+steps with the highest score
```

**Weakness:** Only considers final position quality, not the strategic value of disappearing from the enemy's tracking.

---

## FirstMateBot

**File:** `bots.py:337-373` | **The simplest bot — a fixed priority charger.**

### State

```python
self.team     # "blue" or "red"
PRIORITY = ["torpedo", "mine", "sonar", "drone", "stealth"]
```

### Charging Strategy (`decide_charge`)

```
For each system in priority order:
    If system is not at max charge:
        Return that system name
Return None (all systems full — shouldn't happen in practice)
```

**Max charges:** torpedo=6, mine=6, sonar=6, drone=6, stealth=4

The priority order means torpedo charges first, then mine, then detection systems, then stealth. This makes the bot weapon-focused.

**Weakness:** Completely static priority. Doesn't adapt to game state — doesn't rush sonar when position intel is needed, doesn't prioritize stealth when health is low, doesn't consider which systems the engineer has broken.

---

## EngineerBot

**File:** `bots.py:376-431` | **Strategically marks breakdowns to minimize damage and maximize self-repair.**

### State

```python
self.team  # "blue" or "red"
```

Stateless — all decisions are based on the current engineering board passed as a parameter.

### Marking Strategy (`decide_mark`)

The engineer must mark one node in the direction section matching the captain's move. The strategy has three tiers:

```
Tier 1: COMPLETE A CIRCUIT (best case — free repair)
    For each unmarked node in this direction section:
        If it belongs to a circuit (index 0, 1, or 2 → circuits C1, C2, C3):
            Count how many of the circuit's other 3 nodes are already marked
            If 3 of 4 are marked (this would complete it):
                Mark this node → circuit auto-clears all 4 nodes

Tier 2: PREFER NON-RADIATION, CIRCUIT-LINKED NODES
    Among unmarked nodes:
        Filter out radiation nodes (index 5)
        Among non-radiation, prefer circuit-linked nodes (index 0-2)
            These contribute to future circuit completions
        Pick the first matching node

Tier 3: LAST RESORT — RADIATION NODE
    If only radiation nodes remain unmarked, mark one
    (4 radiation marks = 1 self-damage + full board clear)
```

### Engineering Board Layout Reference

Each direction section has 6 nodes (indices 0-5):

| Index | Type | Circuit | Color | Blocks |
|-------|------|---------|-------|--------|
| 0 | Circuit | C1 | varies by direction | varies |
| 1 | Circuit | C2 | varies by direction | varies |
| 2 | Circuit | C3 | varies by direction | varies |
| 3 | Extra | none | varies | varies |
| 4 | Extra | none | varies | varies |
| 5 | Radiation | none | radiation | reactor hazard |

Node colors (red/green/yellow) determine which systems they block when marked:
- **Red** → blocks torpedo + mine
- **Green** → blocks sonar + drone
- **Yellow** → blocks stealth

Circuits span all 4 direction sections (one node per section). When all 4 nodes in a circuit are marked, they auto-clear — this is the primary way to repair without surfacing.

**Weakness:** Doesn't consider which systems are currently needed. Might mark a green node (blocking sonar/drone) when the captain desperately needs sonar. No coordination with captain's weapon plans.

---

## RadioOperatorBot

**File:** `bots.py:433-486` | **Tracks enemy movements and generates commentary.**

### State

```python
self.team             # "blue" or "red"
self.move_log         # list of direction strings, cleared on enemy surface
self.surface_sectors  # list of sectors where enemy surfaced
self.torpedo_count    # how many torpedoes enemy fired
self.drone_sectors    # sectors enemy has drone-scanned
```

### Event Recording

| Method | Triggered By | Action |
|--------|-------------|--------|
| `record_move(direction)` | Enemy captain moves | Appends direction to `move_log` |
| `record_surface(sector)` | Enemy surfaces | Appends to `surface_sectors`, clears `move_log` |
| `record_torpedo(row, col)` | Enemy fires torpedo | Increments `torpedo_count` |
| `record_drone(sector)` | Enemy uses drone | Appends to `drone_sectors` |

### Commentary Generation (`generate_commentary`)

Called at the start of each of the bot's team's turns via `_emit_ro_bot_commentary`. Produces a text summary:

```
Parts (concatenated):
1. "Enemy last surfaced in sector {N}." (if any surface events)
2. "Enemy moving mostly {DIR} ({count}/{total} recent moves)."
     └── Analyzes last 6 moves, reports dominant direction if ≥50%
3. "Enemy has fired {N} torpedo(es)." (if any)
```

The commentary appears as a `bot_chat` event in the team's event log.

**Weakness:** This is the most underdeveloped bot. It tracks events but does NOT:
- Maintain a set of possible enemy positions
- Eliminate impossible positions using island/trail constraints
- Feed position estimates to the CaptainBot
- Use surface sector + subsequent moves to narrow down exact position
- Cross-reference torpedo origin with movement patterns

---

## Event Dispatch Pipeline

All game events flow through a unified pipeline, ensuring bots and humans receive the same information:

```
Player/Bot Action
    │
    ▼
game_state.py function (e.g., captain_move, captain_torpedo)
    │
    ├── Modifies game state dict
    ├── Returns event list: [{"event": "type", "team": ..., "data": ...}, ...]
    │
    ▼
server.py: _dispatch_events(game_id, events)
    │
    ├── For each event:
    │   ├── socketio.emit() to appropriate room/player (human UI)
    │   └── _update_ro_bot() / _update_captain_bot_*() (bot relay)
    │
    ▼
_broadcast_game_state(game_id)
    │
    ├── serialize_game(game, perspective_team="blue") → blue humans
    ├── serialize_game(game, perspective_team="red")  → red humans
    └── serialize_game(game, perspective_team=None)    → spectators (full visibility)
```

### Event Types

| Event | Emitted By | Visible To | Bot Relay |
|-------|-----------|------------|-----------|
| `direction_announced` | Captain move | All players | Enemy RO bot |
| `surface_announced` | Captain surface | All players | Enemy RO bot + Enemy Captain bot |
| `torpedo_fired` | Captain torpedo | All players | Enemy RO bot |
| `torpedo_result` | Server (damage calc) | All players | — |
| `mine_dropped` | Captain mine | Own team only | — |
| `mine_triggered` | Captain trigger | All players | — |
| `mine_result` | Server (damage calc) | All players | — |
| `drone_query` | Drone activation | All players | Enemy RO bot |
| `drone_result` | Server (sector check) | Activating team | Own Captain bot |
| `sonar_query` | Sonar activation | All players | — |
| `sonar_response` | Enemy captain | All players | Own Captain bot |
| `stealth_activated` | Captain stealth | All players (no direction) | Enemy RO bot (no direction) |
| `stealth_direction` | Captain stealth | Own team only | — |
| `engineer_breakdown` | Engineer mark | Own team | — |
| `system_charged` | FM charge | Own team | — |
| `bot_chat` | Any bot | Own team | — |
| `damage` | Various | All players | — |

---

## Known Limitations & Improvement Areas

### CaptainBot
- **Movement:** Greedy 1-step lookahead — can walk into dead ends. Could benefit from a deeper search (BFS/DFS for longest path) or dead-end detection.
- **Torpedo targeting:** Fires at the closest cell in the known sector. Should incorporate RadioOperatorBot's position estimates for precision.
- **No mine usage:** Never drops or triggers mines. Mines are a powerful positional tool — drop in chokepoints, trigger when enemy is near.
- **Drone strategy:** Random sector scanning instead of systematic elimination (scan the sector with the most possible positions).
- **Sonar analysis:** Records sonar responses but the current logic for using them to narrow position is minimal.
- **No self-damage avoidance:** Doesn't check if a torpedo or mine detonation would hit its own submarine.

### FirstMateBot
- **Static priority:** Doesn't adapt to game state. Should rush charging sonar/drone when position intel is stale, prioritize stealth when trapped, or delay torpedo if engineer has blocked it.
- **No engineer awareness:** Charges systems that may be broken (red/green/yellow nodes marked). Wasted charges if the system can't activate anyway.

### EngineerBot
- **No system priority awareness:** Doesn't know which systems the captain needs. Could communicate with captain about which colors (red/green/yellow) to protect.
- **No proactive blocking:** Doesn't strategically mark nodes to block enemy-useful systems while keeping own-useful systems clear.

### RadioOperatorBot
- **No position tracking:** The biggest gap. Should maintain a set of possible positions using:
  - Starting constraint (any sea cell)
  - Sequential direction moves (each narrows possibilities)
  - Island elimination (positions that would cross islands are impossible)
  - Surface sector announcements (pins position to a sector, then trail resets)
  - Torpedo range backtracking (enemy fired from within range 1-4 of target)
- **No communication to CaptainBot:** Commentary goes to the event log (human-readable) but there's no structured data feed from RO bot → Captain bot for targeting.
- **Passive tracking only:** Doesn't recommend actions (e.g., "use drone on sector 3 to narrow it down").

### System-Wide
- **No cross-role coordination:** The captain, FM, and engineer on the same team never coordinate. In the physical game, teammates constantly communicate ("Don't go east, I need a north to complete a circuit!", "Torpedo is ready!", "Sonar is broken!").
- **No adaptive difficulty:** All bots play at the same fixed skill level.
- **No personality/strategy profiles:** Could have aggressive bots (rush torpedo), defensive bots (prioritize stealth + sonar), etc.
- **Surface timing:** Bot surfaces only when completely trapped. Could surface proactively to clear a bad engineering board or reset a tangled trail.
