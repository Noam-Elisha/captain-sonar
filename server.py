"""
Admiral Radar – Flask + SocketIO server.
Run:  python server.py
"""

from __future__ import annotations
import secrets, string
from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
import game_state as gs
from maps import get_col_labels, generate_map, DEFAULT_SETTINGS
from bots import CaptainBot, FirstMateBot, EngineerBot, RadioOperatorBot
from comms import TeamComms

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# ── In-memory storage ─────────────────────────────────────────────────────────
# games[game_id] = {
#   "game":    game_state dict (from game_state.py) or None pre-start,
#   "players": { name: {name, team, role, ready, sid, is_bot, bot} },
#   "host":    name (first player to create),
# }
games: dict = {}

# sid_map[sid] = {game_id, name}   (for disconnect handling)
sid_map: dict = {}

# bot_tasks[game_id] = True while a bot background loop is running
bot_tasks: dict = {}

VALID_TEAMS = {"blue", "red"}
VALID_ROLES = {"captain", "first_mate", "engineer", "radio_operator"}
BOT_NAME_PREFIX = "Bot"


# ── Spectator helpers ─────────────────────────────────────────────────────────

def _get_spectators(game_id):
    return games[game_id].get("spectators", {})


def _broadcast_to_spectators(game_id):
    """Send full (unmasked) game state to all connected spectators."""
    g = games[game_id]
    if not g.get("game"):
        return
    state = gs.serialize_game(g["game"], perspective_team=None)
    for spec in _get_spectators(game_id).values():
        if spec.get("sid"):
            socketio.emit("game_state", state, room=spec["sid"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _gen_id():
    while True:
        gid = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
        if gid not in games:
            return gid


def _lobby_state(game_id):
    g = games[game_id]
    # Exclude non-JSON-serializable bot objects from player data
    players = []
    for p in g["players"].values():
        players.append({k: v for k, v in p.items() if k != "bot"})
    spectators = [{"name": s["name"]} for s in g.get("spectators", {}).values()]
    return {
        "game_id":    game_id,
        "host":       g["host"],
        "players":    players,
        "spectators": spectators,
        "phase":      "lobby" if g["game"] is None else g["game"]["phase"],
        "map_settings": g.get("map_settings"),
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
    """Return list of sids matching optional team/role filters (human only)."""
    sids = []
    for p in games[game_id]["players"].values():
        if p.get("is_bot"):
            continue
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


def _emit_bot_chat_team_only(game_id, team, data):
    """Emit bot_chat to ONLY this team's human players + all spectators.
    Team-internal messages (RO reports, engineer marks, FM charges, placement)
    must NOT leak to the enemy team."""
    for sid in _team_role_sids(game_id, team=team):
        socketio.emit("bot_chat", data, room=sid)
    for spec in _get_spectators(game_id).values():
        if spec.get("sid"):
            socketio.emit("bot_chat", data, room=spec["sid"])


def _dispatch_events(game_id, game, events):
    """Route events from game_state to the correct clients and TeamComms."""
    for ev in events:
        t = ev.get("type")

        # Route every event through TeamComms (replaces ad-hoc bot update calls)
        _route_event_to_comms(game_id, ev, game)

        if t == "moved":
            socketio.emit("direction_announced",
                          {"team": ev["team"], "direction": ev["direction"]},
                          room=game_id)
            _emit_to_team_role(game_id, ev["team"], "captain", "moved_self",
                                {"row": ev["row"], "col": ev["col"],
                                 "trail": game["submarines"][ev["team"]]["trail"],
                                 "direction": ev["direction"]})
            _emit_to_team_role(game_id, ev["team"], "engineer", "direction_to_mark",
                                {"direction": ev["direction"]})
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
            _emit_to_team_role(game_id, ev["team"], "engineer", "board_update",
                                {"board": game["submarines"][ev["team"]]["engineering"]})

        elif t == "circuit_cleared":
            team_c = ev.get("team") or _current_active(game_id)
            _emit_to_team_role(game_id, team_c, "engineer",
                                "board_update",
                                {"board": game["submarines"][team_c]["engineering"]})
            socketio.emit("circuit_cleared",
                          {"team": team_c, "circuit": ev.get("circuit")},
                          room=game_id)

        elif t == "system_charged":
            # Include reason="charge" so first_mate.js can distinguish charging from consumption
            update_data = {
                "systems": game["submarines"][ev["team"]]["systems"],
                "reason":  "charge",
                "system":  ev.get("system"),
            }
            _emit_to_team_role(game_id, ev["team"], "first_mate", "systems_update", update_data)
            _emit_to_team_role(game_id, ev["team"], "captain",     "systems_update", update_data)

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

        elif t == "sonar_announced":
            socketio.emit("sonar_announced", {"team": ev["team"]}, room=game_id)

        elif t == "sonar_activated":
            # RULEBOOK interactive sonar: emit query to enemy captain, auto-respond if bot
            activating_team = ev["team"]
            enemy = gs.other_team(activating_team)
            socketio.emit("sonar_announced", {"team": activating_team}, room=game_id)
            # Send query to enemy captain (human or bot)
            _emit_to_team_role(game_id, enemy, "captain", "sonar_query",
                                {"activating_team": activating_team})
            # If enemy captain is a bot, auto-respond immediately
            enemy_cap = _get_bot_for_role(game_id, enemy, "captain")
            if enemy_cap and enemy_cap.get("bot"):
                _bot_sonar_respond(game_id, game, enemy, enemy_cap)

        elif t == "sonar_result":
            # RULEBOOK: sonar result is public — both teams hear what the enemy captain said.
            # Broadcast to whole room; each client checks data.target to determine role.
            target = ev["target"]
            result_data = {"type1": ev["type1"], "val1": ev["val1"],
                           "type2": ev["type2"], "val2": ev["val2"],
                           "target": target}
            socketio.emit("sonar_result", result_data, room=game_id)
            _emit_to_team_role(game_id, target, "first_mate", "systems_update",
                                {"systems": game["submarines"][target]["systems"]})

        elif t == "drone_used":
            socketio.emit("drone_announced",
                          {"team": ev["team"], "sector": ev["ask_sector"]},
                          room=game_id)

        elif t == "drone_result":
            # RULEBOOK: drone result is announced publicly — both teams hear it.
            # Broadcast to whole room; each client checks data.target to determine role.
            result_data = {
                "in_sector":  ev["in_sector"],
                "ask_sector": ev.get("ask_sector", 0),
                "target":     ev["target"],   # activating team
            }
            socketio.emit("drone_result", result_data, room=game_id)
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
            # RULEBOOK stealth: engineer still marks 1 node (in stealth direction, privately)
            # and FM still charges 1 system — notify both via private events
            _emit_to_team_role(game_id, ev["team"], "engineer", "direction_to_mark",
                                {"direction": ev["direction"], "is_stealth": True})
            _emit_to_team_role(game_id, ev["team"], "first_mate", "can_charge",
                                {"is_stealth": True})

        elif t == "turn_end":
            pass

        elif t == "turn_start":
            socketio.emit("turn_start", {"team": ev["team"]}, room=game_id)
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
    """Send personalised game state to each connected player, and full state to spectators."""
    g = games[game_id]
    if not g["game"]:
        return
    for name, p in g["players"].items():
        if p.get("is_bot") or not p.get("sid"):
            continue
        team = p.get("team")
        state = gs.serialize_game(g["game"], perspective_team=team)
        socketio.emit("game_state", state, room=p["sid"])
    # Spectators get full unmasked state
    _broadcast_to_spectators(game_id)


def _can_start(game_id):
    """Check if lobby is ready to start."""
    g = games[game_id]
    players = list(g["players"].values())
    # Need at least 2 total (can be bots)
    if len(players) < 2:
        return False, "Need at least 2 players (humans or bots)"
    teams_present = {p["team"] for p in players if p["team"]}
    if len(teams_present) < 2:
        return False, "Need players on both teams"
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


# ── Bot helpers ────────────────────────────────────────────────────────────────

def _make_bot_player(team: str, role: str) -> dict:
    """Create a bot player entry."""
    role_short = role.replace("_", "-")
    name = f"{BOT_NAME_PREFIX}_{team.capitalize()}_{role_short.capitalize()}"
    bot = None
    if role == "captain":
        bot = CaptainBot(team)
    elif role == "first_mate":
        bot = FirstMateBot(team)
    elif role == "engineer":
        bot = EngineerBot(team)
    elif role == "radio_operator":
        bot = RadioOperatorBot(team)

    return {
        "name":    name,
        "team":    team,
        "role":    role,
        "ready":   True,
        "sid":     None,
        "is_bot":  True,
        "bot":     bot,
    }


def _get_bot_for_role(game_id: str, team: str, role: str):
    """Return the bot player dict if a bot holds this team/role, else None."""
    for p in games[game_id]["players"].values():
        if p.get("is_bot") and p["team"] == team and p["role"] == role:
            return p
    return None


def _get_team_comms(game_id: str, team: str):
    """Return the TeamComms object for a team, or None."""
    g = games.get(game_id)
    if g and "comms" in g:
        return g["comms"].get(team)
    return None


def _route_event_to_comms(game_id: str, ev: dict, game: dict):
    """Route a game event to the appropriate TeamComms objects.
    This replaces the old ad-hoc _update_ro_bot, _update_captain_bot_* functions.
    Events go through TeamComms → bots read their inbox when it's their turn."""
    t = ev.get("type")

    if t == "moved":
        # Other team hears the enemy move direction
        other = gs.other_team(ev["team"])
        comms = _get_team_comms(game_id, other)
        if comms:
            comms.relay_enemy_move(ev["direction"])

    elif t == "surfaced":
        # Other team hears enemy surfaced in a sector
        other = gs.other_team(ev["team"])
        comms = _get_team_comms(game_id, other)
        if comms:
            comms.relay_enemy_surface(ev["sector"])

    elif t == "torpedo_fired":
        # Other team hears enemy torpedo at exact coords
        other = gs.other_team(ev["team"])
        comms = _get_team_comms(game_id, other)
        if comms:
            comms.relay_enemy_torpedo(ev["row"], ev["col"])
        # Own team's RO also hears the friendly torpedo
        own_comms = _get_team_comms(game_id, ev["team"])
        if own_comms:
            own_comms.relay_friendly_torpedo(ev["row"], ev["col"])

    elif t == "mine_placed":
        # Other team hears a mine was placed (but not where)
        other = gs.other_team(ev["team"])
        comms = _get_team_comms(game_id, other)
        if comms:
            comms.relay_enemy_mine_placed()

    elif t == "mine_detonated":
        # Own team's RO hears the friendly mine detonation
        own_comms = _get_team_comms(game_id, ev["team"])
        if own_comms:
            own_comms.relay_friendly_mine_detonated(ev["row"], ev["col"])

    elif t == "sonar_result":
        # Both RO and captain on the activating team hear the result
        target = ev["target"]   # the activating team
        comms = _get_team_comms(game_id, target)
        if comms:
            comms.relay_sonar_result(ev["type1"], ev["val1"], ev["type2"], ev["val2"])

    elif t == "drone_result":
        # Both RO and captain on the activating team hear the result
        target = ev["target"]
        comms = _get_team_comms(game_id, target)
        if comms:
            comms.relay_drone_result(ev.get("ask_sector", 0), ev["in_sector"])


def _process_bot_comms_pre_turn(game_id: str, team: str):
    """Process bot communications BEFORE the captain acts.
    Flow: RO processes inbox → generates report → FM reports status →
          Engineer recommends → Captain processes inbox."""
    g = games[game_id]
    comms = _get_team_comms(game_id, team)
    if not comms:
        return

    # 1. RO processes events and generates position report for captain
    ro = _get_bot_for_role(game_id, team, "radio_operator")
    if ro and ro.get("bot") and ro["bot"].initialized:
        msgs = comms.read_inbox("radio_operator")
        ro["bot"].process_inbox(msgs)
        summary = ro["bot"].generate_report(comms)
        if summary:  # Only chat when RO has useful info
            _emit_bot_chat_team_only(game_id, team, {
                "team": team, "role": "radio_operator", "name": ro["name"],
                "msg": summary,
            })

    # 2. FM reports current system status to captain
    fm = _get_bot_for_role(game_id, team, "first_mate")
    if fm and fm.get("bot"):
        sub = g["game"]["submarines"][team]
        fm["bot"].send_communications(comms, sub["systems"])

    # 3. Engineer recommends directions to captain
    eng = _get_bot_for_role(game_id, team, "engineer")
    if eng and eng.get("bot"):
        sub = g["game"]["submarines"][team]
        eng["bot"].send_communications(comms, sub["engineering"])

    # 4. Captain processes all accumulated messages (RO report, FM status, Engineer recs)
    cap = _get_bot_for_role(game_id, team, "captain")
    if cap and cap.get("bot"):
        msgs = comms.read_inbox("captain")
        cap["bot"].process_inbox(msgs)


def _process_bot_comms_post_move(game_id: str, team: str):
    """After captain moves, send priority communications to FM and Engineer."""
    g = games[game_id]
    comms = _get_team_comms(game_id, team)
    if not comms:
        return

    # Captain sends priorities to FM and Engineer
    cap = _get_bot_for_role(game_id, team, "captain")
    if cap and cap.get("bot"):
        sub = g["game"]["submarines"][team]
        cap["bot"].send_communications(comms, sub, g["game"]["map"])

    # FM processes captain's new priority
    fm = _get_bot_for_role(game_id, team, "first_mate")
    if fm and fm.get("bot"):
        msgs = comms.read_inbox("first_mate")
        fm["bot"].process_inbox(msgs)

    # Engineer processes captain's new system protection priority
    eng = _get_bot_for_role(game_id, team, "engineer")
    if eng and eng.get("bot"):
        msgs = comms.read_inbox("engineer")
        eng["bot"].process_inbox(msgs)


# ── Bot execution loop ─────────────────────────────────────────────────────────

def _schedule_bots(game_id: str):
    """Start a background bot loop if one is not already running."""
    if bot_tasks.get(game_id):
        return
    bot_tasks[game_id] = True
    socketio.start_background_task(_run_bot_loop, game_id)


def _run_bot_loop(game_id: str):
    """
    Background task: execute pending bot actions with 1.2 s pauses.
    Stops when no bot action is needed (human turn, game over, etc.).
    """
    import eventlet
    max_steps = 300   # safety ceiling
    steps = 0
    try:
        while steps < max_steps:
            steps += 1
            eventlet.sleep(1.2)

            if game_id not in games:
                break
            g = games[game_id]
            game = g["game"]
            if not game or game["phase"] not in ("placement", "playing"):
                break

            if game["phase"] == "placement":
                acted = _bot_placement_step(game_id, g, game)
            else:
                acted = _bot_playing_step(game_id, g, game)

            if not acted:
                break
    finally:
        bot_tasks[game_id] = False


def _bot_placement_step(game_id: str, g: dict, game: dict) -> bool:
    """Place submarines for any bot captains that haven't placed yet."""
    acted = False
    for team in ["blue", "red"]:
        if game["submarines"][team]["position"] is not None:
            continue
        cap = _get_bot_for_role(game_id, team, "captain")
        if cap is None:
            continue
        bot: CaptainBot = cap["bot"]
        row, col = bot.decide_placement(game["map"])
        ok, msg = gs.place_submarine(game, team, row, col)
        if ok:
            socketio.emit("sub_placed", {"team": team}, room=game_id)
            _emit_bot_chat_team_only(game_id, team, {
                "team": team, "role": "captain", "name": cap["name"],
                "msg": "Submarine deployed",
            })
            if game["phase"] == "playing":
                current = gs.current_team(game)
                socketio.emit("game_phase", {"current_team": current}, room=game_id)
                _broadcast_game_state(game_id)
            acted = True
    return acted


def _bot_playing_step(game_id: str, g: dict, game: dict) -> bool:
    """Execute one pending bot action for the current team. Returns True if acted."""
    if game["phase"] == "ended":
        return False

    team = gs.current_team(game)
    ts   = game["turn_state"]
    sub  = game["submarines"][team]

    # Step 0 — If surfaced and not yet moved, dive first (RULEBOOK: dive before moving)
    if sub["surfaced"] and not ts["moved"]:
        cap = _get_bot_for_role(game_id, team, "captain")
        if cap is not None:
            ok, msg = gs.captain_dive(game, team)
            if ok:
                socketio.emit("dive_announced", {"team": team}, room=game_id)
                _broadcast_game_state(game_id)
                return True

    # Step 0b — If waiting for sonar response and enemy captain is a bot, auto-respond
    if ts.get("waiting_for") == "sonar_response":
        responding_team = gs.other_team(team)
        enemy_cap = _get_bot_for_role(game_id, responding_team, "captain")
        if enemy_cap and enemy_cap.get("bot"):
            return _bot_sonar_respond(game_id, game, responding_team, enemy_cap)
        return False   # waiting for human enemy captain

    # Step 0c — Process team communications before captain acts
    # RO analyzes enemy movements, FM reports systems, Engineer recommends,
    # Captain reads all of it to make an informed decision.
    if not ts["moved"]:
        _process_bot_comms_pre_turn(game_id, team)

    # Step 1 — Captain must move (or surface/weapon) if not yet moved
    if not ts["moved"]:
        cap = _get_bot_for_role(game_id, team, "captain")
        if cap is None:
            return False   # human captain — wait
        acted = _bot_captain_action(game_id, g, game, team, cap)
        if acted:
            # After captain moves, send priority comms to FM and Engineer
            _process_bot_comms_post_move(game_id, team)
        return acted

    # Step 2 — Engineer marks (on normal move OR stealth move)
    has_dir = ts["direction"] is not None or ts.get("stealth_direction") is not None
    if not ts["engineer_done"] and has_dir:
        eng = _get_bot_for_role(game_id, team, "engineer")
        if eng is not None:
            return _bot_engineer_action(game_id, g, game, team, eng)

    # Step 3 — First mate charges (on normal move OR stealth move)
    if not ts["first_mate_done"] and has_dir:
        fm = _get_bot_for_role(game_id, team, "first_mate")
        if fm is not None:
            return _bot_fm_action(game_id, g, game, team, fm)

    # Step 3.5 — Captain may use a weapon system AFTER announcing a course
    # RULEBOOK TBT: "Captain or First Mate can activate a system after each course announcement"
    ok_end, _ = gs.can_end_turn(game, team)
    if ok_end and not ts.get("system_used"):
        cap = _get_bot_for_role(game_id, team, "captain")
        if cap is not None:
            acted = _bot_captain_weapon_action(game_id, g, game, team, cap)
            if acted:
                return True

    # Step 4 — End turn if possible and captain is a bot
    ok, _ = gs.can_end_turn(game, team)
    if ok:
        cap = _get_bot_for_role(game_id, team, "captain")
        if cap is not None:
            return _bot_end_turn(game_id, g, game, team, cap)

    return False


def _bot_captain_action(game_id, g, game, team, cap_player) -> bool:
    """Captain bot takes its action. Returns True if an action was taken."""
    bot: CaptainBot = cap_player["bot"]
    sub = game["submarines"][team]
    enemy_team = "red" if team == "blue" else "blue"
    enemy_health = game["submarines"][enemy_team]["health"]
    name = cap_player["name"]

    action = bot.decide_action(sub, enemy_health, game["map"], game["turn_state"])
    if action is None:
        return False

    atype = action[0]

    if atype == "move":
        direction = action[1]
        ok, msg, events = gs.captain_move(game, team, direction)
        if ok:
            _dispatch_events(game_id, game, events)
            _broadcast_game_state(game_id)
            return True
        # Move failed (trail?) → surface
        ok2, msg2, events2 = gs.captain_surface(game, team)
        if ok2:
            _do_surface_and_dive(game_id, game, team, name, events2)
            return True

    elif atype == "surface":
        ok, msg, events = gs.captain_surface(game, team)
        if ok:
            _do_surface_and_dive(game_id, game, team, name, events)
            return True

    elif atype == "stealth":
        # action = ("stealth", direction, steps)
        direction = action[1] if len(action) > 1 else None
        steps     = action[2] if len(action) > 2 else 0
        if direction and steps > 0:
            ok, msg, events = gs.captain_use_stealth(game, team, direction, steps)
            if ok:
                _dispatch_events(game_id, game, events)
                _broadcast_game_state(game_id)
                return True
        # Stealth failed or no moves — surface
        ok, msg, events = gs.captain_surface(game, team)
        if ok:
            _do_surface_and_dive(game_id, game, team, name, events)
            return True

    return False


def _do_surface(game_id, game, team, bot_name, surface_events):
    """Dispatch surface events. Bots dive at the start of their next turn.
    RULEBOOK: enemy gets 3 bonus turns after surfacing — bot must wait."""
    _dispatch_events(game_id, game, surface_events)
    _broadcast_game_state(game_id)


# Keep alias for any remaining references
_do_surface_and_dive = _do_surface


def _bot_captain_weapon_action(game_id, g, game, team, cap_player) -> bool:
    """Captain bot optionally uses a weapon system AFTER moving (post eng+FM step).
    RULEBOOK TBT: systems activate after each course announcement."""
    bot = cap_player["bot"]
    sub = game["submarines"][team]
    name = cap_player["name"]

    action = bot.decide_weapon_action(sub, game["map"])
    if action is None:
        return False

    atype = action[0]

    if atype == "torpedo":
        tr, tc = action[1], action[2]
        ok, msg, events = gs.captain_fire_torpedo(game, team, tr, tc)
        if ok:
            _dispatch_events(game_id, game, events)
            _broadcast_game_state(game_id)
            return True

    elif atype == "drone":
        sector = action[1]
        ok, msg, events = gs.captain_use_drone(game, team, sector)
        if ok:
            _dispatch_events(game_id, game, events)
            _broadcast_game_state(game_id)
            return True

    return False


def _bot_engineer_action(game_id, g, game, team, eng_player) -> bool:
    """Engineer bot marks a node."""
    bot: EngineerBot = eng_player["bot"]
    ts = game["turn_state"]
    # Use public direction or private stealth direction
    direction = ts["direction"] if ts["direction"] is not None else ts.get("stealth_direction")
    board = game["submarines"][team]["engineering"]

    index = bot.decide_mark(board, direction)
    if index is None:
        # No valid node — skip (server will allow end turn without engineer done? check)
        # Actually game_state requires engineer_done, so mark first available
        available = list(range(6))
        for i in available:
            if not board[direction][i]["marked"]:
                index = i
                break
    if index is None:
        return False  # All marked — shouldn't happen but be safe

    ok, msg, events, _ = gs.engineer_mark(game, team, direction, index)
    if ok:
        # Send board update to human engineer (if any)
        _emit_to_team_role(game_id, team, "engineer", "board_update",
                           {"board": board})
        _dispatch_events(game_id, game, events)
        _broadcast_game_state(game_id)
        desc = bot.describe_mark(direction, index, board)
        _emit_bot_chat_team_only(game_id, team, {
            "team": team, "role": "engineer", "name": eng_player["name"],
            "msg": desc,
        })
        return True
    return False


def _bot_fm_action(game_id, g, game, team, fm_player) -> bool:
    """First-mate bot charges a system."""
    bot: FirstMateBot = fm_player["bot"]
    systems = game["submarines"][team]["systems"]

    system = bot.decide_charge(systems)
    if system is None:
        # Everything full — FM done, allow end turn
        game["turn_state"]["first_mate_done"] = True
        return True

    ok, msg, events = gs.first_mate_charge(game, team, system)
    if ok:
        _dispatch_events(game_id, game, events)
        _emit_to_team_role(game_id, team, "first_mate", "systems_update",
                           {"systems": game["submarines"][team]["systems"]})
        _broadcast_game_state(game_id)
        _emit_bot_chat_team_only(game_id, team, {
            "team": team, "role": "first_mate", "name": fm_player["name"],
            "msg": bot.describe_charge(system),
        })
        return True
    # Charge failed (already full?); mark done
    game["turn_state"]["first_mate_done"] = True
    return True


def _bot_end_turn(game_id, g, game, team, cap_player) -> bool:
    """Captain bot ends the turn."""
    ok, msg, events = gs.end_turn(game, team)
    if ok:
        _dispatch_events(game_id, game, events)
        return True
    return False


def _bot_sonar_respond(game_id, game, responding_team, cap_player) -> bool:
    """Bot captain responds to a sonar query with 1 true and 1 false piece of info."""
    bot = cap_player["bot"]
    own_sub = game["submarines"][responding_team]
    type1, val1, type2, val2 = bot.respond_sonar(own_sub, game["map"])
    ok, msg, events = gs.captain_respond_sonar(game, responding_team, type1, val1, type2, val2)
    if ok:
        _dispatch_events(game_id, game, events)
        _broadcast_game_state(game_id)
        _schedule_bots(game_id)
        return True
    # If validation failed, try a different response (shouldn't happen normally)
    return False


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


@app.route("/spectate")
def spectate():
    """Spectator view — sees full game state, both submarines, all info."""
    game_id = request.args.get("game_id", "").upper().strip()
    name    = request.args.get("name", "").strip()

    if not game_id or not name:
        return redirect(url_for("index"))
    if game_id not in games:
        return redirect(url_for("index"))

    g = games[game_id]
    # If game hasn't started yet, show lobby waiting
    if g["game"] is None:
        return redirect(url_for("lobby", game_id=game_id, name=name))

    map_def = g["game"]["map"]
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
        sector_size=map_def.get("sector_size", map_def.get("sector_width", 5)),
        sector_width=map_def.get("sector_width", map_def.get("sector_size", 5)),
        sector_height=map_def.get("sector_height", map_def.get("sector_size", 5)),
        islands=map_def["islands"],
        col_labels=get_col_labels(map_def["cols"]),
    )
    return render_template(f"{role}.html", **common)


# ── Socket Events — Connection ─────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    pass


@socketio.on("disconnect")
def on_disconnect(*args):
    sid = request.sid
    info = sid_map.pop(sid, None)
    if info:
        game_id  = info["game_id"]
        name     = info["name"]
        is_spec  = info.get("is_spectator", False)
        if is_spec:
            if game_id in games and name in _get_spectators(game_id):
                games[game_id]["spectators"][name]["sid"] = None
                _emit_lobby(game_id)
        else:
            if game_id in games and name in games[game_id]["players"]:
                games[game_id]["players"][name]["sid"] = None
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
        "game":       None,
        "players":    {name: {"name": name, "team": "blue", "role": "",
                               "ready": False, "sid": request.sid,
                               "is_bot": False, "bot": None}},
        "spectators": {},
        "host":       name,
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

    # Handle spectator rejoin
    if name in g.get("spectators", {}):
        g["spectators"][name]["sid"] = request.sid
        sid_map[request.sid] = {"game_id": game_id, "name": name, "is_spectator": True}
        join_room(game_id)
        emit("spectator_ack", {"game_id": game_id, "name": name})
        if g["game"] is not None:
            state = gs.serialize_game(g["game"], perspective_team=None)
            emit("game_state", state)
        else:
            _emit_lobby(game_id)
        return

    if name in g["players"]:
        # Rejoin (reconnect) — update sid, restore state
        g["players"][name]["sid"] = request.sid
        sid_map[request.sid] = {"game_id": game_id, "name": name}
        join_room(game_id)
        emit("join_ack", {"game_id": game_id, "name": name})
        if g["game"] is not None:
            state = gs.serialize_game(g["game"], perspective_team=g["players"][name]["team"])
            emit("game_state", state)
        else:
            _emit_lobby(game_id)
        return

    if g["game"] is not None and g["game"]["phase"] != "lobby":
        return emit("error", {"msg": "Game already in progress"})

    human_count = sum(1 for p in g["players"].values() if not p.get("is_bot"))
    if human_count >= 8:
        return emit("error", {"msg": "Lobby is full (max 8 human players)"})

    if any(p["name"].lower() == name.lower() for p in g["players"].values()):
        return emit("error", {"msg": "Name already taken"})

    g["players"][name] = {"name": name, "team": "red", "role": "",
                          "ready": False, "sid": request.sid,
                          "is_bot": False, "bot": None}
    sid_map[request.sid] = {"game_id": game_id, "name": name}
    join_room(game_id)
    emit("join_ack", {"game_id": game_id, "name": name})
    _emit_lobby(game_id)


@socketio.on("join_as_spectator")
def on_join_as_spectator(data):
    """Join a game as a spectator (no role, full game visibility)."""
    game_id = (data.get("game_id") or "").upper().strip()
    name    = (data.get("name") or "").strip()

    if not game_id or not name:
        return emit("error", {"msg": "game_id and name required"})
    if game_id not in games:
        return emit("error", {"msg": "Game not found"})

    g = games[game_id]

    # If they're currently a player, remove them from players first
    if name in g["players"] and not g["players"][name].get("is_bot"):
        del g["players"][name]

    # Init spectators dict if missing (older games)
    if "spectators" not in g:
        g["spectators"] = {}

    # Duplicate name check (against other spectators and players)
    all_names = (
        set(g["players"].keys()) |
        {s for s in g["spectators"] if s != name}
    )
    if any(n.lower() == name.lower() for n in all_names):
        return emit("error", {"msg": "Name already taken by a player"})

    g["spectators"][name] = {"name": name, "sid": request.sid}
    sid_map[request.sid] = {"game_id": game_id, "name": name, "is_spectator": True}
    join_room(game_id)
    emit("spectator_ack", {"game_id": game_id, "name": name})

    if g["game"] is not None:
        state = gs.serialize_game(g["game"], perspective_team=None)
        emit("game_state", state)

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


@socketio.on("add_bot")
def on_add_bot(data):
    """Host adds a bot to a specific team/role slot."""
    game_id = (data.get("game_id") or "").upper()
    name    = data.get("name", "")      # must be host
    team    = data.get("team", "")
    role    = data.get("role", "")

    if game_id not in games:
        return emit("error", {"msg": "Game not found"})
    g = games[game_id]
    if g["host"] != name:
        return emit("error", {"msg": "Only the host can add bots"})
    if g["game"] is not None:
        return emit("error", {"msg": "Cannot add bots after game starts"})
    if team not in VALID_TEAMS:
        return emit("error", {"msg": "Invalid team"})
    if role not in VALID_ROLES:
        return emit("error", {"msg": "Invalid role"})

    # Check role not already taken on this team
    for p in g["players"].values():
        if p["team"] == team and p["role"] == role:
            return emit("error", {"msg": f"{role} already taken on {team} fleet"})

    # Check total player count
    if len(g["players"]) >= 8:
        return emit("error", {"msg": "Lobby is full (max 8 players)"})

    bot_player = _make_bot_player(team, role)
    # Ensure unique name
    base_name = bot_player["name"]
    counter = 2
    while bot_player["name"] in g["players"]:
        bot_player["name"] = f"{base_name}_{counter}"
        counter += 1

    g["players"][bot_player["name"]] = bot_player
    _emit_lobby(game_id)
    emit("bot_added", {"team": team, "role": role, "name": bot_player["name"]})


@socketio.on("remove_bot")
def on_remove_bot(data):
    """Host removes a bot player."""
    game_id  = (data.get("game_id") or "").upper()
    name     = data.get("name", "")     # host name
    bot_name = data.get("bot_name", "")

    if game_id not in games:
        return emit("error", {"msg": "Game not found"})
    g = games[game_id]
    if g["host"] != name:
        return emit("error", {"msg": "Only the host can remove bots"})
    if g["game"] is not None:
        return emit("error", {"msg": "Cannot remove bots after game starts"})
    if bot_name not in g["players"]:
        return emit("error", {"msg": "Bot not found"})
    if not g["players"][bot_name].get("is_bot"):
        return emit("error", {"msg": "That player is not a bot"})

    del g["players"][bot_name]
    _emit_lobby(game_id)


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

    # Validate and normalize
    rows = int(settings.get("rows", 15))
    cols = int(settings.get("cols", 15))
    sw = int(settings.get("sector_width", 5))
    sh = int(settings.get("sector_height", 5))
    if rows % sh != 0 or cols % sw != 0:
        return emit("error", {"msg": "Dimensions must be divisible by sector size"})

    # Store validated settings with proper types
    validated = {
        "rows": rows,
        "cols": cols,
        "sector_width": sw,
        "sector_height": sh,
        "num_islands": int(settings.get("num_islands", 12)),
        "island_size": int(settings.get("island_size", 2)),
        "islands": settings.get("islands"),
    }
    g["map_settings"] = validated
    socketio.emit("map_settings_update", {"settings": validated}, room=game_id)


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

    teams_present = list({p["team"] for p in g["players"].values() if p["team"]})
    teams_present.sort()
    import random; random.shuffle(teams_present)

    map_settings = g.get("map_settings", DEFAULT_SETTINGS)
    map_def = generate_map(map_settings)
    g["game"] = gs.make_game(map_def)
    g["game"]["turn_order"] = teams_present
    g["game"]["active_team"] = teams_present[0]  # explicit active team for surface-bonus tracking
    g["game"]["phase"] = "placement"

    # Initialize TeamComms for each team
    g["comms"] = {
        "blue": TeamComms("blue"),
        "red":  TeamComms("red"),
    }

    # Initialize bots that need the map definition
    for team in ["blue", "red"]:
        ro = _get_bot_for_role(game_id, team, "radio_operator")
        if ro and ro.get("bot"):
            ro["bot"].initialize(g["game"]["map"])

    socketio.emit("game_started", {
        "map": {
            "rows":        map_def["rows"],
            "cols":        map_def["cols"],
            "sector_width": map_def["sector_width"],
            "sector_height": map_def["sector_height"],
            "sector_size": map_def["sector_size"],
            "islands":     map_def["islands"],
            "col_labels":  get_col_labels(map_def["cols"]),
        },
        "turn_order": teams_present,
    }, room=game_id)

    # Schedule bots to handle placement
    _schedule_bots(game_id)


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

    if g["game"]["phase"] == "playing":
        current = gs.current_team(g["game"])
        socketio.emit("game_phase", {"current_team": current}, room=game_id)
        _broadcast_game_state(game_id)

    # Schedule bots to handle other team's placement or first move
    _schedule_bots(game_id)


# ── Socket Events — Captain ───────────────────────────────────────────────────

def _get_captain(game_id, name):
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

    # Auto-dive: surfacing clears trail+board, dive is automatic
    ok2, msg2 = gs.captain_dive(game, p["team"])
    if ok2:
        emit("dive_ack", {})
        socketio.emit("dive_announced", {"team": p["team"]}, room=game_id)

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
    _emit_to_team_role(game_id, p["team"], "captain", "systems_update",
                       {"systems": game["submarines"][p["team"]]["systems"]})
    _emit_to_team_role(game_id, p["team"], "first_mate", "systems_update",
                       {"systems": game["submarines"][p["team"]]["systems"]})
    _check_turn_auto_advance(game_id, game)


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
    _check_turn_auto_advance(game_id, game)


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
    # mine list is refreshed via game_state broadcast in _check_turn_auto_advance
    _check_turn_auto_advance(game_id, game)


@socketio.on("sonar_respond")
def on_sonar_respond(data):
    """Enemy captain responds to a sonar query with 2 pieces of info (1 true, 1 false)."""
    game_id = (data.get("game_id") or "").upper()
    name    = data.get("name", "")
    type1   = data.get("type1", "")
    val1    = data.get("val1")
    type2   = data.get("type2", "")
    val2    = data.get("val2")

    g = games.get(game_id)
    if not g or g["game"] is None:
        return emit("error", {"msg": "Game not found"})

    p = _get_player(game_id, name)
    if not p or p["role"] != "captain":
        return emit("error", {"msg": "Only the enemy captain can respond to sonar"})

    # Validate this captain is on the responding (enemy) team
    activating_team = gs.current_team(g["game"])
    if p["team"] == activating_team:
        return emit("error", {"msg": "The activating team's captain cannot respond to their own sonar"})

    # Convert val1/val2 to int if they're numeric
    try:
        if type1 == "row" or type1 == "col":
            val1 = int(val1)
        elif type1 == "sector":
            val1 = int(val1)
        if type2 == "row" or type2 == "col":
            val2 = int(val2)
        elif type2 == "sector":
            val2 = int(val2)
    except (TypeError, ValueError):
        return emit("error", {"msg": "Invalid value types for sonar response"})

    ok, msg, events = gs.captain_respond_sonar(g["game"], p["team"], type1, val1, type2, val2)
    if not ok:
        return emit("error", {"msg": msg})

    _dispatch_events(game_id, g["game"], events)
    _check_turn_auto_advance(game_id, g["game"])


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
    _check_turn_auto_advance(game_id, game)


@socketio.on("captain_stealth")
def on_captain_stealth(data):
    game_id   = (data.get("game_id") or "").upper()
    name      = data.get("name", "")
    direction = data.get("direction", "")
    steps     = int(data.get("steps", 0))
    p, game = _get_captain(game_id, name)
    if not p:
        return

    ok, msg, events = gs.captain_use_stealth(game, p["team"], direction, steps)
    if not ok:
        return emit("error", {"msg": msg})

    _dispatch_events(game_id, game, events)
    _check_turn_auto_advance(game_id, game)


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
    # Schedule bots for next team's turn
    _schedule_bots(game_id)


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

    # _dispatch_events handles systems_update (with reason="charge") via system_charged event
    _dispatch_events(game_id, g["game"], events)
    _check_turn_auto_advance(game_id, g["game"])


@socketio.on("first_mate_sonar")
def on_first_mate_sonar(data):
    """First mate activates sonar (interactive: enemy captain must respond)."""
    game_id = (data.get("game_id") or "").upper()
    name    = data.get("name", "")

    g = games.get(game_id)
    if not g or g["game"] is None:
        return emit("error", {"msg": "Game not found"})

    p = _get_player(game_id, name)
    if not p or p["role"] != "first_mate":
        return emit("error", {"msg": "Only the first mate can use sonar"})
    if g["game"]["phase"] != "playing":
        return emit("error", {"msg": "Game is not in playing phase"})

    ok, msg, events = gs.captain_use_sonar(g["game"], p["team"])
    if not ok:
        return emit("error", {"msg": msg})

    _dispatch_events(game_id, g["game"], events)
    _check_turn_auto_advance(game_id, g["game"])


@socketio.on("first_mate_drone")
def on_first_mate_drone(data):
    """First mate activates drone (green system — operated by FM, not captain)."""
    game_id    = (data.get("game_id") or "").upper()
    name       = data.get("name", "")
    ask_sector = data.get("sector")

    g = games.get(game_id)
    if not g or g["game"] is None:
        return emit("error", {"msg": "Game not found"})

    p = _get_player(game_id, name)
    if not p or p["role"] != "first_mate":
        return emit("error", {"msg": "Only the first mate can use drone"})
    if g["game"]["phase"] != "playing":
        return emit("error", {"msg": "Game is not in playing phase"})

    ok, msg, events = gs.captain_use_drone(g["game"], p["team"], ask_sector)
    if not ok:
        return emit("error", {"msg": msg})

    _dispatch_events(game_id, g["game"], events)
    _check_turn_auto_advance(game_id, g["game"])


@socketio.on("ro_canvas_stroke")
def on_ro_canvas_stroke(data):
    """Radio operator canvas stroke — relay to spectators only."""
    game_id = (data.get("game_id") or "").upper()
    if game_id not in games:
        return
    # Relay to all connected spectators
    for spec in _get_spectators(game_id).values():
        if spec.get("sid"):
            socketio.emit("ro_canvas_stroke", data, room=spec["sid"])


@socketio.on("ro_pan")
def on_ro_pan(data):
    """Radio operator pan offset — relay to spectators so they can sync the RO canvas overlay."""
    game_id = (data.get("game_id") or "").upper()
    if game_id not in games:
        return
    for spec in _get_spectators(game_id).values():
        if spec.get("sid"):
            socketio.emit("ro_pan", data, room=spec["sid"])


# ── Socket Events — Player Comms ─────────────────────────────────────────

@socketio.on("player_comms")
def on_player_comms(data):
    """Human player sends a quick-action comms message to bot teammates."""
    VALID_COMMS = {
        "captain": {"request_position_report", "set_charge_priority", "set_system_protect"},
        "radio_operator": {"report_positions"},
        "first_mate": {"status_report"},
        "engineer": {"recommend_directions"},
    }
    VALID_SYSTEMS = {"torpedo", "mine", "sonar", "drone", "stealth"}

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

    allowed = VALID_COMMS.get(p["role"], set())
    if msg_type not in allowed:
        return emit("error", {"msg": "Not authorized for this message type"})

    if msg_type in ("set_charge_priority", "set_system_protect"):
        if payload.get("system") not in VALID_SYSTEMS:
            return emit("error", {"msg": "Invalid system name"})

    comms = _get_team_comms(game_id, p["team"])
    if not comms:
        return emit("error", {"msg": "Comms not available"})

    comms.post_from_human(p["role"], msg_type, payload)

    # Echo the message to the team's comms log
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


# ── Auto-advance helper ───────────────────────────────────────────────────────

def _check_turn_auto_advance(game_id, game):
    """Broadcast state and trigger bot actions if needed.
    Also detects BLACKOUT (no valid moves) and auto-surfaces the active submarine."""
    # RULEBOOK blackout: if captain has no valid moves at start of their turn, must surface
    if (game["phase"] == "playing"
            and not game["turn_state"]["moved"]
            and not game["turn_state"]["waiting_for"]):
        team = gs.current_team(game)
        sub = game["submarines"][team]
        if not sub["surfaced"] and not gs.has_valid_move(game, team):
            # Force surface (blackout) + auto-dive
            ok, msg, events = gs.captain_surface(game, team)
            if ok:
                socketio.emit("blackout_announced",
                              {"team": team, "msg": "No valid moves — surfacing!"}, room=game_id)
                _dispatch_events(game_id, game, events)
                ok2, _ = gs.captain_dive(game, team)
                if ok2:
                    socketio.emit("dive_announced", {"team": team}, room=game_id)

    _broadcast_game_state(game_id)
    _schedule_bots(game_id)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    print(f"Starting Admiral Radar server on http://localhost:{port}")
    socketio.run(app, host="0.0.0.0", port=port)
