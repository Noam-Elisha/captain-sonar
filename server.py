"""
Captain Sonar – Flask + SocketIO server.
Run:  python server.py
"""

import secrets, string
from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
import game_state as gs
from maps import get_col_labels, MAPS

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# ── In-memory storage ─────────────────────────────────────────────────────────
# games[game_id] = {
#   "game":    game_state dict (from game_state.py) or None pre-start,
#   "players": { name: {name, team, role, ready, sid} },
#   "host":    name (first player to create),
# }
games: dict = {}

# sid_map[sid] = {game_id, name}   (for disconnect handling)
sid_map: dict = {}

VALID_TEAMS = {"blue", "red"}
VALID_ROLES = {"captain", "first_mate", "engineer", "radio_operator"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _gen_id():
    while True:
        gid = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
        if gid not in games:
            return gid


def _lobby_state(game_id):
    g = games[game_id]
    return {
        "game_id": game_id,
        "host":    g["host"],
        "players": list(g["players"].values()),
        "phase":   "lobby" if g["game"] is None else g["game"]["phase"],
    }


def _emit_lobby(game_id):
    socketio.emit("lobby_state", _lobby_state(game_id), room=game_id)


def _player_in_game(game_id, name):
    return game_id in games and name in games[game_id]["players"]


def _get_player(game_id, name):
    return games[game_id]["players"].get(name)


def _emit_error(msg, sid=None):
    if sid:
        socketio.emit("error", {"msg": msg}, room=sid)
    else:
        emit("error", {"msg": msg})


def _team_role_sids(game_id, team=None, role=None):
    """Return list of sids matching optional team/role filters."""
    sids = []
    for p in games[game_id]["players"].values():
        if team and p["team"] != team:
            continue
        if role and p["role"] != role:
            continue
        if p.get("sid"):
            sids.append(p["sid"])
    return sids


def _emit_to_role(game_id, role, event, data):
    for sid in _team_role_sids(game_id, role=role):
        socketio.emit(event, data, room=sid)


def _emit_to_team_role(game_id, team, role, event, data):
    for sid in _team_role_sids(game_id, team=team, role=role):
        socketio.emit(event, data, room=sid)


def _dispatch_events(game_id, game, events):
    """
    Route events from game_state to the correct clients.
    Some events go to everyone, some only to specific team/role.
    """
    for ev in events:
        t = ev.get("type")

        if t == "moved":
            # Direction announced to ALL (radio ops track it)
            socketio.emit("direction_announced",
                          {"team": ev["team"], "direction": ev["direction"]},
                          room=game_id)
            # Position update only to own captain
            _emit_to_team_role(game_id, ev["team"], "captain", "moved_self",
                                {"row": ev["row"], "col": ev["col"],
                                 "trail": game["submarines"][ev["team"]]["trail"]})
            # Notify engineer of direction to mark
            _emit_to_team_role(game_id, ev["team"], "engineer", "direction_to_mark",
                                {"direction": ev["direction"]})
            # Notify FM they can charge
            _emit_to_team_role(game_id, ev["team"], "first_mate", "can_charge", {})

        elif t == "surfaced":
            socketio.emit("surface_announced",
                          {"team": ev["team"], "sector": ev["sector"],
                           "health": ev["health"]},
                          room=game_id)

        elif t == "torpedo_fired":
            socketio.emit("torpedo_fired",
                          {"team": ev["team"], "row": ev["row"], "col": ev["col"]},
                          room=game_id)

        elif t == "damage":
            socketio.emit("damage",
                          {"team": ev["team"], "amount": ev["amount"],
                           "health": ev["health"], "cause": ev.get("cause", ""),
                           "row": ev.get("row"), "col": ev.get("col")},
                          room=game_id)

        elif t == "engineering_damage":
            socketio.emit("damage",
                          {"team": ev["team"], "amount": ev["damage"],
                           "health": ev["health"],
                           "cause": ev["cause"]},
                          room=game_id)
            # Update engineer board display for own team
            _emit_to_team_role(game_id, ev["team"], "engineer", "board_update",
                                {"board": game["submarines"][ev["team"]]["engineering"]})

        elif t == "circuit_cleared":
            _emit_to_team_role(game_id, _current_active(game_id), "engineer",
                                "board_update",
                                {"board": game["submarines"][_current_active(game_id)]["engineering"]})

        elif t == "system_charged":
            # Update FM display
            _emit_to_team_role(game_id, ev["team"], "first_mate", "systems_update",
                                {"systems": game["submarines"][ev["team"]]["systems"]})
            # Update captain display
            _emit_to_team_role(game_id, ev["team"], "captain", "systems_update",
                                {"systems": game["submarines"][ev["team"]]["systems"]})

        elif t == "mine_placed":
            _emit_to_team_role(game_id, ev["team"], "captain", "mine_placed_ack",
                                {"mines": game["submarines"][ev["team"]]["mines"],
                                 "systems": game["submarines"][ev["team"]]["systems"]})
            _emit_to_team_role(game_id, ev["team"], "first_mate", "systems_update",
                                {"systems": game["submarines"][ev["team"]]["systems"]})

        elif t == "mine_detonated":
            socketio.emit("mine_detonated",
                          {"team": ev["team"], "row": ev["row"], "col": ev["col"]},
                          room=game_id)

        elif t == "sonar_used":
            socketio.emit("sonar_announced", {"team": ev["team"]}, room=game_id)

        elif t == "sonar_result":
            # Private result to querying captain only
            _emit_to_team_role(game_id, ev["target"], "captain", "sonar_result",
                                {"row_match":    ev["row_match"],
                                 "col_match":    ev["col_match"],
                                 "sector_match": ev["sector_match"]})
            # Update systems (charge consumed)
            _emit_to_team_role(game_id, ev["target"], "first_mate", "systems_update",
                                {"systems": game["submarines"][ev["target"]]["systems"]})

        elif t == "drone_used":
            socketio.emit("drone_announced",
                          {"team": ev["team"], "sector": ev["ask_sector"]},
                          room=game_id)

        elif t == "drone_result":
            _emit_to_team_role(game_id, ev["target"], "captain", "drone_result",
                                {"in_sector": ev["in_sector"]})
            _emit_to_team_role(game_id, ev["target"], "first_mate", "systems_update",
                                {"systems": game["submarines"][ev["target"]]["systems"]})

        elif t == "stealth_used":
            socketio.emit("stealth_announced",
                          {"team": ev["team"], "steps": ev["steps"]},
                          room=game_id)
            _emit_to_team_role(game_id, ev["team"], "captain", "moved_self",
                                {"row": game["submarines"][ev["team"]]["position"][0],
                                 "col": game["submarines"][ev["team"]]["position"][1],
                                 "trail": game["submarines"][ev["team"]]["trail"]})
            _emit_to_team_role(game_id, ev["team"], "first_mate", "systems_update",
                                {"systems": game["submarines"][ev["team"]]["systems"]})

        elif t == "turn_end":
            pass  # combined with turn_start

        elif t == "turn_start":
            socketio.emit("turn_start", {"team": ev["team"]}, room=game_id)
            # Send full game state to each client
            _broadcast_game_state(game_id)

        elif t == "game_over":
            games[game_id]["game"]["phase"] = "ended"
            socketio.emit("game_over",
                          {"winner": ev["winner"], "loser": ev["loser"]},
                          room=game_id)


def _current_active(game_id):
    g = games[game_id]
    if g["game"]:
        return gs.current_team(g["game"])
    return None


def _broadcast_game_state(game_id):
    """Send personalised game state to each connected player."""
    g = games[game_id]
    if not g["game"]:
        return
    for name, p in g["players"].items():
        if not p.get("sid"):
            continue
        team = p.get("team")
        state = gs.serialize_game(g["game"], perspective_team=team)
        socketio.emit("game_state", state, room=p["sid"])


def _can_start(game_id):
    """Check if lobby is ready to start (need 1 captain + ≥1 each role per team at minimum)."""
    g = games[game_id]
    players = list(g["players"].values())
    if len(players) < 2:
        return False, "Need at least 2 players"
    # Must have at least one captain per team that has any players
    teams_present = {p["team"] for p in players if p["team"]}
    for team in teams_present:
        team_players = [p for p in players if p["team"] == team]
        roles = {p["role"] for p in team_players}
        if "captain" not in roles:
            return False, f"{team} team needs a captain"
        if "first_mate" not in roles:
            return False, f"{team} team needs a first mate"
        if "engineer" not in roles:
            return False, f"{team} team needs an engineer"
    return True, None


# ── HTTP Routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/lobby")
def lobby():
    game_id = request.args.get("game_id", "").upper().strip()
    name    = request.args.get("name", "").strip()
    if not game_id or not name:
        return redirect(url_for("index"))
    return render_template("lobby.html", game_id=game_id, player_name=name)


@app.route("/play")
def play():
    """Redirect to role-specific view."""
    game_id = request.args.get("game_id", "").upper().strip()
    name    = request.args.get("name", "").strip()
    if not game_id or game_id not in games or not name:
        return redirect(url_for("index"))

    g = games[game_id]
    if g["game"] is None or g["game"]["phase"] == "lobby":
        return redirect(url_for("lobby", game_id=game_id, name=name))

    player = _get_player(game_id, name)
    if not player:
        return redirect(url_for("index"))

    role = player["role"]
    team = player["team"]
    map_def = g["game"]["map"]

    common = dict(
        game_id=game_id,
        player_name=name,
        team=team,
        role=role,
        map_rows=map_def["rows"],
        map_cols=map_def["cols"],
        sector_size=map_def["sector_size"],
        islands=map_def["islands"],
        col_labels=get_col_labels(map_def["cols"]),
    )
    return render_template(f"{role}.html", **common)


# ── Socket Events — Connection ─────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    pass


@socketio.on("disconnect")
def on_disconnect():
    sid = request.sid
    info = sid_map.pop(sid, None)
    if info:
        game_id = info["game_id"]
        name    = info["name"]
        if game_id in games and name in games[game_id]["players"]:
            games[game_id]["players"][name]["sid"] = None
            # Notify lobby
            _emit_lobby(game_id)


@socketio.on("join_room")
def on_join_room(data):
    game_id = data.get("game_id", "").upper()
    join_room(game_id)


# ── Socket Events — Lobby ─────────────────────────────────────────────────────

@socketio.on("create_game")
def on_create_game(data):
    name = (data.get("name") or "").strip()
    if not name:
        return emit("error", {"msg": "Name required"})

    game_id = _gen_id()
    games[game_id] = {
        "game":    None,
        "players": {name: {"name": name, "team": "blue", "role": "", "ready": False, "sid": request.sid}},
        "host":    name,
    }
    sid_map[request.sid] = {"game_id": game_id, "name": name}
    join_room(game_id)
    emit("game_created", {"game_id": game_id, "name": name})
    _emit_lobby(game_id)


@socketio.on("join_game")
def on_join_game(data):
    game_id = (data.get("game_id") or "").upper().strip()
    name    = (data.get("name") or "").strip()

    if not game_id or not name:
        return emit("error", {"msg": "game_id and name required"})
    if game_id not in games:
        return emit("error", {"msg": "Game not found"})
    g = games[game_id]
    if name in g["players"]:
        # Rejoin (reconnect) — update sid, restore state
        g["players"][name]["sid"] = request.sid
        sid_map[request.sid] = {"game_id": game_id, "name": name}
        join_room(game_id)
        emit("join_ack", {"game_id": game_id, "name": name})
        if g["game"] is not None:
            # Game in progress — send current game state instead of lobby
            state = gs.serialize_game(g["game"], perspective_team=g["players"][name]["team"])
            emit("game_state", state)
        else:
            _emit_lobby(game_id)
        return

    if g["game"] is not None and g["game"]["phase"] != "lobby":
        return emit("error", {"msg": "Game already in progress"})

    if len(g["players"]) >= 8:
        return emit("error", {"msg": "Lobby is full (max 8 players)"})

    # Check name uniqueness
    if any(p["name"].lower() == name.lower() for p in g["players"].values()):
        return emit("error", {"msg": "Name already taken"})

    g["players"][name] = {"name": name, "team": "red", "role": "", "ready": False, "sid": request.sid}
    sid_map[request.sid] = {"game_id": game_id, "name": name}
    join_room(game_id)
    emit("join_ack", {"game_id": game_id, "name": name})
    _emit_lobby(game_id)


@socketio.on("set_team")
def on_set_team(data):
    game_id = (data.get("game_id") or "").upper()
    name    = data.get("name", "")
    team    = data.get("team", "")
    if team not in VALID_TEAMS:
        return emit("error", {"msg": "Invalid team"})
    if not _player_in_game(game_id, name):
        return emit("error", {"msg": "Player not found"})
    games[game_id]["players"][name]["team"] = team
    games[game_id]["players"][name]["ready"] = False
    _emit_lobby(game_id)


@socketio.on("set_role")
def on_set_role(data):
    game_id = (data.get("game_id") or "").upper()
    name    = data.get("name", "")
    role    = data.get("role", "")
    if role not in VALID_ROLES and role != "":
        return emit("error", {"msg": "Invalid role"})
    if not _player_in_game(game_id, name):
        return emit("error", {"msg": "Player not found"})

    p = games[game_id]["players"][name]
    team = p["team"]

    # Check role not already taken on same team
    if role:
        for other_name, other_p in games[game_id]["players"].items():
            if other_name != name and other_p["team"] == team and other_p["role"] == role:
                return emit("error", {"msg": f"{role} already taken on {team} team"})

    p["role"] = role
    p["ready"] = False
    _emit_lobby(game_id)


@socketio.on("player_ready")
def on_player_ready(data):
    game_id = (data.get("game_id") or "").upper()
    name    = data.get("name", "")
    ready   = bool(data.get("ready", False))
    if not _player_in_game(game_id, name):
        return emit("error", {"msg": "Player not found"})
    p = games[game_id]["players"][name]
    if not p["role"] or not p["team"]:
        return emit("error", {"msg": "Must have role and team before readying up"})
    p["ready"] = ready
    _emit_lobby(game_id)


@socketio.on("start_game")
def on_start_game(data):
    game_id = (data.get("game_id") or "").upper()
    name    = data.get("name", "")
    if game_id not in games:
        return emit("error", {"msg": "Game not found"})
    g = games[game_id]
    if g["host"] != name:
        return emit("error", {"msg": "Only the host can start the game"})

    ok, msg = _can_start(game_id)
    if not ok:
        return emit("error", {"msg": msg})

    # Determine turn order by which teams are present
    teams_present = list({p["team"] for p in g["players"].values() if p["team"]})
    teams_present.sort()  # deterministic, then shuffle
    import random; random.shuffle(teams_present)

    g["game"] = gs.make_game("alpha")
    g["game"]["turn_order"] = teams_present
    g["game"]["phase"] = "placement"

    socketio.emit("game_started", {
        "map": {
            "rows":        g["game"]["map"]["rows"],
            "cols":        g["game"]["map"]["cols"],
            "sector_size": g["game"]["map"]["sector_size"],
            "islands":     g["game"]["map"]["islands"],
            "col_labels":  get_col_labels(g["game"]["map"]["cols"]),
        },
        "turn_order": teams_present,
    }, room=game_id)


# ── Socket Events — Placement ─────────────────────────────────────────────────

@socketio.on("place_sub")
def on_place_sub(data):
    game_id = (data.get("game_id") or "").upper()
    name    = data.get("name", "")
    row     = data.get("row")
    col     = data.get("col")

    if game_id not in games:
        return emit("error", {"msg": "Game not found"})
    g = games[game_id]
    p = _get_player(game_id, name)
    if not p or p["role"] != "captain":
        return emit("error", {"msg": "Only the captain can place the submarine"})
    if g["game"] is None or g["game"]["phase"] != "placement":
        return emit("error", {"msg": "Not in placement phase"})

    ok, msg = gs.place_submarine(g["game"], p["team"], row, col)
    if not ok:
        return emit("error", {"msg": msg})

    socketio.emit("sub_placed", {"team": p["team"]}, room=game_id)

    # If both placed → game starts
    if g["game"]["phase"] == "playing":
        current = gs.current_team(g["game"])
        socketio.emit("game_phase", {"current_team": current}, room=game_id)
        _broadcast_game_state(game_id)


# ── Socket Events — Captain ───────────────────────────────────────────────────

def _get_captain(game_id, name):
    """Return (player, game) if name is a captain in game_id, else emit error."""
    g = games.get(game_id)
    if not g or g["game"] is None:
        emit("error", {"msg": "Game not found or not started"})
        return None, None
    p = _get_player(game_id, name)
    if not p or p["role"] != "captain":
        emit("error", {"msg": "Only the captain can do that"})
        return None, None
    if g["game"]["phase"] != "playing":
        emit("error", {"msg": "Game is not in playing phase"})
        return None, None
    return p, g["game"]


@socketio.on("captain_move")
def on_captain_move(data):
    game_id   = (data.get("game_id") or "").upper()
    name      = data.get("name", "")
    direction = (data.get("direction") or "").lower()
    p, game = _get_captain(game_id, name)
    if not p:
        return

    ok, msg, events = gs.captain_move(game, p["team"], direction)
    if not ok:
        return emit("error", {"msg": msg})

    _dispatch_events(game_id, game, events)
    _check_turn_auto_advance(game_id, game)


@socketio.on("captain_surface")
def on_captain_surface(data):
    game_id = (data.get("game_id") or "").upper()
    name    = data.get("name", "")
    p, game = _get_captain(game_id, name)
    if not p:
        return

    ok, msg, events = gs.captain_surface(game, p["team"])
    if not ok:
        return emit("error", {"msg": msg})

    _dispatch_events(game_id, game, events)
    _check_turn_auto_advance(game_id, game)


@socketio.on("captain_dive")
def on_captain_dive(data):
    game_id = (data.get("game_id") or "").upper()
    name    = data.get("name", "")
    p, game = _get_captain(game_id, name)
    if not p:
        return

    ok, msg = gs.captain_dive(game, p["team"])
    if not ok:
        return emit("error", {"msg": msg})

    emit("dive_ack", {})
    socketio.emit("dive_announced", {"team": p["team"]}, room=game_id)


@socketio.on("captain_torpedo")
def on_captain_torpedo(data):
    game_id = (data.get("game_id") or "").upper()
    name    = data.get("name", "")
    row     = data.get("row")
    col     = data.get("col")
    p, game = _get_captain(game_id, name)
    if not p:
        return

    ok, msg, events = gs.captain_fire_torpedo(game, p["team"], row, col)
    if not ok:
        return emit("error", {"msg": msg})

    _dispatch_events(game_id, game, events)
    # Update captain's systems display
    _emit_to_team_role(game_id, p["team"], "captain", "systems_update",
                       {"systems": game["submarines"][p["team"]]["systems"]})
    _emit_to_team_role(game_id, p["team"], "first_mate", "systems_update",
                       {"systems": game["submarines"][p["team"]]["systems"]})


@socketio.on("captain_mine_place")
def on_captain_mine_place(data):
    game_id = (data.get("game_id") or "").upper()
    name    = data.get("name", "")
    row     = data.get("row")
    col     = data.get("col")
    p, game = _get_captain(game_id, name)
    if not p:
        return

    ok, msg, events = gs.captain_place_mine(game, p["team"], row, col)
    if not ok:
        return emit("error", {"msg": msg})

    _dispatch_events(game_id, game, events)


@socketio.on("captain_mine_det")
def on_captain_mine_det(data):
    game_id    = (data.get("game_id") or "").upper()
    name       = data.get("name", "")
    mine_index = data.get("mine_index", 0)
    p, game = _get_captain(game_id, name)
    if not p:
        return

    ok, msg, events = gs.captain_detonate_mine(game, p["team"], mine_index)
    if not ok:
        return emit("error", {"msg": msg})

    _dispatch_events(game_id, game, events)
    # Update captain's mine list
    _emit_to_team_role(game_id, p["team"], "captain", "mine_placed_ack",
                       {"mines": game["submarines"][p["team"]]["mines"],
                        "systems": game["submarines"][p["team"]]["systems"]})


@socketio.on("captain_sonar")
def on_captain_sonar(data):
    game_id    = (data.get("game_id") or "").upper()
    name       = data.get("name", "")
    ask_row    = data.get("ask_row")
    ask_col    = data.get("ask_col")
    ask_sector = data.get("ask_sector")
    p, game = _get_captain(game_id, name)
    if not p:
        return

    ok, msg, events = gs.captain_use_sonar(game, p["team"], ask_row, ask_col, ask_sector)
    if not ok:
        return emit("error", {"msg": msg})

    _dispatch_events(game_id, game, events)


@socketio.on("captain_drone")
def on_captain_drone(data):
    game_id    = (data.get("game_id") or "").upper()
    name       = data.get("name", "")
    ask_sector = data.get("sector")
    p, game = _get_captain(game_id, name)
    if not p:
        return

    ok, msg, events = gs.captain_use_drone(game, p["team"], ask_sector)
    if not ok:
        return emit("error", {"msg": msg})

    _dispatch_events(game_id, game, events)


@socketio.on("captain_stealth")
def on_captain_stealth(data):
    game_id = (data.get("game_id") or "").upper()
    name    = data.get("name", "")
    moves   = data.get("moves", [])
    p, game = _get_captain(game_id, name)
    if not p:
        return

    ok, msg, events = gs.captain_use_stealth(game, p["team"], moves)
    if not ok:
        return emit("error", {"msg": msg})

    _dispatch_events(game_id, game, events)


@socketio.on("captain_end_turn")
def on_captain_end_turn(data):
    game_id = (data.get("game_id") or "").upper()
    name    = data.get("name", "")
    p, game = _get_captain(game_id, name)
    if not p:
        return

    ok, msg, events = gs.end_turn(game, p["team"])
    if not ok:
        return emit("error", {"msg": msg})

    _dispatch_events(game_id, game, events)


# ── Socket Events — Engineer ──────────────────────────────────────────────────

@socketio.on("engineer_mark")
def on_engineer_mark(data):
    game_id   = (data.get("game_id") or "").upper()
    name      = data.get("name", "")
    direction = (data.get("direction") or "").lower()
    index     = data.get("index")

    g = games.get(game_id)
    if not g or g["game"] is None:
        return emit("error", {"msg": "Game not found"})

    p = _get_player(game_id, name)
    if not p or p["role"] != "engineer":
        return emit("error", {"msg": "Only the engineer can mark nodes"})

    ok, msg, events, _ = gs.engineer_mark(g["game"], p["team"], direction, index)
    if not ok:
        return emit("error", {"msg": msg})

    # Send updated board to engineer
    emit("board_update", {"board": g["game"]["submarines"][p["team"]]["engineering"]})
    _dispatch_events(game_id, g["game"], events)
    _check_turn_auto_advance(game_id, g["game"])


# ── Socket Events — First Mate ────────────────────────────────────────────────

@socketio.on("first_mate_charge")
def on_first_mate_charge(data):
    game_id = (data.get("game_id") or "").upper()
    name    = data.get("name", "")
    system  = data.get("system", "")

    g = games.get(game_id)
    if not g or g["game"] is None:
        return emit("error", {"msg": "Game not found"})

    p = _get_player(game_id, name)
    if not p or p["role"] != "first_mate":
        return emit("error", {"msg": "Only the first mate can charge systems"})

    ok, msg, events = gs.first_mate_charge(g["game"], p["team"], system)
    if not ok:
        return emit("error", {"msg": msg})

    _dispatch_events(game_id, g["game"], events)
    emit("systems_update", {"systems": g["game"]["submarines"][p["team"]]["systems"]})
    _check_turn_auto_advance(game_id, g["game"])


# ── Auto-advance helper ───────────────────────────────────────────────────────

def _check_turn_auto_advance(game_id, game):
    """
    Nothing auto-advances the turn — captain must explicitly call end_turn.
    But we do broadcast game state updates here.
    """
    _broadcast_game_state(game_id)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Starting Captain Sonar server on http://localhost:5000")
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
