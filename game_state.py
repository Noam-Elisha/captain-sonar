"""
Captain Sonar – Server-side game state (turn-based mode).

Engineering board layout (Map Alpha standard):
  Each direction section has 6 nodes indexed 0-5.
  Circuits span across directions – when all nodes in a circuit are marked, they clear (no damage).
  Filling an entire direction section → 1 hull damage + clear that section.
  Filling all 6 radiation nodes (across all sections) → 1 hull damage + clear radiation.

  WEST  : [yellow/c1, red/c1, green/c1, green, radiation, radiation]
  NORTH : [red/c2, red, green, yellow/c2, yellow/c2, radiation]
  SOUTH : [red/c3, red, green/c3, yellow/c3, yellow, radiation]
  EAST  : [yellow/c3, red/c1, green/c2, green, radiation, radiation]

System charge costs (First Mate charges per captain move):
  torpedo : 3   mine : 3   sonar : 3   drone : 4   stealth : 5

Torpedo range: Manhattan distance ≤ 4.
  Direct hit (same cell): 2 damage.   Adjacent (distance 1): 1 damage.

Surface: clear trail, take 1 damage, announce sector.
"""

from maps import get_sector, MAPS
import copy

# ── Engineering board definition ──────────────────────────────────────────────

ENGINEERING_LAYOUT = {
    "west":  [
        {"color": "yellow",    "circuit": 1},
        {"color": "red",       "circuit": 1},
        {"color": "green",     "circuit": 1},
        {"color": "green",     "circuit": None},
        {"color": "radiation", "circuit": None},
        {"color": "radiation", "circuit": None},
    ],
    "north": [
        {"color": "red",       "circuit": 2},
        {"color": "red",       "circuit": None},
        {"color": "green",     "circuit": None},
        {"color": "yellow",    "circuit": 2},
        {"color": "yellow",    "circuit": 2},
        {"color": "radiation", "circuit": None},
    ],
    "south": [
        {"color": "red",       "circuit": 3},
        {"color": "red",       "circuit": None},
        {"color": "green",     "circuit": 3},
        {"color": "yellow",    "circuit": 3},
        {"color": "yellow",    "circuit": None},
        {"color": "radiation", "circuit": None},
    ],
    "east":  [
        {"color": "yellow",    "circuit": 3},
        {"color": "red",       "circuit": 1},
        {"color": "green",     "circuit": 2},
        {"color": "green",     "circuit": None},
        {"color": "radiation", "circuit": None},
        {"color": "radiation", "circuit": None},
    ],
}

# circuit_id → list of (direction, index) pairs
CIRCUITS = {
    1: [("west", 0), ("west", 1), ("west", 2), ("east", 1)],
    2: [("north", 0), ("north", 3), ("north", 4), ("east", 2)],
    3: [("south", 0), ("south", 2), ("south", 3), ("east", 0)],
}

# Radiation node positions
RADIATION_NODES = [
    ("west", 4), ("west", 5),
    ("north", 5),
    ("south", 5),
    ("east", 4), ("east", 5),
]

SYSTEM_MAX_CHARGE = {
    "torpedo": 3,
    "mine":    3,
    "sonar":   3,
    "drone":   4,
    "stealth": 5,
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


def engineer_mark_node(board, direction, index):
    """
    Mark node at (direction, index).
    Returns a list of events: [{"type": ..., ...}]
    """
    if board[direction][index]["marked"]:
        return [{"type": "error", "msg": "Node already marked"}]

    board[direction][index]["marked"] = True
    events = []

    # Check circuits first (circuit completion clears nodes, no damage)
    circuit_id = board[direction][index]["circuit"]
    if circuit_id is not None:
        circuit_nodes = CIRCUITS[circuit_id]
        if all(board[d][i]["marked"] for d, i in circuit_nodes):
            for d, i in circuit_nodes:
                board[d][i]["marked"] = False
            events.append({"type": "circuit_cleared", "circuit": circuit_id})

    # Check radiation (after circuit processing)
    total_radiation = sum(
        1 for d, i in RADIATION_NODES if board[d][i]["marked"]
    )
    if total_radiation >= len(RADIATION_NODES):
        for d, i in RADIATION_NODES:
            board[d][i]["marked"] = False
        events.append({"type": "radiation_damage", "damage": 1})

    # Check direction overload (all 6 nodes filled → damage + clear)
    if all(n["marked"] for n in board[direction]):
        for n in board[direction]:
            n["marked"] = False
        events.append({"type": "direction_damage", "direction": direction, "damage": 1})

    return events


def get_available_nodes(board, direction):
    """Return indices of unmarked nodes in the given direction."""
    return [i for i, n in enumerate(board[direction]) if not n["marked"]]


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
        "moved":           False,   # captain has moved/surfaced this turn
        "direction":       None,    # direction chosen this turn
        "engineer_done":   False,
        "first_mate_done": False,
        "waiting_for":     None,    # None | "sonar_response" | "drone_response"
        "sonar_query":     None,    # {row, col, sector} asked values
        "drone_query":     None,    # {sector} asked value
    }


def current_team(game):
    return game["turn_order"][game["turn_index"] % 2]


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

    # Move
    sub["position"] = [nr, nc]
    sub["trail"].append([nr, nc])
    game["turn_state"]["moved"] = True
    game["turn_state"]["direction"] = direction

    events = [{"type": "moved", "team": team, "direction": direction, "row": nr, "col": nc}]
    game["log"].append({"type": "move", "team": team, "direction": direction})
    return True, None, events


def captain_surface(game, team):
    """Surface the submarine. Returns (ok, error_msg, events)."""
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

    sub["health"] -= 1
    sub["trail"] = [[r, c]]   # clear trail (keep current position)
    sub["surfaced"] = True

    events = [{"type": "surfaced", "team": team, "sector": sector, "health": sub["health"]}]
    game["log"].append({"type": "surface", "team": team, "sector": sector})

    game["turn_state"]["moved"] = True
    game["turn_state"]["direction"] = None
    game["turn_state"]["engineer_done"] = True   # no engineering needed when surfacing
    game["turn_state"]["first_mate_done"] = True  # no charging when surfacing

    result = _check_game_over(game)
    if result:
        events.append(result)

    return True, None, events


def captain_dive(game, team):
    """Dive after surfacing."""
    sub = game["submarines"][team]
    if not sub["surfaced"]:
        return False, "Not surfaced"
    sub["surfaced"] = False
    return True, None


# ── Engineer ──────────────────────────────────────────────────────────────────

def engineer_mark(game, team, direction, index):
    """Mark an engineering node. Returns (ok, error_msg, events, damage)."""
    if current_team(game) != team:
        return False, "Not your turn", [], 0
    if not game["turn_state"]["moved"]:
        return False, "Captain hasn't moved yet", [], 0
    if game["turn_state"]["direction"] is None:
        return False, "No direction to mark (submarine surfaced)", [], 0
    if game["turn_state"]["engineer_done"]:
        return False, "Already marked this turn", [], 0
    if direction != game["turn_state"]["direction"]:
        return False, f"Must mark in the {game['turn_state']['direction']} section", [], 0

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
                                "cause": ev["type"], "damage": dmg, "health": sub["health"]})
        else:
            out_events.append({"type": "circuit_cleared", "team": team, "circuit": ev.get("circuit")})

    result = _check_game_over(game)
    if result:
        out_events.append(result)

    return True, None, out_events, total_damage


# ── First Mate ────────────────────────────────────────────────────────────────

def first_mate_charge(game, team, system):
    """Charge a system. Returns (ok, error_msg, events)."""
    if current_team(game) != team:
        return False, "Not your turn", []
    if not game["turn_state"]["moved"]:
        return False, "Captain hasn't moved yet", []
    if game["turn_state"]["direction"] is None:
        return False, "No charging when surfacing", []
    if game["turn_state"]["first_mate_done"]:
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


def captain_fire_torpedo(game, team, target_row, target_col):
    """Fire a torpedo. Returns (ok, error_msg, events)."""
    if current_team(game) != team:
        return False, "Not your turn", []
    if game["phase"] != "playing":
        return False, "Game not active", []
    sub = game["submarines"][team]
    if not _check_charge(sub, "torpedo"):
        return False, "Torpedo not charged", []
    if not is_valid_position(game, target_row, target_col):
        return False, "Invalid target", []

    r, c = sub["position"]
    dist = abs(target_row - r) + abs(target_col - c)
    if dist > 4:
        return False, "Target out of torpedo range (max 4)", []

    _use_system(sub, "torpedo")
    events = [{"type": "torpedo_fired", "team": team, "row": target_row, "col": target_col}]
    events += _apply_explosion(game, team, target_row, target_col)
    game["log"].append({"type": "torpedo", "team": team, "row": target_row, "col": target_col})
    return True, None, events


def captain_place_mine(game, team, target_row, target_col):
    """Place a mine on an adjacent cell. Returns (ok, error_msg, events)."""
    if current_team(game) != team:
        return False, "Not your turn", []
    sub = game["submarines"][team]
    if not _check_charge(sub, "mine"):
        return False, "Mine not charged", []
    if not is_valid_position(game, target_row, target_col):
        return False, "Invalid target", []

    r, c = sub["position"]
    dist = abs(target_row - r) + abs(target_col - c)
    if dist != 1:
        return False, "Mine must be placed on an adjacent cell", []

    # Can't place on trail (including current pos)
    if [target_row, target_col] in sub["trail"]:
        return False, "Cannot place mine on a cell already in your trail", []

    _use_system(sub, "mine")
    sub["mines"].append([target_row, target_col])
    events = [{"type": "mine_placed", "team": team}]
    game["log"].append({"type": "mine_placed", "team": team})
    return True, None, events


def captain_detonate_mine(game, team, mine_index):
    """Detonate one of the team's own mines. Returns (ok, error_msg, events)."""
    if game["phase"] != "playing":
        return False, "Game not active", []
    sub = game["submarines"][team]
    if mine_index < 0 or mine_index >= len(sub["mines"]):
        return False, "Invalid mine index", []

    mine = sub["mines"].pop(mine_index)
    events = [{"type": "mine_detonated", "team": team, "row": mine[0], "col": mine[1]}]
    events += _apply_explosion(game, team, mine[0], mine[1])
    game["log"].append({"type": "mine_detonated", "team": team, "row": mine[0], "col": mine[1]})
    return True, None, events


def _apply_explosion(game, firing_team, target_row, target_col):
    """Apply torpedo/mine explosion damage. Friendly fire included."""
    events = []
    for team, sub in game["submarines"].items():
        if sub["position"] is None:
            continue
        r, c = sub["position"]
        dist = abs(target_row - r) + abs(target_col - c)
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


def captain_use_sonar(game, team, ask_row, ask_col, ask_sector):
    """
    Use sonar: ask row, column, sector about the enemy.
    Server determines truths and returns them privately to querying captain.
    Returns (ok, error_msg, events)
    """
    if current_team(game) != team:
        return False, "Not your turn", []
    sub = game["submarines"][team]
    if not _check_charge(sub, "sonar"):
        return False, "Sonar not charged", []

    enemy_team = "red" if team == "blue" else "blue"
    enemy_sub = game["submarines"][enemy_team]
    er, ec = enemy_sub["position"]
    map_def = game["map"]
    sector_size = map_def["sector_size"]
    actual_sector = get_sector(er, ec, sector_size, map_def["cols"])

    # Verify ask values are reasonable
    row_match    = (er == ask_row)
    col_match    = (ec == ask_col)
    sector_match = (actual_sector == ask_sector)

    _use_system(sub, "sonar")
    events = [
        {"type": "sonar_used", "team": team, "ask_row": ask_row,
         "ask_col": ask_col, "ask_sector": ask_sector},
        # Private result sent only to querying captain
        {"type": "sonar_result", "target": team,
         "row_match": row_match, "col_match": col_match, "sector_match": sector_match},
        # Public event (enemy knows sonar was used)
        {"type": "sonar_announced", "team": team},
    ]
    game["log"].append({"type": "sonar", "team": team})
    return True, None, events


def captain_use_drone(game, team, ask_sector):
    """
    Use drone: ask if enemy is in a sector.
    Returns (ok, error_msg, events)
    """
    if current_team(game) != team:
        return False, "Not your turn", []
    sub = game["submarines"][team]
    if not _check_charge(sub, "drone"):
        return False, "Drone not charged", []

    enemy_team = "red" if team == "blue" else "blue"
    enemy_sub = game["submarines"][enemy_team]
    er, ec = enemy_sub["position"]
    map_def = game["map"]
    sector_size = map_def["sector_size"]
    actual_sector = get_sector(er, ec, sector_size, map_def["cols"])

    in_sector = (actual_sector == ask_sector)

    _use_system(sub, "drone")
    events = [
        {"type": "drone_used", "team": team, "ask_sector": ask_sector},
        {"type": "drone_result", "target": team, "in_sector": in_sector},
        {"type": "drone_announced", "team": team, "sector": ask_sector},
    ]
    game["log"].append({"type": "drone", "team": team})
    return True, None, events


def captain_use_stealth(game, team, moves):
    """
    Use stealth (Silence): move 0-4 cells silently.
    moves is a list of directions e.g. ["north", "north", "east"]
    Returns (ok, error_msg, events)
    """
    if current_team(game) != team:
        return False, "Not your turn", []
    if game["turn_state"]["moved"]:
        return False, "Already moved this turn", []
    sub = game["submarines"][team]
    if not _check_charge(sub, "stealth"):
        return False, "Stealth not charged", []
    if len(moves) > 4:
        return False, "Stealth allows at most 4 moves", []

    # Validate all moves up-front
    r, c = sub["position"]
    visited = set(tuple(pos) for pos in sub["trail"])
    path = []
    for d in moves:
        if d not in ("north", "south", "east", "west"):
            return False, f"Invalid direction: {d}", []
        dr, dc = direction_delta(d)
        r, c = r + dr, c + dc
        if not is_valid_position(game, r, c):
            return False, f"Invalid move {d} during stealth", []
        if (r, c) in visited:
            return False, "Cannot revisit a cell during stealth", []
        visited.add((r, c))
        path.append([r, c])

    # Apply moves
    _use_system(sub, "stealth")
    for pos in path:
        sub["position"] = pos
        sub["trail"].append(pos)

    game["turn_state"]["moved"] = True
    game["turn_state"]["direction"] = None  # stealth doesn't announce direction
    game["turn_state"]["engineer_done"] = True  # no engineer action during stealth
    game["turn_state"]["first_mate_done"] = True  # no first mate action during stealth

    events = [
        {"type": "stealth_used", "team": team, "steps": len(moves)},
        {"type": "moved_private", "team": team,
         "row": sub["position"][0], "col": sub["position"][1]},
    ]
    game["log"].append({"type": "stealth", "team": team, "steps": len(moves)})
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
    # When a directional move was made, engineer AND first mate must act first.
    # (Surface and stealth auto-set both flags, so this only blocks normal moves.)
    if ts["direction"] is not None:
        if not ts["engineer_done"]:
            return False, "Waiting for engineer to mark a node"
        if not ts["first_mate_done"]:
            return False, "Waiting for first mate to charge a system"
    return True, None


def end_turn(game, team):
    """End the active team's turn. Returns (ok, error_msg, events)."""
    ok, msg = can_end_turn(game, team)
    if not ok:
        return False, msg, []

    game["turn_index"] += 1
    game["turn_state"] = make_turn_state()
    next_t = current_team(game)
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

    return {
        "phase":      game["phase"],
        "turn_index": game["turn_index"],
        "current_team": current_team(game) if game["phase"] == "playing" else None,
        "turn_order": game["turn_order"],
        "turn_state": game["turn_state"],
        "submarines": subs,
        "winner":     game["winner"],
        "map": {
            "rows":        map_def["rows"],
            "cols":        map_def["cols"],
            "sector_size": map_def["sector_size"],
            "islands":     map_def["islands"],
            "name":        map_def["name"],
        },
    }
