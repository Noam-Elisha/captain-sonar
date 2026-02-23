"""
Captain Sonar – Server-side game state (turn-based mode).

Engineering board layout (Map Alpha standard):
  Each direction section has 6 nodes indexed 0-5.
  Indices 0-2: Circuit nodes (one per circuit C1/C2/C3, span all 4 directions).
  Indices 3-4: Extra non-circuit nodes (block a system when marked).
  Index  5:   Reactor/radiation node.

  Node colors vary by direction (real board layout):
    WEST:  [red/C1,    green/C2,   yellow/C3,  yellow, red,   radiation]
    NORTH: [yellow/C1, red/C2,     yellow/C3,  red,    green, radiation]
    SOUTH: [green/C1,  yellow/C2,  red/C3,     green,  yellow,radiation]
    EAST:  [yellow/C1, red/C2,     green/C3,   yellow, red,   radiation]

  Circuits span all 4 directions (one node per direction per circuit):
    C1 (orange): W[0]=red,    N[0]=yellow, S[0]=green,  E[0]=yellow
    C2 (cyan):   W[1]=green,  N[1]=red,    S[1]=yellow, E[1]=red
    C3 (pink):   W[2]=yellow, N[2]=yellow, S[2]=red,    E[2]=green

  Visual circuit routing (SVG overlay — chosen to avoid line crossovers):
    SOUTH bundle (C3-pink):   S[0]─S[1]─S[2] → E[0] (yellow, adjacent to SOUTH)
    WEST  bundle (C1-orange): W[0]─W[1]─W[2] → E[1] (red,    mid)
    NORTH bundle (C2-cyan):   N[0]─N[1]─N[2] → E[2] (green,  straight down)

  When all 4 nodes of a circuit are marked → they self-clear (no damage).
  When all 6 nodes of one direction section are marked → 1 damage + clear ENTIRE board.
  When all 4 radiation nodes are marked → 1 damage + clear ENTIRE board.

System charge costs (First Mate charges per captain move):
  torpedo : 6   mine : 6   sonar : 6   drone : 6   stealth : 4

Torpedo range: Manhattan distance ≤ 4.
  Direct hit (same cell): 2 damage.   Adjacent (distance 1): 1 damage.

Surface: clear trail + entire engineering board, announce sector.
  No HP cost.  Enemy team gets 3 free turns.
  Captain must DIVE before moving again.

Silence (Stealth): move 0-4 spaces in ONE straight line silently.
Systems: blocked if any node of corresponding color is marked in engineer board.
         A team cannot activate two systems in the same turn.

Blackout: if captain has no valid moves at start of turn, must surface immediately.

Sonar (interactive): activating FM/captain triggers sonar_query to enemy captain.
  Enemy captain must respond with 2 pieces of info (1 true, 1 false, different types).
  The activating team receives the enemy captain's stated info (not server-computed truth).
"""

from maps import get_sector, MAPS
import copy

# ── Engineering board definition ──────────────────────────────────────────────
# Node color → which systems it affects when marked:
#   red    → mine + torpedo
#   green  → sonar + drone
#   yellow → stealth (silence)
#   radiation → reactor (all radiation marked → damage)

ENGINEERING_LAYOUT = {
    "west": [
        {"color": "red",       "circuit": 1},   # 0  mine/torpedo  C1
        {"color": "green",     "circuit": 2},   # 1  sonar/drone   C2
        {"color": "yellow",    "circuit": 3},   # 2  stealth        C3
        {"color": "yellow",    "circuit": None}, # 3  stealth (extra)
        {"color": "red",       "circuit": None}, # 4  mine/torpedo (extra)
        {"color": "radiation", "circuit": None}, # 5  reactor
    ],
    "north": [
        {"color": "yellow",    "circuit": 1},   # 0  stealth         C1
        {"color": "red",       "circuit": 2},   # 1  mine/torpedo    C2
        {"color": "yellow",    "circuit": 3},   # 2  stealth         C3
        {"color": "red",       "circuit": None}, # 3  mine/torpedo (extra)
        {"color": "green",     "circuit": None}, # 4  sonar/drone (extra)
        {"color": "radiation", "circuit": None}, # 5  reactor
    ],
    "south": [
        {"color": "green",     "circuit": 1},   # 0  sonar/drone     C1
        {"color": "yellow",    "circuit": 2},   # 1  stealth         C2
        {"color": "red",       "circuit": 3},   # 2  mine/torpedo    C3
        {"color": "green",     "circuit": None}, # 3  sonar/drone (extra)
        {"color": "yellow",    "circuit": None}, # 4  stealth (extra)
        {"color": "radiation", "circuit": None}, # 5  reactor
    ],
    "east":  [
        {"color": "yellow",    "circuit": 1},   # 0  stealth         C1
        {"color": "red",       "circuit": 2},   # 1  mine/torpedo    C2
        {"color": "green",     "circuit": 3},   # 2  sonar/drone     C3
        {"color": "yellow",    "circuit": None}, # 3  stealth (extra)
        {"color": "red",       "circuit": None}, # 4  mine/torpedo (extra)
        {"color": "radiation", "circuit": None}, # 5  reactor
    ],
}

# circuit_id → list of (direction, index) pairs – each circuit spans all 4 directions
# Node colors vary per direction (see ENGINEERING_LAYOUT above).
CIRCUITS = {
    1: [("north", 0), ("south", 0), ("east", 0), ("west", 0)],  # C1: N=yellow, S=green,  E=yellow, W=red
    2: [("north", 1), ("south", 1), ("east", 1), ("west", 1)],  # C2: N=red,    S=yellow, E=red,    W=green
    3: [("north", 2), ("south", 2), ("east", 2), ("west", 2)],  # C3: N=yellow, S=red,    E=green,  W=yellow
}

# Radiation node positions (one per direction = 4 total)
RADIATION_NODES = [
    ("west", 5), ("north", 5), ("south", 5), ("east", 5),
]

# Node color → systems it blocks when marked
SYSTEM_COLORS = {
    "torpedo": "red",
    "mine":    "red",
    "sonar":   "green",
    "drone":   "green",
    "stealth": "yellow",
}

SYSTEM_MAX_CHARGE = {
    "torpedo": 6,
    "mine":    6,
    "sonar":   6,
    "drone":   6,
    "stealth": 4,
}

TEAMS = ["blue", "red"]
ROLES = ["captain", "first_mate", "engineer", "radio_operator"]


# ── Engineering Board ──────────────────────────────────────────────────────────

def make_engineering_board():
    """Return a fresh engineering board state dict."""
    board = {}
    for direction, nodes in ENGINEERING_LAYOUT.items():
        board[direction] = [
            {"color": n["color"], "circuit": n["circuit"], "marked": False}
            for n in nodes
        ]
    return board


def clear_engineering_board(board):
    """Clear ALL marked nodes on the entire engineering board."""
    for dir_nodes in board.values():
        for node in dir_nodes:
            node["marked"] = False


def engineer_mark_node(board, direction, index):
    """
    Mark node at (direction, index).
    Returns a list of events: [{"type": ..., ...}]

    RULEBOOK check order (priority high → low):
    1. Direction overload: all 6 nodes in one section marked → 1 damage + clear ENTIRE board.
       Overload fires BEFORE circuit repair — filling the section always causes damage.
       Circuit repair is a preventive mechanic (avoid filling the section), not a reactive one.
    2. Radiation overload: all 4 radiation nodes marked → 1 damage + clear ENTIRE board.
    3. Circuit completed (C1/C2/C3): clear those 4 nodes only, no damage.
    """
    if board[direction][index]["marked"]:
        return [{"type": "error", "msg": "Node already marked"}]

    board[direction][index]["marked"] = True
    events = []

    # ── 1. Direction overload (checked FIRST per rulebook priority) ───────────
    # If all 6 nodes in this section are now marked, the section is overloaded.
    # This takes priority over circuit completion — filling the board always damages.
    if all(n["marked"] for n in board[direction]):
        clear_engineering_board(board)
        events.append({"type": "direction_damage", "direction": direction, "damage": 1})
        return events   # overload fired; radiation / circuit checks moot after full clear

    # ── 2. Radiation overload ─────────────────────────────────────────────────
    total_radiation = sum(
        1 for d, i in RADIATION_NODES if board[d][i]["marked"]
    )
    if total_radiation >= len(RADIATION_NODES):
        clear_engineering_board(board)
        events.append({"type": "radiation_damage", "damage": 1})
        return events   # circuit check moot after full clear

    # ── 3. Circuit completion (self-repair, no damage) ────────────────────────
    circuit_id = board[direction][index]["circuit"]
    if circuit_id is not None:
        circuit_nodes = CIRCUITS[circuit_id]
        if all(board[d][i]["marked"] for d, i in circuit_nodes):
            for d, i in circuit_nodes:
                board[d][i]["marked"] = False
            events.append({"type": "circuit_cleared", "circuit": circuit_id})

    return events


def get_available_nodes(board, direction):
    """Return indices of unmarked nodes in the given direction."""
    return [i for i, n in enumerate(board[direction]) if not n["marked"]]


def is_system_blocked(board, system):
    """Return True if any engineer node for this system is currently marked."""
    target_color = SYSTEM_COLORS.get(system)
    if not target_color:
        return False
    for direction, nodes in board.items():
        for node in nodes:
            if node["color"] == target_color and node["marked"]:
                return True
    return False


# ── Submarine State ────────────────────────────────────────────────────────────

def make_submarine(team):
    return {
        "team":     team,
        "position": None,        # [row, col] or None
        "health":   4,
        "trail":    [],           # list of [row, col] visited (excluding current position trail lines)
        "mines":    [],           # list of [row, col]
        "systems":  {sys: 0 for sys in SYSTEM_MAX_CHARGE},
        "engineering": make_engineering_board(),
        "surfaced": False,        # True while surfacing (not yet dived)
    }


# ── Game State ─────────────────────────────────────────────────────────────────

def make_game(map_key="alpha"):
    map_def = MAPS[map_key]
    island_set = set(tuple(p) for p in map_def["islands"])
    return {
        "map_key":    map_key,
        "map":        map_def,
        "island_set": island_set,
        "phase":      "placement",   # placement | playing | ended
        "turn_index": 0,
        "turn_order": ["blue", "red"],
        "active_team": "blue",       # explicit active team (handles surface bonus turns)
        "surface_bonus": None,        # None | {"for_team": team, "turns_remaining": int}
        "submarines": {
            "blue": make_submarine("blue"),
            "red":  make_submarine("red"),
        },
        "turn_state": make_turn_state(),
        "log":        [],
        "winner":     None,
        "pending":    {},   # pending sonar/drone queries
    }


def make_turn_state():
    return {
        "moved":             False,   # captain has moved/surfaced this turn
        "direction":         None,    # direction chosen this turn (None for stealth/surface)
        "stealth_direction": None,    # private stealth direction (only revealed to own team)
        "engineer_done":     False,
        "first_mate_done":   False,
        "waiting_for":       None,    # None | "sonar_response" | "drone_response"
        "sonar_query":       None,    # {row, col, sector} asked values
        "drone_query":       None,    # {sector} asked value
        "system_used":       False,   # a system was already activated this turn
    }


def current_team(game):
    """Return the team whose turn it currently is."""
    return game["active_team"]


def other_team(team):
    return "red" if team == "blue" else "blue"


def is_valid_position(game, row, col):
    map_def = game["map"]
    if row < 0 or row >= map_def["rows"]:
        return False
    if col < 0 or col >= map_def["cols"]:
        return False
    if (row, col) in game["island_set"]:
        return False
    return True


def direction_delta(direction):
    return {"north": (-1, 0), "south": (1, 0), "west": (0, -1), "east": (0, 1)}[direction]


# ── Placement ─────────────────────────────────────────────────────────────────

def place_submarine(game, team, row, col):
    """Place a submarine. Returns (ok, error_msg)."""
    if game["phase"] != "placement":
        return False, "Game not in placement phase"
    sub = game["submarines"][team]
    if sub["position"] is not None:
        return False, "Already placed"
    if not is_valid_position(game, row, col):
        return False, "Invalid position"
    sub["position"] = [row, col]
    sub["trail"] = [[row, col]]
    game["log"].append({"type": "placed", "team": team, "row": row, "col": col})
    # Check if both placed
    if all(game["submarines"][t]["position"] is not None for t in TEAMS):
        game["phase"] = "playing"
    return True, None


# ── Movement ──────────────────────────────────────────────────────────────────

def captain_move(game, team, direction):
    """Move the submarine. Returns (ok, error_msg, events)."""
    if game["phase"] != "playing":
        return False, "Game not active", []
    if current_team(game) != team:
        return False, "Not your turn", []
    if game["turn_state"]["moved"]:
        return False, "Already moved this turn", []
    if game["turn_state"]["waiting_for"]:
        return False, "Waiting for a response", []

    sub = game["submarines"][team]
    if sub["surfaced"]:
        return False, "Submarine is surfaced – press DIVE first", []

    dr, dc = direction_delta(direction)
    r, c = sub["position"]
    nr, nc = r + dr, c + dc

    if not is_valid_position(game, nr, nc):
        return False, "Invalid move (boundary or island)", []

    # Can't revisit – trail includes starting position
    if [nr, nc] in sub["trail"]:
        return False, "Cannot revisit a cell (you've been there before)", []

    # RULEBOOK: Cannot move into own mine
    if [nr, nc] in sub["mines"]:
        return False, "Cannot move into own mine", []

    # Move
    sub["position"] = [nr, nc]
    sub["trail"].append([nr, nc])
    game["turn_state"]["moved"] = True
    game["turn_state"]["direction"] = direction

    events = [{"type": "moved", "team": team, "direction": direction, "row": nr, "col": nc}]
    game["log"].append({"type": "move", "team": team, "direction": direction})
    return True, None, events


def captain_surface(game, team):
    """
    Surface the submarine. Returns (ok, error_msg, events).

    RULEBOOK (TBT mode):
    - Surfacing does NOT cost HP (rulebook TBT: only enemy bonus turns, no damage).
    - Clears trail (keeps current position) + clears ENTIRE engineering board.
    - Announces sector to all.
    - Enemy team gets 3 free turns (surface bonus).
    """
    if game["phase"] != "playing":
        return False, "Game not active", []
    if current_team(game) != team:
        return False, "Not your turn", []
    if game["turn_state"]["moved"]:
        return False, "Already acted this turn", []
    if game["turn_state"]["waiting_for"]:
        return False, "Waiting for a response", []

    sub = game["submarines"][team]
    r, c = sub["position"]
    sector = get_sector(r, c, game["map"]["sector_size"], game["map"]["cols"])

    # RULEBOOK: surfacing does NOT cost HP
    sub["trail"] = [[r, c]]   # clear trail (keep current position)
    sub["surfaced"] = True

    # RULEBOOK: clear entire engineering board when surfacing
    clear_engineering_board(sub["engineering"])

    # RULEBOOK: enemy team gets 3 bonus turns after surfacing
    enemy = other_team(team)
    game["surface_bonus"] = {"for_team": enemy, "turns_remaining": 3}

    events = [
        {"type": "surfaced", "team": team, "sector": sector, "health": sub["health"]},
    ]
    game["log"].append({"type": "surface", "team": team, "sector": sector})

    game["turn_state"]["moved"] = True
    game["turn_state"]["direction"] = None
    game["turn_state"]["engineer_done"] = True   # no engineering needed when surfacing
    game["turn_state"]["first_mate_done"] = True  # no charging when surfacing

    return True, None, events


def captain_dive(game, team):
    """Dive after surfacing. Must be this team's turn."""
    if current_team(game) != team:
        return False, "Not your turn"
    sub = game["submarines"][team]
    if not sub["surfaced"]:
        return False, "Not surfaced"
    sub["surfaced"] = False
    return True, None


# ── Engineer ──────────────────────────────────────────────────────────────────

def engineer_mark(game, team, direction, index):
    """Mark an engineering node. Returns (ok, error_msg, events, damage).
    RULEBOOK stealth: engineer must mark one node in the stealth direction (private)."""
    if current_team(game) != team:
        return False, "Not your turn", [], 0
    if not game["turn_state"]["moved"]:
        return False, "Captain hasn't moved yet", [], 0
    ts = game["turn_state"]
    # Determine required direction (public move direction, or private stealth direction)
    effective_dir = ts["direction"] if ts["direction"] is not None else ts.get("stealth_direction")
    if effective_dir is None:
        return False, "No direction to mark (submarine surfaced)", [], 0
    if ts["engineer_done"]:
        return False, "Already marked this turn", [], 0
    if direction != effective_dir:
        return False, f"Must mark in the {effective_dir} section", [], 0

    board = game["submarines"][team]["engineering"]
    if board[direction][index]["marked"]:
        return False, "Node already marked", [], 0

    eng_events = engineer_mark_node(board, direction, index)
    game["turn_state"]["engineer_done"] = True

    total_damage = 0
    out_events = []
    sub = game["submarines"][team]

    for ev in eng_events:
        if ev["type"] in ("radiation_damage", "direction_damage"):
            dmg = ev["damage"]
            sub["health"] -= dmg
            total_damage += dmg
            out_events.append({"type": "engineering_damage", "team": team,
                                "cause": ev["type"], "damage": dmg, "health": sub["health"],
                                "direction": ev.get("direction")})
        else:
            out_events.append({"type": "circuit_cleared", "team": team, "circuit": ev.get("circuit")})

    result = _check_game_over(game)
    if result:
        out_events.append(result)

    return True, None, out_events, total_damage


# ── First Mate ────────────────────────────────────────────────────────────────

def first_mate_charge(game, team, system):
    """Charge a system. Returns (ok, error_msg, events).
    RULEBOOK stealth: FM still charges one system on a stealth move."""
    if current_team(game) != team:
        return False, "Not your turn", []
    if not game["turn_state"]["moved"]:
        return False, "Captain hasn't moved yet", []
    ts = game["turn_state"]
    # Allow charging on normal moves AND stealth moves (not on surface)
    effective_dir = ts["direction"] if ts["direction"] is not None else ts.get("stealth_direction")
    if effective_dir is None:
        return False, "No charging when surfacing", []
    if ts["first_mate_done"]:
        return False, "Already charged this turn", []
    if system not in SYSTEM_MAX_CHARGE:
        return False, f"Unknown system: {system}", []

    sub = game["submarines"][team]
    max_c = SYSTEM_MAX_CHARGE[system]
    current = sub["systems"][system]

    if current >= max_c:
        return False, f"{system} already fully charged", []

    sub["systems"][system] = current + 1
    game["turn_state"]["first_mate_done"] = True

    new_val = sub["systems"][system]
    events = [{"type": "system_charged", "team": team, "system": system,
               "charge": new_val, "max": max_c, "ready": new_val >= max_c}]
    return True, None, events


# ── Weapons & Systems ─────────────────────────────────────────────────────────

def _check_charge(sub, system):
    return sub["systems"][system] >= SYSTEM_MAX_CHARGE[system]


def _use_system(sub, system):
    sub["systems"][system] = 0


def has_valid_move(game, team):
    """Return True if the submarine has at least one legal direction to move."""
    sub = game["submarines"][team]
    r, c = sub["position"]
    for direction in ("north", "south", "east", "west"):
        dr, dc = direction_delta(direction)
        nr, nc = r + dr, c + dc
        if is_valid_position(game, nr, nc) and [nr, nc] not in sub["trail"] and [nr, nc] not in sub["mines"]:
            return True
    return False


def captain_fire_torpedo(game, team, target_row, target_col):
    """Fire a torpedo. Returns (ok, error_msg, events).
    If system unavailable: takes 1 damage instead of firing.
    RULEBOOK: torpedo destroys (without exploding) any mine at the impact cell.
    RULEBOOK TBT: systems activate AFTER the captain announces a course (moved=True)."""
    if current_team(game) != team:
        return False, "Not your turn", []
    if game["phase"] != "playing":
        return False, "Game not active", []
    if not game["turn_state"]["moved"]:
        return False, "Must announce a course before firing torpedo", []
    if game["turn_state"]["system_used"]:
        return False, "Already used a system this turn", []
    if not is_valid_position(game, target_row, target_col):
        return False, "Invalid target", []

    sub = game["submarines"][team]
    r, c = sub["position"]
    dist = abs(target_row - r) + abs(target_col - c)
    if dist > 4 or dist == 0:
        return False, "Torpedo range: 1–4 spaces (Manhattan distance)", []

    # RULEBOOK: "Confirm with the Engineer that there are no breakdowns affecting the system.
    # If there are, they must be repaired before the system can be activated."
    # Simply reject — do NOT deal damage for attempting to use an unavailable system.
    if not _check_charge(sub, "torpedo"):
        return False, "Torpedo not fully charged yet", []
    if is_system_blocked(sub["engineering"], "torpedo"):
        return False, "Torpedo blocked by engineer breakdown (red node marked)", []

    _use_system(sub, "torpedo")
    game["turn_state"]["system_used"] = True

    # RULEBOOK: torpedo destroys (without exploding) any mine at the impact cell
    for t, s in game["submarines"].items():
        before = len(s["mines"])
        s["mines"] = [m for m in s["mines"] if m != [target_row, target_col]]
        if len(s["mines"]) < before:
            game["log"].append({"type": "mine_destroyed_by_torpedo",
                                 "team": t, "row": target_row, "col": target_col})

    events = [{"type": "torpedo_fired", "team": team, "row": target_row, "col": target_col}]
    events += _apply_explosion(game, team, target_row, target_col)
    game["log"].append({"type": "torpedo", "team": team, "row": target_row, "col": target_col})
    return True, None, events


def captain_place_mine(game, team, target_row, target_col):
    """Place a mine on a cardinally adjacent cell. Returns (ok, error_msg, events).
    RULEBOOK: 'adjacent' means N/S/E/W only (Manhattan distance 1).
    RULEBOOK TBT: systems activate AFTER the captain announces a course (moved=True)."""
    if current_team(game) != team:
        return False, "Not your turn", []
    if not game["turn_state"]["moved"]:
        return False, "Must announce a course before placing a mine", []
    if game["turn_state"]["system_used"]:
        return False, "Already used a system this turn", []
    if not is_valid_position(game, target_row, target_col):
        return False, "Invalid target", []

    sub = game["submarines"][team]
    r, c = sub["position"]
    manhattan_dist = abs(target_row - r) + abs(target_col - c)
    if manhattan_dist != 1:   # RULEBOOK: cardinal adjacency only (N/S/E/W)
        return False, "Mine must be placed in a cardinally adjacent cell (N/S/E/W only)", []

    # Can't place on route (trail lines) – rulebook explicit
    if [target_row, target_col] in sub["trail"]:
        return False, "Cannot place mine on a cell already in your route", []

    # RULEBOOK: "must be repaired before the system can be activated" — reject, no damage.
    if not _check_charge(sub, "mine"):
        return False, "Mine system not fully charged yet", []
    if is_system_blocked(sub["engineering"], "mine"):
        return False, "Mine system blocked by engineer breakdown (red node marked)", []

    _use_system(sub, "mine")
    game["turn_state"]["system_used"] = True
    sub["mines"].append([target_row, target_col])
    events = [{"type": "mine_placed", "team": team}]
    game["log"].append({"type": "mine_placed", "team": team})
    return True, None, events


def captain_detonate_mine(game, team, mine_index):
    """Detonate one of the team's own mines. Returns (ok, error_msg, events).
    RULEBOOK: can only detonate on own turn; cannot detonate while surfaced.
    Like all captain actions, cannot be taken while waiting for a sonar response."""
    if game["phase"] != "playing":
        return False, "Game not active", []
    if current_team(game) != team:
        return False, "Not your turn", []
    if game["turn_state"]["waiting_for"]:
        return False, "Cannot detonate mine while waiting for a response", []
    sub = game["submarines"][team]
    # RULEBOOK: "At any time, except while surfaced, the Captain can trigger a mine"
    if sub["surfaced"]:
        return False, "Cannot trigger a mine while surfaced", []
    if mine_index < 0 or mine_index >= len(sub["mines"]):
        return False, "Invalid mine index", []

    mine = sub["mines"].pop(mine_index)
    events = [{"type": "mine_detonated", "team": team, "row": mine[0], "col": mine[1]}]
    events += _apply_explosion(game, team, mine[0], mine[1])
    game["log"].append({"type": "mine_detonated", "team": team, "row": mine[0], "col": mine[1]})
    return True, None, events


def _apply_explosion(game, firing_team, target_row, target_col):
    """Apply torpedo/mine explosion damage. Friendly fire included.
    RULEBOOK: damage radius uses Chebyshev distance (includes diagonals).
      Direct hit (same cell):       2 damage
      Adjacent (Chebyshev dist 1):  1 damage  ← includes 8 surrounding cells
    Rulebook example: mine at B7, sub at C6 (diagonally adjacent) → indirect hit.
    """
    events = []
    for team, sub in game["submarines"].items():
        # RULEBOOK edge case: if a prior damage in this same explosion already ended
        # the game, stop — don't emit a second (contradictory) game_over event.
        if game["phase"] == "ended":
            break
        if sub["position"] is None:
            continue
        r, c = sub["position"]
        # RULEBOOK: use Chebyshev distance (max of row-diff and col-diff)
        # so all 8 surrounding cells are within distance 1 (not just N/S/E/W)
        dist = max(abs(target_row - r), abs(target_col - c))
        if dist == 0:
            dmg = 2
        elif dist == 1:
            dmg = 1
        else:
            continue
        sub["health"] -= dmg
        events.append({"type": "damage", "team": team, "amount": dmg,
                        "health": sub["health"], "cause": "explosion",
                        "row": target_row, "col": target_col})
        result = _check_game_over(game)
        if result:
            events.append(result)
    return events


# ── Sonar (interactive) ────────────────────────────────────────────────────────

def captain_use_sonar(game, team):
    """
    Activate sonar. Sets waiting_for='sonar_response' so enemy captain must respond.
    RULEBOOK: enemy captain gives 2 pieces of info (1 true, 1 false, different types).
    The activating team sees the enemy's stated info (NOT server-computed truth).
    RULEBOOK TBT: systems activate AFTER the captain announces a course (moved=True).
    Returns (ok, error_msg, events)
    """
    if current_team(game) != team:
        return False, "Not your turn", []
    if not game["turn_state"]["moved"]:
        return False, "Must announce a course before activating sonar", []
    if game["turn_state"]["system_used"]:
        return False, "Already used a system this turn", []
    sub = game["submarines"][team]
    if not _check_charge(sub, "sonar"):
        return False, "Sonar not charged", []
    if is_system_blocked(sub["engineering"], "sonar"):
        return False, "Sonar blocked by engineer breakdown (green nodes marked)", []

    _use_system(sub, "sonar")
    game["turn_state"]["system_used"] = True
    game["turn_state"]["waiting_for"] = "sonar_response"

    # NOTE: sonar_announced is broadcast by the server's sonar_activated handler.
    # Do NOT add a separate sonar_announced event here — that would cause a duplicate.
    events = [
        {"type": "sonar_activated", "team": team},
    ]
    game["log"].append({"type": "sonar", "team": team})
    return True, None, events


def captain_respond_sonar(game, responding_team, type1, val1, type2, val2):
    """
    Enemy captain responds to sonar query.
    RULEBOOK: must give 2 different types (row/col/sector), exactly 1 true and 1 false.
    responding_team: the team that is responding (NOT the activating team).
    Returns (ok, error_msg, events)
    """
    activating_team = other_team(responding_team)

    if game["turn_state"]["waiting_for"] != "sonar_response":
        return False, "No sonar query is pending", []
    if current_team(game) != activating_team:
        return False, "Sonar query is not active", []

    # Validate types
    valid_types = {"row", "col", "sector"}
    if type1 not in valid_types or type2 not in valid_types:
        return False, "Invalid type (must be 'row', 'col', or 'sector')", []
    if type1 == type2:
        return False, "Both pieces of info must be different types (e.g. one row and one sector)", []

    # Determine truth using responding team's actual position
    enemy_sub = game["submarines"][responding_team]
    er, ec = enemy_sub["position"]
    map_def = game["map"]
    actual_sector = get_sector(er, ec, map_def["sector_size"], map_def["cols"])

    def is_true(t, v):
        if t == "row":    return er == v
        if t == "col":    return ec == v
        if t == "sector": return actual_sector == v
        return False

    truth1 = is_true(type1, val1)
    truth2 = is_true(type2, val2)

    # Exactly 1 must be true, 1 must be false
    if truth1 and truth2:
        return False, "Both pieces of information are true — exactly 1 must be true and 1 false", []
    if not truth1 and not truth2:
        return False, "Both pieces of information are false — exactly 1 must be true and 1 false", []

    game["turn_state"]["waiting_for"] = None

    events = [
        {"type": "sonar_result",
         "target": activating_team,
         "type1": type1, "val1": val1,
         "type2": type2, "val2": val2},
    ]
    return True, None, events


def captain_use_drone(game, team, ask_sector):
    """
    Use drone: ask if enemy is in a sector.
    RULEBOOK TBT: systems activate AFTER the captain announces a course (moved=True).
    Returns (ok, error_msg, events)
    """
    if current_team(game) != team:
        return False, "Not your turn", []
    if not game["turn_state"]["moved"]:
        return False, "Must announce a course before launching drone", []
    if game["turn_state"]["system_used"]:
        return False, "Already used a system this turn", []
    sub = game["submarines"][team]
    if not _check_charge(sub, "drone"):
        return False, "Drone not charged", []
    if is_system_blocked(sub["engineering"], "drone"):
        return False, "Drone blocked by engineer breakdown (green nodes marked)", []

    enemy_team = other_team(team)
    enemy_sub = game["submarines"][enemy_team]
    er, ec = enemy_sub["position"]
    map_def = game["map"]
    sector_size = map_def["sector_size"]
    actual_sector = get_sector(er, ec, sector_size, map_def["cols"])

    in_sector = (actual_sector == ask_sector)

    _use_system(sub, "drone")
    game["turn_state"]["system_used"] = True
    events = [
        {"type": "drone_used", "team": team, "ask_sector": ask_sector},
        {"type": "drone_result", "target": team, "in_sector": in_sector, "ask_sector": ask_sector},
        {"type": "drone_announced", "team": team, "sector": ask_sector},
    ]
    game["log"].append({"type": "drone", "team": team})
    return True, None, events


def captain_use_stealth(game, team, direction, steps):
    """
    Use stealth (Silence): move 0-4 cells in a STRAIGHT LINE silently.
    Rulebook: "moves his submarine up to four spaces in a straight line."
    direction: one of 'north'/'south'/'east'/'west'
    steps: integer 0-4 (how many spaces to move in that direction)
    If system unavailable: takes 1 damage instead of moving.
    Returns (ok, error_msg, events)
    """
    if current_team(game) != team:
        return False, "Not your turn", []
    if game["turn_state"]["moved"]:
        return False, "Already moved this turn", []
    if game["turn_state"]["system_used"]:
        return False, "Already used a system this turn", []
    if direction not in ("north", "south", "east", "west"):
        return False, f"Invalid direction: {direction}", []
    if not isinstance(steps, int) or steps < 0 or steps > 4:
        return False, "Stealth: steps must be 0–4", []

    sub = game["submarines"][team]

    # RULEBOOK: "must be repaired before the system can be activated" — reject, no damage.
    # If stealth isn't available the captain must make a normal move or surface instead.
    if not _check_charge(sub, "stealth"):
        return False, "Stealth (Silence) system not fully charged yet", []
    if is_system_blocked(sub["engineering"], "stealth"):
        return False, "Stealth blocked by engineer breakdown (yellow node marked)", []

    # Validate straight-line path
    r, c = sub["position"]
    visited = set(tuple(pos) for pos in sub["trail"])
    mines_set = set(tuple(m) for m in sub["mines"])
    path = []
    dr, dc = direction_delta(direction)
    for _ in range(steps):
        r, c = r + dr, c + dc
        if not is_valid_position(game, r, c):
            return False, "Invalid move during stealth (boundary or island)", []
        if (r, c) in visited:
            return False, "Cannot revisit a cell during stealth", []
        if (r, c) in mines_set:
            return False, "Cannot move into own mine during stealth", []
        visited.add((r, c))
        path.append([r, c])

    # Apply moves
    _use_system(sub, "stealth")
    game["turn_state"]["system_used"] = True
    for pos in path:
        sub["position"] = pos
        sub["trail"].append(pos)

    game["turn_state"]["moved"] = True
    game["turn_state"]["direction"] = None           # public direction stays hidden
    game["turn_state"]["stealth_direction"] = direction  # private — only own team knows
    # RULEBOOK: engineer still marks 1 node in the stealth direction,
    # and FM still charges 1 system on a stealth move.
    # Do NOT set engineer_done or first_mate_done — they must still act.

    events = [
        {"type": "stealth_used", "team": team, "steps": steps, "direction": direction},
        {"type": "moved_private", "team": team,
         "row": sub["position"][0], "col": sub["position"][1]},
    ]
    game["log"].append({"type": "stealth", "team": team, "steps": steps})
    return True, None, events


# ── Turn management ───────────────────────────────────────────────────────────

def can_end_turn(game, team):
    """Check if the active captain can end their turn."""
    ts = game["turn_state"]
    if current_team(game) != team:
        return False, "Not your turn"
    if not ts["moved"]:
        return False, "Must move or surface before ending turn"
    if ts["waiting_for"]:
        return False, "Waiting for a response"
    # When a directional move OR stealth was used, engineer AND first mate must act first.
    # (Surface auto-sets both done flags so the check below is always satisfied there.)
    has_direction = ts["direction"] is not None or ts.get("stealth_direction") is not None
    if has_direction:
        if not ts["engineer_done"]:
            return False, "Waiting for engineer to mark a node"
        if not ts["first_mate_done"]:
            # Exception: if ALL systems are fully charged, FM has nothing to charge.
            # The rulebook only requires FM to mark a space when one is available.
            sub = game["submarines"][team]
            all_full = all(
                sub["systems"][s] >= SYSTEM_MAX_CHARGE[s] for s in SYSTEM_MAX_CHARGE
            )
            if not all_full:
                return False, "Waiting for first mate to charge a system"
    return True, None


def end_turn(game, team):
    """
    End the active team's turn. Returns (ok, error_msg, events).

    RULEBOOK surface bonus: when a team surfaces, the other team gets 3 free turns.
    surface_bonus = {"for_team": X, "turns_remaining": N}
    - While turns_remaining > 0 and active_team == bonus team: stay on bonus team.
    - When bonus exhausted: switch to the other (surfaced) team.
    """
    ok, msg = can_end_turn(game, team)
    if not ok:
        return False, msg, []

    game["turn_index"] += 1

    sb = game.get("surface_bonus")
    if sb is not None:
        if sb["for_team"] == team:
            # Bonus team just played one of their bonus turns
            sb["turns_remaining"] -= 1
            if sb["turns_remaining"] <= 0:
                # Bonus exhausted — switch back to the surfaced team
                game["surface_bonus"] = None
                game["active_team"] = other_team(team)
            # else: bonus team continues (active_team stays the same)
        else:
            # The surfaced team ended their (surface) turn — give bonus to bonus team
            game["active_team"] = sb["for_team"]
    else:
        # Normal turn switch
        game["active_team"] = other_team(team)

    game["turn_state"] = make_turn_state()
    next_t = game["active_team"]
    events = [{"type": "turn_end", "team": team},
              {"type": "turn_start", "team": next_t}]
    return True, None, events


# ── Game over check ───────────────────────────────────────────────────────────

def _check_game_over(game):
    for team, sub in game["submarines"].items():
        if sub["health"] <= 0:
            game["phase"] = "ended"
            winner = "red" if team == "blue" else "blue"
            game["winner"] = winner
            return {"type": "game_over", "winner": winner, "loser": team}
    return None


# ── Serialisation helpers ─────────────────────────────────────────────────────

def serialize_game(game, perspective_team=None):
    """
    Serialize game state for sending to a client.
    If perspective_team is set, hide the OTHER team's exact position (only sector visible).
    """
    map_def = game["map"]
    sector_size = map_def["sector_size"]

    subs = {}
    for team, sub in game["submarines"].items():
        is_own = (perspective_team == team)
        s = {
            "team":      team,
            "health":    sub["health"],
            "surfaced":  sub["surfaced"],
            "systems":   {k: {"charge": v, "max": SYSTEM_MAX_CHARGE[k], "ready": v >= SYSTEM_MAX_CHARGE[k]}
                          for k, v in sub["systems"].items()},
            "mine_count": len(sub["mines"]),
        }
        if is_own or perspective_team is None:
            s["position"] = sub["position"]
            s["trail"]    = sub["trail"]
            s["mines"]    = sub["mines"]
            s["engineering"] = sub["engineering"]
        else:
            # Hide exact position; only reveal sector when surfaced
            if sub["surfaced"] and sub["position"]:
                r, c = sub["position"]
                s["sector"] = get_sector(r, c, sector_size, map_def["cols"])
            s["position"] = None
            s["trail"]    = None
            s["mines"]    = None
            s["engineering"] = None
        subs[team] = s

    # Build turn_state, hiding stealth_direction from the enemy team
    ts = game["turn_state"]
    if perspective_team is not None and perspective_team != current_team(game) and game["phase"] == "playing":
        # Enemy team should not see the stealth direction
        ts = dict(ts)
        ts["stealth_direction"] = None

    return {
        "phase":         game["phase"],
        "turn_index":    game["turn_index"],
        "current_team":  current_team(game) if game["phase"] == "playing" else None,
        "active_team":   game.get("active_team"),
        "surface_bonus": game.get("surface_bonus"),
        "turn_order":    game["turn_order"],
        "turn_state":    ts,
        "submarines":    subs,
        "winner":        game["winner"],
        "map": {
            "rows":        map_def["rows"],
            "cols":        map_def["cols"],
            "sector_size": map_def["sector_size"],
            "islands":     map_def["islands"],
            "name":        map_def["name"],
        },
    }
