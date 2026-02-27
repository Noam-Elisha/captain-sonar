# Comms Cleanup, Human Comms, Spectator Eng Board, Lobby Map Editor — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Clean up bot comms noise, add human player quick-action comms, match spectator engineering board to engineer view, and build a lobby map editor with equal sector enforcement.

**Architecture:** Four independent features sharing the same codebase. Tasks 1-2 (comms) should be done first as they're smallest. Task 3 (spectator eng) is self-contained CSS/JS. Task 4 (map editor) is the largest — touches lobby, maps.py, server.py, and all template files.

**Tech Stack:** Flask + SocketIO (Python), vanilla JS, CSS variables for theming.

---

## Task 1: Strip Noise from Bot Chat Messages

Clean up all bot_chat emissions: remove emojis, shorten names, condense text, eliminate duplicates.

**Files:**
- Modify: `server.py` — bot_chat emission points
- Modify: `bots.py` — `generate_report()`, `generate_commentary()`, `describe_mark()`, `describe_charge()`
- Modify: `static/js/spectator.js` — bot_chat handler (line 136)
- Modify: `static/js/captain.js` — bot_chat handler (line 229)
- Modify: `static/js/engineer.js` — bot_chat handler (line 150)
- Modify: `static/js/first_mate.js` — bot_chat handler (line 176)
- Modify: `static/js/radio_operator.js` — bot_chat handler (line 131)

### Step 1: Remove all public bot_chat that duplicates game events

In `server.py`, DELETE these `socketio.emit("bot_chat", ...)` calls entirely:
- Line 598 (dive — already `dive_announced`)
- Line 683 (move direction — already `direction_announced`)
- Line 709 (stealth — already `stealth_announced`)
- Line 728 (surface — already `surface_announced`)
- Line 757 (torpedo — already `torpedo_fired`)
- Line 770 (drone — already `drone_result`)
- Line 781 (sonar activation — already `sonar_announced`)
- Line 871 (sonar response — already `sonar_result`)

### Step 2: Remove `_emit_ro_bot_commentary()` and its call

In `server.py`:
- Delete `_emit_ro_bot_commentary()` function (lines 501-512)
- Delete its call at line ~281 inside `_dispatch_events` under `turn_start` event: `_emit_ro_bot_commentary(game_id, ev["team"])`

This eliminates duplicate RO messages — the pre-turn report from `_process_bot_comms_pre_turn` is sufficient.

### Step 3: Strip emojis from remaining team-only bot_chat messages

In `server.py`, edit the 3 remaining `_emit_bot_chat_team_only` calls:

**RO report** (line ~451): Change msg from `f"📡 {summary}"` to `f"{summary}"`
**Engineer mark** (line ~818): Change msg from `f"🔧 {desc}"` to `f"{desc}"`
**FM charge** (line ~843): Change msg from `f"⚙️ {bot.describe_charge(system)}"` to `f"{bot.describe_charge(system)}"`
**Placement** (line ~570): Change msg from `"Submarine deployed 🗺"` to `"Submarine deployed"`

### Step 4: Condense bot message text in bots.py

In `bots.py`:

**`RadioOperatorBot.generate_report()`** (~line 135): Condense from verbose analysis to short summary.
Replace the current implementation body with:
```python
count = len(self.possible_positions)
sectors = self._estimate_sectors()
sector_count = len(sectors)
if count <= 5:
    return f"Enemy pinpointed to ~{count} cells in sector(s) {','.join(str(s) for s in sorted(sectors))}"
elif count <= 30:
    return f"~{count} positions, likely sector(s) {','.join(str(s) for s in sorted(sectors))}"
else:
    return f"Tracking {count} positions across {sector_count} sector(s)"
```

**`EngineerBot.describe_mark()`** (~line 973): Shorten from descriptive to terse.
Replace body with:
```python
return f"Marked {direction} node {index}"
```

**`FirstMateBot.describe_charge()`** (~line 812): Shorten.
Replace body with:
```python
return f"Charged {system}"
```

### Step 5: Update all JS bot_chat handlers to use short role tags

Replace the bot_chat handler in ALL 5 JS files with a version that displays `[RO]`, `[CAP]`, `[FM]`, `[ENG]` instead of the full bot name:

```javascript
socket.on('bot_chat', data => {
  const ROLE_TAG = {captain:'CAP', first_mate:'FM', engineer:'ENG', radio_operator:'RO'};
  const tag = ROLE_TAG[data.role] || 'BOT';
  logEvent(`[${tag}] ${data.msg}`, 'bot');
});
```

For `spectator.js`, keep the team coloring:
```javascript
socket.on('bot_chat', data => {
  const ROLE_TAG = {captain:'CAP', first_mate:'FM', engineer:'ENG', radio_operator:'RO'};
  const tag = ROLE_TAG[data.role] || 'BOT';
  const teamCls = data.team === 'blue' ? 'bot-blue' : data.team === 'red' ? 'bot-red' : 'bot';
  logEvent(`[${tag}] ${data.msg}`, teamCls);
});
```

### Step 6: Test and commit

Run: `python server.py` (start server, play a few bot turns, verify comms are clean)
```bash
git add server.py bots.py static/js/spectator.js static/js/captain.js static/js/engineer.js static/js/first_mate.js static/js/radio_operator.js
git commit -m "fix: clean up bot comms noise — remove emojis, shorten messages, deduplicate"
```

---

## Task 2: Human Player Quick Action Comms

Add role-specific quick action buttons so human players can communicate with bot teammates.

**Files:**
- Modify: `comms.py` — add `post_from_human()` method
- Modify: `server.py` — add `on_player_comms` handler
- Modify: `bots.py` — handle human commands in `process_inbox()`
- Modify: `templates/captain.html` — add comms buttons
- Modify: `templates/radio_operator.html` — add comms buttons
- Modify: `templates/first_mate.html` — add comms buttons
- Modify: `templates/engineer.html` — add comms buttons
- Modify: `static/js/captain.js` — button click handlers
- Modify: `static/js/radio_operator.js` — button click handlers
- Modify: `static/js/first_mate.js` — button click handlers
- Modify: `static/js/engineer.js` — button click handlers
- Modify: `static/css/base.css` — comms button styles

### Step 1: Add `post_from_human()` to TeamComms

In `comms.py`, add method to `TeamComms` class:
```python
def post_from_human(self, from_role: str, msg_type: str, data: dict = None):
    """Post a message from a human player into the comms system.
    Routes to appropriate bot inboxes based on msg_type."""
    msg = {"type": msg_type, "from": from_role, "human": True}
    if data:
        msg.update(data)

    if msg_type == "request_position_report":
        self._inboxes["radio_operator"].append(msg)
    elif msg_type == "set_charge_priority":
        self._inboxes["first_mate"].append(msg)
    elif msg_type == "set_system_protect":
        self._inboxes["engineer"].append(msg)
    elif msg_type == "report_positions":
        self._inboxes["captain"].append(msg)
    elif msg_type == "status_report":
        self._inboxes["captain"].append(msg)
    elif msg_type == "recommend_directions":
        self._inboxes["captain"].append(msg)
```

### Step 2: Add server-side `on_player_comms` handler

In `server.py`, add new socket event handler after the existing captain/engineer/FM handlers:

```python
@socketio.on("player_comms")
def on_player_comms(data):
    """Human player sends a quick-action comms message to bot teammates."""
    game_id = (data.get("game_id") or "").upper()
    name = data.get("name", "")
    msg_type = data.get("msg_type", "")
    payload = data.get("payload", {})

    g = games.get(game_id)
    if not g or g["game"] is None:
        return emit("error", {"msg": "Game not found"})

    p = _get_player(game_id, name)
    if not p:
        return emit("error", {"msg": "Player not found"})

    comms = _get_team_comms(game_id, p["team"])
    if not comms:
        return emit("error", {"msg": "Comms not available"})

    comms.post_from_human(p["role"], msg_type, payload)

    # Echo the message to the team's comms log
    ROLE_TAG = {"captain": "CAP", "first_mate": "FM", "engineer": "ENG", "radio_operator": "RO"}
    tag = ROLE_TAG.get(p["role"], "???")
    # Build readable message
    readable = _human_comms_readable(msg_type, payload)
    _emit_bot_chat_team_only(game_id, p["team"], {
        "team": p["team"], "role": p["role"], "name": name,
        "msg": readable,
    })


def _human_comms_readable(msg_type, payload):
    """Convert a player_comms msg_type into a readable log message."""
    if msg_type == "request_position_report":
        return "Requesting position report"
    elif msg_type == "set_charge_priority":
        return f"Priority: {payload.get('system', '?')}"
    elif msg_type == "set_system_protect":
        return f"Protect: {payload.get('system', '?')}"
    elif msg_type == "report_positions":
        return "Reporting positions"
    elif msg_type == "status_report":
        return "Systems status report"
    elif msg_type == "recommend_directions":
        return "Direction recommendation"
    return msg_type
```

### Step 3: Handle human commands in bot `process_inbox()`

In `bots.py`, add handling for `"human": True` messages in each bot's `process_inbox()`:

**RadioOperatorBot.process_inbox()** (~line 107): After existing processing, add:
```python
if msg.get("human") and msg["type"] == "request_position_report":
    self._pending_report = True  # Flag to generate report on next opportunity
```

**CaptainBot.process_inbox()** (~line 362): Already handles RO reports, FM status, etc. Human messages with types like "report_positions", "status_report", "recommend_directions" just provide info — captain bot reads them as-is from inbox. No special handling needed since the messages contain the data.

**FirstMateBot.process_inbox()** (~line 768): Add handling for human captain's priority:
```python
if msg.get("human") and msg["type"] == "set_charge_priority":
    self.charge_priority = msg.get("system")
```

**EngineerBot.process_inbox()** (~line 845): Add handling for human captain's protect:
```python
if msg.get("human") and msg["type"] == "set_system_protect":
    system = msg.get("system")
    if system:
        self.protect_systems = [system]
```

### Step 4: Add comms button HTML to all 4 player templates

Add a `.comms-buttons` div above the event-log in each template.

**In `templates/captain.html`** (before the COMMS LOG section):
```html
<div class="comms-buttons" id="comms-buttons">
  <button class="comms-btn" onclick="sendComms('request_position_report')">Request Position</button>
  <button class="comms-btn" id="btn-priority" onclick="cyclePriority()">Priority: Torpedo</button>
  <button class="comms-btn" id="btn-protect" onclick="cycleProtect()">Protect: Torpedo</button>
</div>
```

**In `templates/radio_operator.html`** (before COMMS LOG):
```html
<div class="comms-buttons" id="comms-buttons">
  <button class="comms-btn" onclick="sendComms('report_positions')">Report Positions</button>
</div>
```

**In `templates/first_mate.html`** (before COMMS LOG):
```html
<div class="comms-buttons" id="comms-buttons">
  <button class="comms-btn" onclick="sendComms('status_report')">Status Report</button>
</div>
```

**In `templates/engineer.html`** (before COMMS LOG):
```html
<div class="comms-buttons" id="comms-buttons">
  <button class="comms-btn" onclick="sendComms('recommend_directions')">Recommend Directions</button>
</div>
```

### Step 5: Add JS click handlers to each player JS file

Add to each player JS file:

**Common for all** (add near top of each file):
```javascript
function sendComms(msgType, payload) {
  socket.emit('player_comms', {
    game_id: GAME_ID, name: MY_NAME,
    msg_type: msgType, payload: payload || {}
  });
}
```

**Captain-specific** (in captain.js):
```javascript
const SYSTEMS = ['torpedo', 'mine', 'sonar', 'drone', 'stealth'];
let priorityIdx = 0, protectIdx = 0;

function cyclePriority() {
  priorityIdx = (priorityIdx + 1) % SYSTEMS.length;
  const sys = SYSTEMS[priorityIdx];
  document.getElementById('btn-priority').textContent = 'Priority: ' + sys.charAt(0).toUpperCase() + sys.slice(1);
  sendComms('set_charge_priority', { system: sys });
}

function cycleProtect() {
  protectIdx = (protectIdx + 1) % SYSTEMS.length;
  const sys = SYSTEMS[protectIdx];
  document.getElementById('btn-protect').textContent = 'Protect: ' + sys.charAt(0).toUpperCase() + sys.slice(1);
  sendComms('set_system_protect', { system: sys });
}
```

### Step 6: Add comms button styles to base.css

In `static/css/base.css`, add:
```css
.comms-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: .35rem;
  padding: .4rem .75rem;
}
.comms-btn {
  padding: .3rem .6rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: rgba(255,255,255,.04);
  color: var(--text-muted);
  font-size: .72rem;
  cursor: pointer;
  transition: all .15s;
  letter-spacing: .03em;
}
.comms-btn:hover {
  border-color: var(--accent);
  color: var(--text);
  background: rgba(200,168,50,.1);
}
```

### Step 7: Commit

```bash
git add comms.py server.py bots.py templates/captain.html templates/radio_operator.html templates/first_mate.html templates/engineer.html static/js/captain.js static/js/radio_operator.js static/js/first_mate.js static/js/engineer.js static/css/base.css
git commit -m "feat: add human player quick action comms buttons"
```

---

## Task 3: Spectator Engineering Board Redesign

Replace the simplified 4-column layout with a mini 2x2 grid matching the engineer view.

**Files:**
- Modify: `static/js/spectator.js` — rewrite `renderEngBoard()` (lines 260-339)
- Modify: `static/css/spectator.css` — replace mini-eng styles with 2x2 layout

### Step 1: Replace spectator CSS engineering styles

In `static/css/spectator.css`, replace all `.mini-eng`, `.mini-col`, `.mini-col-lbl`, `.mini-node`, `.mini-reactor-div`, and `.mini-circ-*` styles (lines ~171-219) with:

```css
/* ── Mini engineering board (2x2 matching engineer view) ──── */
.mini-eng-2x2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px;
  position: relative;
}
.mini-eng-section {
  background: rgba(30, 30, 35, 0.45);
  border: 1px solid rgba(100, 100, 110, 0.25);
  border-radius: 6px;
  padding: 4px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
}
.mini-dir-label {
  font-size: .52rem;
  font-weight: 700;
  letter-spacing: .06em;
  color: var(--text-muted);
  text-transform: uppercase;
  line-height: 1;
}
.mini-node-row {
  display: flex;
  gap: 3px;
  justify-content: center;
}
.mini-node-2x2 {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 1px solid rgba(255,255,255,.15);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: .32rem;
  font-weight: 700;
  flex-shrink: 0;
  transition: background .2s, border-color .2s;
}
.mini-node-2x2.red        { background: rgba(239,68,68,.18);  border-color: rgba(239,68,68,.40); }
.mini-node-2x2.green      { background: rgba(34,197,94,.18);  border-color: rgba(34,197,94,.40); }
.mini-node-2x2.yellow     { background: rgba(234,179,8,.18);  border-color: rgba(234,179,8,.40); }
.mini-node-2x2.radiation  { background: rgba(168,85,247,.18); border-color: rgba(168,85,247,.40); }
.mini-node-2x2.red.marked        { background: var(--col-red);    border-color: var(--col-red);    box-shadow: 0 0 3px var(--col-red); }
.mini-node-2x2.green.marked      { background: var(--col-green);  border-color: var(--col-green);  box-shadow: 0 0 3px var(--col-green); }
.mini-node-2x2.yellow.marked     { background: var(--col-yellow); border-color: var(--col-yellow); box-shadow: 0 0 3px var(--col-yellow); }
.mini-node-2x2.radiation.marked  { background: var(--col-radiation); border-color: var(--col-radiation); box-shadow: 0 0 3px var(--col-radiation); }
.mini-circuit-badge {
  font-size: .28rem;
  font-weight: 900;
  line-height: 1;
  opacity: .8;
}
.mini-circuit-badge.c1 { color: #f97316; }
.mini-circuit-badge.c2 { color: #06b6d4; }
.mini-circuit-badge.c3 { color: #ec4899; }
.mini-node-2x2.marked .mini-circuit-badge { color: #fff; opacity: .6; }
.mini-rad-sym { font-size: .5rem; opacity: .85; }
.mini-node-2x2.marked .mini-rad-sym { color: #fff; }
.mini-divider {
  width: 80%;
  border-top: 1px dashed rgba(255,255,255,.1);
  margin: 1px 0;
}
```

### Step 2: Rewrite `renderEngBoard()` in spectator.js

Replace the function (lines 260-339) with:

```javascript
function renderEngBoard(id, board) {
  const el = document.getElementById(id);
  if (!el || !board) return;
  el.innerHTML = '';

  const DIR_ORDER_2X2 = [
    ['west', 'north'],
    ['south', 'east']
  ];
  const DIR_LABELS = { west: 'W', north: 'N', south: 'S', east: 'E' };
  const CIRCUIT_LABELS = ['C1', 'C2', 'C3'];
  const CIRCUIT_CLASSES = ['c1', 'c2', 'c3'];

  const grid = document.createElement('div');
  grid.className = 'mini-eng-2x2';

  DIR_ORDER_2X2.flat().forEach(dir => {
    const nodes = board[dir];
    if (!nodes) return;

    const section = document.createElement('div');
    section.className = 'mini-eng-section';

    // Direction label
    const lbl = document.createElement('div');
    lbl.className = 'mini-dir-label';
    lbl.textContent = DIR_LABELS[dir];
    section.appendChild(lbl);

    // Circuit nodes (0-2)
    const mainRow = document.createElement('div');
    mainRow.className = 'mini-node-row';
    for (let i = 0; i < 3; i++) {
      const node = nodes[i];
      const dot = document.createElement('div');
      dot.className = 'mini-node-2x2 ' + node.color + (node.marked ? ' marked' : '');
      const badge = document.createElement('span');
      badge.className = 'mini-circuit-badge ' + CIRCUIT_CLASSES[i];
      badge.textContent = CIRCUIT_LABELS[i];
      dot.appendChild(badge);
      mainRow.appendChild(dot);
    }
    section.appendChild(mainRow);

    // Divider
    const divider = document.createElement('div');
    divider.className = 'mini-divider';
    section.appendChild(divider);

    // Extra nodes (3-5)
    const extraRow = document.createElement('div');
    extraRow.className = 'mini-node-row';
    for (let i = 3; i < 6; i++) {
      const node = nodes[i];
      const dot = document.createElement('div');
      dot.className = 'mini-node-2x2 ' + node.color + (node.marked ? ' marked' : '');
      if (node.color === 'radiation') {
        const sym = document.createElement('span');
        sym.className = 'mini-rad-sym';
        sym.textContent = '\u2622';
        dot.appendChild(sym);
      }
      extraRow.appendChild(dot);
    }
    section.appendChild(extraRow);

    grid.appendChild(section);
  });

  el.appendChild(grid);
}
```

### Step 3: Commit

```bash
git add static/js/spectator.js static/css/spectator.css
git commit -m "fix: spectator engineering board now matches engineer 2x2 layout"
```

---

## Task 4: Lobby Map Editor with Equal Sectors

Add map customization to the lobby with sliders, live preview, random island generation, and enforced equal sectors.

**Files:**
- Modify: `maps.py` — add `generate_map()`, `generate_islands()`
- Modify: `server.py` — handle map settings, use generated map at game start
- Modify: `templates/lobby.html` — add map editor UI
- Modify: `templates/spectator.html` — support dynamic sector dimensions
- Modify: `static/js/spectator.js` — support separate sector width/height

### Step 1: Add map generation to maps.py

Replace the hardcoded `MAPS` dict with a generation function. Keep `get_sector()` and `get_col_labels()`.

```python
import math
import random

# Default map for backwards compatibility
DEFAULT_SETTINGS = {
    "rows": 15,
    "cols": 15,
    "sector_width": 5,
    "sector_height": 5,
    "num_islands": 12,
    "island_size": 2,
}

def generate_map(settings=None):
    """Generate a map definition from settings."""
    s = settings or DEFAULT_SETTINGS
    rows = s["rows"]
    cols = s["cols"]
    sw = s["sector_width"]
    sh = s["sector_height"]

    islands = generate_islands(rows, cols, s.get("num_islands", 12), s.get("island_size", 2))

    return {
        "name": "Custom Map",
        "rows": rows,
        "cols": cols,
        "sector_width": sw,
        "sector_height": sh,
        "sector_size": sw,  # kept for backwards compat with get_sector()
        "islands": islands,
    }


def generate_islands(rows, cols, num_islands, max_island_size):
    """Generate random island positions avoiding edges."""
    island_set = set()
    max_islands = int(rows * cols * 0.1)
    num_islands = min(num_islands, max_islands)

    for _ in range(num_islands):
        # Find valid position (not on edges, not overlapping)
        attempts = 0
        while attempts < 50:
            r = random.randint(1, rows - 2)
            c = random.randint(1, cols - 2)
            if (r, c) not in island_set:
                break
            attempts += 1
        else:
            continue

        # Determine size
        size = 1
        if max_island_size >= 2 and random.random() < 0.15:
            size = random.randint(2, max_island_size)

        # Place island cells
        for di in range(size):
            for dj in range(size):
                nr, nc = r + di, c + dj
                if 0 < nr < rows - 1 and 0 < nc < cols - 1:
                    # Hollow corners for variety on larger islands
                    if size >= 3 and (di in (0, size-1)) and (dj in (0, size-1)):
                        if random.random() < 0.4:
                            continue
                    island_set.add((nr, nc))

    return sorted(island_set)


def get_sector(row, col, sector_size=5, map_cols=15):
    """Return 1-indexed sector number for a given (row, col)."""
    sectors_per_row = math.ceil(map_cols / sector_size)
    sr = row // sector_size
    sc = col // sector_size
    return sr * sectors_per_row + sc + 1


def get_col_labels(n):
    """Generate A, B, C ... Z, AA, AB ... column labels."""
    labels = []
    for i in range(n):
        if i < 26:
            labels.append(chr(ord('A') + i))
        else:
            labels.append(chr(ord('A') + (i // 26) - 1) + chr(ord('A') + (i % 26)))
    return labels
```

### Step 2: Update server.py to handle map settings and use generated maps

**Add `map_settings` socket handler** (after `on_remove_bot`):
```python
@socketio.on("map_settings")
def on_map_settings(data):
    """Host updates map settings in the lobby."""
    game_id = (data.get("game_id") or "").upper()
    name = data.get("name", "")
    settings = data.get("settings", {})

    if game_id not in games:
        return emit("error", {"msg": "Game not found"})
    g = games[game_id]
    if g["host"] != name:
        return emit("error", {"msg": "Only the host can change map settings"})
    if g["game"] is not None:
        return emit("error", {"msg": "Cannot change map after game starts"})

    # Validate
    rows = int(settings.get("rows", 15))
    cols = int(settings.get("cols", 15))
    sw = int(settings.get("sector_width", 5))
    sh = int(settings.get("sector_height", 5))
    if rows % sh != 0 or cols % sw != 0:
        return emit("error", {"msg": "Dimensions must be divisible by sector size"})

    g["map_settings"] = settings
    # Broadcast to all in lobby so non-hosts see the preview
    socketio.emit("map_settings_update", {"settings": settings}, room=game_id)
```

**Modify `on_start_game`** (line ~1234): Replace `g["game"] = gs.make_game("alpha")` with:
```python
from maps import generate_map
map_settings = g.get("map_settings", maps.DEFAULT_SETTINGS)
map_def = generate_map(map_settings)
g["game"] = gs.make_game_with_map(map_def)
```

**Add `make_game_with_map()` to game_state.py** (or modify `make_game` to accept a map_def):
In `game_state.py`, modify `make_game()` to accept an optional map parameter:
```python
def make_game(map_name_or_def="alpha"):
    if isinstance(map_name_or_def, dict):
        map_def = map_name_or_def
    else:
        from maps import generate_map, DEFAULT_SETTINGS
        map_def = generate_map(DEFAULT_SETTINGS)
    # ... rest of initialization using map_def
```

**Update the `game_started` emission** in `on_start_game` to include `sector_width` and `sector_height`:
```python
socketio.emit("game_started", {
    "map": {
        "rows":          map_def["rows"],
        "cols":          map_def["cols"],
        "sector_width":  map_def["sector_width"],
        "sector_height": map_def["sector_height"],
        "sector_size":   map_def["sector_size"],
        "islands":       map_def["islands"],
        "col_labels":    get_col_labels(map_def["cols"]),
    },
    "turn_order": teams_present,
}, room=game_id)
```

### Step 3: Add map editor UI to lobby.html

Insert a new map editor section between the spectators panel and the controls div (after line ~158, before line ~166):

```html
<!-- Map Editor (host only) -->
<div class="map-editor" id="map-editor" style="display:none">
  <h2>MAP SETTINGS</h2>
  <div class="map-controls">
    <div class="slider-group">
      <label>Rows: <span id="val-rows">15</span></label>
      <input type="range" id="sl-rows" min="5" max="30" value="15">
    </div>
    <div class="slider-group">
      <label>Columns: <span id="val-cols">15</span></label>
      <input type="range" id="sl-cols" min="5" max="30" value="15">
    </div>
    <div class="slider-group">
      <label>Sector Width: <span id="val-sw">5</span></label>
      <input type="range" id="sl-sw" min="2" max="10" value="5">
    </div>
    <div class="slider-group">
      <label>Sector Height: <span id="val-sh">5</span></label>
      <input type="range" id="sl-sh" min="2" max="10" value="5">
    </div>
    <div class="slider-group">
      <label>Islands: <span id="val-islands">12</span></label>
      <input type="range" id="sl-islands" min="1" max="30" value="12">
    </div>
    <div class="slider-group">
      <label>Max Island Size: <span id="val-isz">2</span></label>
      <input type="range" id="sl-isz" min="1" max="5" value="2">
    </div>
    <button class="btn-shuffle" onclick="shuffleIslands()">Shuffle Islands</button>
  </div>
  <div class="map-preview-container">
    <canvas id="map-preview" width="300" height="300"></canvas>
  </div>
</div>
```

Add corresponding CSS (inline in lobby.html's `<style>` block):
```css
.map-editor {
  background:var(--bg-panel); border:1px solid var(--border); border-radius:12px;
  padding:1.2rem; max-width:820px; width:100%; margin-top:1rem;
  border-top:3px solid var(--accent);
}
.map-editor h2 { color:var(--accent); letter-spacing:.1em; margin:0 0 1rem; font-size:.95rem; }
.map-controls { display:grid; grid-template-columns:1fr 1fr; gap:.6rem; margin-bottom:1rem; }
.slider-group label { display:block; font-size:.78rem; color:var(--text-muted); margin-bottom:.2rem; }
.slider-group label span { color:var(--text); font-weight:700; }
.slider-group input[type=range] { width:100%; accent-color:var(--accent); }
.btn-shuffle {
  grid-column:span 2; padding:.5rem; border:1px solid var(--border); border-radius:7px;
  background:rgba(200,168,50,.07); color:#c8a832; cursor:pointer; font-size:.8rem;
}
.btn-shuffle:hover { background:rgba(200,168,50,.18); }
.map-preview-container { text-align:center; }
#map-preview { border:1px solid var(--border); border-radius:8px; background:var(--bg-deep); max-width:100%; }
```

### Step 4: Add map editor JS to lobby.html

Add JavaScript for slider logic, auto-snapping, preview rendering, and socket communication. Insert in the `<script>` block:

```javascript
// Map editor state
let mapSettings = { rows: 15, cols: 15, sector_width: 5, sector_height: 5, num_islands: 12, island_size: 2 };
let previewIslands = [];

function initMapEditor() {
  const sliders = ['rows', 'cols', 'sw', 'sh', 'islands', 'isz'];
  sliders.forEach(id => {
    const sl = document.getElementById('sl-' + id);
    if (sl) sl.addEventListener('input', onSliderChange);
  });
  generatePreviewIslands();
  drawMapPreview();
}

function onSliderChange() {
  let rows = +document.getElementById('sl-rows').value;
  let cols = +document.getElementById('sl-cols').value;
  let sw = +document.getElementById('sl-sw').value;
  let sh = +document.getElementById('sl-sh').value;

  // Snap dimensions to be divisible by sector size
  rows = Math.max(sw, Math.round(rows / sh) * sh);
  cols = Math.max(sh, Math.round(cols / sw) * sw);
  document.getElementById('sl-rows').value = rows;
  document.getElementById('sl-cols').value = cols;

  // Cap islands at 10% of map area
  const maxIslands = Math.floor(rows * cols * 0.1);
  const islandSlider = document.getElementById('sl-islands');
  islandSlider.max = maxIslands;
  if (+islandSlider.value > maxIslands) islandSlider.value = maxIslands;

  mapSettings = {
    rows, cols, sector_width: sw, sector_height: sh,
    num_islands: +document.getElementById('sl-islands').value,
    island_size: +document.getElementById('sl-isz').value,
  };

  // Update labels
  document.getElementById('val-rows').textContent = rows;
  document.getElementById('val-cols').textContent = cols;
  document.getElementById('val-sw').textContent = sw;
  document.getElementById('val-sh').textContent = sh;
  document.getElementById('val-islands').textContent = mapSettings.num_islands;
  document.getElementById('val-isz').textContent = mapSettings.island_size;

  generatePreviewIslands();
  drawMapPreview();
  emitMapSettings();
}

function shuffleIslands() {
  generatePreviewIslands();
  drawMapPreview();
  emitMapSettings();
}

function generatePreviewIslands() {
  // Client-side island generation matching server algorithm
  const { rows, cols, num_islands, island_size } = mapSettings;
  const set = new Set();
  const max = Math.min(num_islands, Math.floor(rows * cols * 0.1));

  for (let i = 0; i < max; i++) {
    let r, c, attempts = 0;
    do {
      r = 1 + Math.floor(Math.random() * (rows - 2));
      c = 1 + Math.floor(Math.random() * (cols - 2));
      attempts++;
    } while (set.has(`${r},${c}`) && attempts < 50);
    if (attempts >= 50) continue;

    let size = 1;
    if (island_size >= 2 && Math.random() < 0.15) {
      size = 2 + Math.floor(Math.random() * (island_size - 1));
    }

    for (let di = 0; di < size; di++) {
      for (let dj = 0; dj < size; dj++) {
        const nr = r + di, nc = c + dj;
        if (nr > 0 && nr < rows - 1 && nc > 0 && nc < cols - 1) {
          if (size >= 3 && (di === 0 || di === size-1) && (dj === 0 || dj === size-1)) {
            if (Math.random() < 0.4) continue;
          }
          set.add(`${nr},${nc}`);
        }
      }
    }
  }

  previewIslands = [...set].map(s => s.split(',').map(Number));
}

function drawMapPreview() {
  const canvas = document.getElementById('map-preview');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const { rows, cols, sector_width, sector_height } = mapSettings;

  const maxW = 300, maxH = 300;
  const cellW = Math.floor(maxW / cols);
  const cellH = Math.floor(maxH / rows);
  const cell = Math.min(cellW, cellH, 20);
  canvas.width = cols * cell;
  canvas.height = rows * cell;

  // Background
  ctx.fillStyle = '#1a1a2e';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  // Grid lines
  ctx.strokeStyle = 'rgba(255,255,255,0.08)';
  ctx.lineWidth = 0.5;
  for (let r = 0; r <= rows; r++) {
    ctx.beginPath(); ctx.moveTo(0, r * cell); ctx.lineTo(cols * cell, r * cell); ctx.stroke();
  }
  for (let c = 0; c <= cols; c++) {
    ctx.beginPath(); ctx.moveTo(c * cell, 0); ctx.lineTo(c * cell, rows * cell); ctx.stroke();
  }

  // Sector boundaries
  ctx.strokeStyle = 'rgba(200,168,50,0.5)';
  ctx.lineWidth = 2;
  for (let sr = 0; sr <= Math.ceil(rows / sector_height); sr++) {
    const y = Math.min(sr * sector_height, rows) * cell;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(cols * cell, y); ctx.stroke();
  }
  for (let sc = 0; sc <= Math.ceil(cols / sector_width); sc++) {
    const x = Math.min(sc * sector_width, cols) * cell;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, rows * cell); ctx.stroke();
  }

  // Sector labels
  ctx.fillStyle = 'rgba(200,168,50,0.6)';
  ctx.font = `${Math.max(cell * 0.8, 10)}px monospace`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  let sNum = 1;
  for (let sr = 0; sr < Math.ceil(rows / sector_height); sr++) {
    for (let sc = 0; sc < Math.ceil(cols / sector_width); sc++) {
      const cx = (sc * sector_width + Math.min(sector_width, cols - sc * sector_width) / 2) * cell;
      const cy = (sr * sector_height + Math.min(sector_height, rows - sr * sector_height) / 2) * cell;
      ctx.fillText(sNum++, cx, cy);
    }
  }

  // Islands
  ctx.fillStyle = '#8B4513';
  previewIslands.forEach(([r, c]) => {
    ctx.fillRect(c * cell + 1, r * cell + 1, cell - 2, cell - 2);
  });
}

function emitMapSettings() {
  if (!isHost) return;
  mapSettings.islands = previewIslands;
  socket.emit('map_settings', { game_id: GAME_ID, name: MY_NAME, settings: mapSettings });
}

// Listen for map settings updates from host
socket.on('map_settings_update', data => {
  if (!isHost) {
    // Non-hosts update their preview from host's settings
    mapSettings = data.settings;
    previewIslands = (data.settings.islands || []);
    // Update slider values and labels
    ['rows','cols'].forEach(k => {
      const sl = document.getElementById('sl-' + (k === 'sector_width' ? 'sw' : k === 'sector_height' ? 'sh' : k));
      if (sl) sl.value = mapSettings[k];
    });
    drawMapPreview();
  }
});
```

In `renderLobby()`, add at the end (before the closing brace):
```javascript
// Show map editor for host
const mapEditor = document.getElementById('map-editor');
if (mapEditor) {
  mapEditor.style.display = isHost ? '' : 'none';
  // Non-hosts could see a read-only preview in the future
}
```

Call `initMapEditor()` at the bottom of the script block (or in a DOMContentLoaded handler).

### Step 5: Update spectator.html and spectator.js for separate sector width/height

**In `spectator.html`** (line 99): Change from single `SECTOR_SZ` to separate width/height:
```javascript
const SECTOR_W = {{ sector_width | default(sector_size) }};
const SECTOR_H = {{ sector_height | default(sector_size) }};
```

**In server `/spectate` route** (server.py ~line 903): Pass both sector dimensions:
```python
return render_template(
    "spectator.html",
    game_id=game_id,
    player_name=name,
    map_rows=map_def["rows"],
    map_cols=map_def["cols"],
    sector_size=map_def.get("sector_size", map_def.get("sector_width", 5)),
    sector_width=map_def.get("sector_width", map_def.get("sector_size", 5)),
    sector_height=map_def.get("sector_height", map_def.get("sector_size", 5)),
    islands=map_def["islands"],
    col_labels=get_col_labels(map_def["cols"]),
)
```

**Do the same for all other play routes** that pass map dimensions (the `/play` route).

**In `spectator.js`**: Update the sector rendering code in `renderMap()` to use `SECTOR_W` and `SECTOR_H` instead of `SECTOR_SZ`:
```javascript
const sPerRow = Math.ceil(MAP_ROWS / SECTOR_H);
const sPerCol = Math.ceil(MAP_COLS / SECTOR_W);
for (let sr = 0; sr < sPerRow; sr++) {
  for (let sc = 0; sc < sPerCol; sc++) {
    const startR = sr * SECTOR_H, startC = sc * SECTOR_W;
    const endR = Math.min(startR + SECTOR_H, MAP_ROWS);
    const endC = Math.min(startC + SECTOR_W, MAP_COLS);
    // ... rest unchanged
  }
}
```

Similarly update any other JS files that use `SECTOR_SZ` for sector rendering (captain.js, radio_operator.js).

### Step 6: Update game_state.py make_game for dynamic maps

Modify `make_game()` in `game_state.py` so it can accept a map dict directly instead of only a map name string. This is the key integration point — when server calls `gs.make_game(map_def)` with a dict, it uses that map directly.

### Step 7: Commit

```bash
git add maps.py server.py game_state.py templates/lobby.html templates/spectator.html static/js/spectator.js
git commit -m "feat: add lobby map editor with equal sector enforcement and live preview"
git push
```

---

## Execution Order

1. **Task 1** (comms cleanup) — smallest, immediate impact
2. **Task 2** (human comms) — builds on cleaned-up comms
3. **Task 3** (spectator eng board) — self-contained CSS/JS
4. **Task 4** (map editor) — largest, most files touched

Commit after each task. Push after Tasks 2 and 4.
