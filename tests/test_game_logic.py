"""
Comprehensive unit tests for Captain Sonar game logic.
Tests game_state.py without server or sockets.

Run:  python -m pytest tests/test_game_logic.py -v
  or: python tests/test_game_logic.py
"""
import sys
import os
# Fix Windows console encoding (emojis in output)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import game_state as gs

# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def fresh_game():
    """Return a fresh game with map alpha."""
    return gs.make_game("alpha")


def place_both(game, blue_pos=(5, 4), red_pos=(10, 10)):
    """Place both submarines so game enters playing phase."""
    ok1, _ = gs.place_submarine(game, "blue", *blue_pos)
    ok2, _ = gs.place_submarine(game, "red",  *red_pos)
    assert ok1 and ok2, "Placement failed in helper"
    assert game["phase"] == "playing"
    return game


def full_turn_blue(game, direction="east"):
    """Complete one full blue turn: move + engineer + fm + end turn."""
    ok, msg, _ = gs.captain_move(game, "blue", direction)
    assert ok, f"Move failed: {msg}"
    ok, msg, _, _ = gs.engineer_mark(game, "blue", direction, 0)
    assert ok, f"Engineer mark failed: {msg}"
    ok, msg, _ = gs.first_mate_charge(game, "blue", "torpedo")
    assert ok, f"FM charge failed: {msg}"
    ok, msg, _ = gs.end_turn(game, "blue")
    assert ok, f"End turn failed: {msg}"


# ────────────────────────────────────────────────────────────────────────────
# 1. Placement
# ────────────────────────────────────────────────────────────────────────────

def test_placement_valid():
    game = fresh_game()
    ok, err = gs.place_submarine(game, "blue", 5, 4)
    assert ok, err
    assert game["submarines"]["blue"]["position"] == [5, 4]


def test_placement_on_island_rejected():
    game = fresh_game()
    # Map alpha islands – find one
    island = list(game["island_set"])[0]
    ok, err = gs.place_submarine(game, "blue", island[0], island[1])
    assert not ok, "Should not be able to place on island"


def test_placement_out_of_bounds_rejected():
    game = fresh_game()
    ok, err = gs.place_submarine(game, "blue", -1, 0)
    assert not ok


def test_placement_twice_rejected():
    game = fresh_game()
    gs.place_submarine(game, "blue", 5, 4)
    ok, err = gs.place_submarine(game, "blue", 5, 5)
    assert not ok, "Should not allow placing twice"


def test_both_placed_starts_game():
    game = fresh_game()
    assert game["phase"] == "placement"
    gs.place_submarine(game, "blue", 5, 4)
    assert game["phase"] == "placement"
    gs.place_submarine(game, "red", 10, 10)
    assert game["phase"] == "playing"


# ────────────────────────────────────────────────────────────────────────────
# 2. Movement
# ────────────────────────────────────────────────────────────────────────────

def test_move_valid():
    game = place_both(fresh_game())
    pos_before = list(game["submarines"]["blue"]["position"])
    ok, msg, events = gs.captain_move(game, "blue", "east")
    assert ok, msg
    pos_after = game["submarines"]["blue"]["position"]
    assert pos_after[1] == pos_before[1] + 1


def test_move_north_south_west_east():
    """All four directions should work from a safe centre position (no adjacent islands)."""
    # (8,9) has no adjacent islands on map alpha
    for direction, (dr, dc) in [("north",(-1,0)),("south",(1,0)),("west",(0,-1)),("east",(0,1))]:
        game = place_both(fresh_game(), blue_pos=(8, 9))
        ok, msg, _ = gs.captain_move(game, "blue", direction)
        assert ok, f"{direction} failed: {msg}"
        pos = game["submarines"]["blue"]["position"]
        assert pos == [8 + dr, 9 + dc]


def test_move_blocked_by_island():
    game = place_both(fresh_game())
    # Find an island and place sub adjacent to it
    island = sorted(game["island_set"])[0]
    r, c = island
    # Try to place sub just north of island and move south into it
    if r > 0 and (r-1, c) not in game["island_set"]:
        ok, _ = gs.place_submarine(fresh_game(), "blue", r-1, c)
        g2 = fresh_game()
        place_both(g2, blue_pos=(r-1, c))
        ok, msg, _ = gs.captain_move(g2, "blue", "south")
        assert not ok, "Should not move into island"


def test_move_cannot_revisit_trail():
    game = place_both(fresh_game(), blue_pos=(5, 4))
    # Move east, then try to move west (back to start)
    gs.captain_move(game, "blue", "east")
    game["turn_state"]["moved"] = False   # hack to allow second move for testing
    game["turn_state"]["direction"] = None
    ok, msg, _ = gs.captain_move(game, "blue", "west")
    assert not ok, "Should not be able to revisit trail"


def test_move_not_your_turn():
    game = place_both(fresh_game())
    ok, msg, _ = gs.captain_move(game, "red", "east")
    assert not ok
    assert "Not your turn" in msg


def test_cannot_move_twice():
    game = place_both(fresh_game())
    gs.captain_move(game, "blue", "east")
    ok, msg, _ = gs.captain_move(game, "blue", "north")
    assert not ok
    assert "Already moved" in msg


# ────────────────────────────────────────────────────────────────────────────
# 3. Turn Gating — the critical fix
# ────────────────────────────────────────────────────────────────────────────

def test_cannot_end_turn_without_moving():
    game = place_both(fresh_game())
    ok, msg, _ = gs.end_turn(game, "blue")
    assert not ok
    assert "navigate" in msg.lower() or "decloak" in msg.lower()


def test_cannot_end_turn_without_engineer_mark():
    """After a directional move, engineer must mark before end turn."""
    game = place_both(fresh_game())
    gs.captain_move(game, "blue", "east")
    # Don't mark engineer
    ok, msg, _ = gs.end_turn(game, "blue")
    assert not ok, "Should require engineer mark"
    assert "engineer" in msg.lower()


def test_cannot_end_turn_without_fm_charge():
    """After directional move + engineer mark, FM must charge before end turn."""
    game = place_both(fresh_game())
    gs.captain_move(game, "blue", "east")
    gs.engineer_mark(game, "blue", "east", 0)
    # Don't charge FM
    ok, msg, _ = gs.end_turn(game, "blue")
    assert not ok, "Should require FM charge"
    assert "tactical officer" in msg.lower()


def test_can_end_turn_after_all_roles():
    """Turn ends only when moved + engineer + FM all done."""
    game = place_both(fresh_game())
    gs.captain_move(game, "blue", "east")
    gs.engineer_mark(game, "blue", "east", 0)
    gs.first_mate_charge(game, "blue", "torpedo")
    ok, msg, _ = gs.end_turn(game, "blue")
    assert ok, msg


def test_can_end_turn_after_surface_immediately():
    """Surface auto-sets engineer_done + first_mate_done, so end turn allowed."""
    game = place_both(fresh_game())
    gs.captain_surface(game, "blue")
    ok, msg, _ = gs.end_turn(game, "blue")
    assert ok, f"Should end turn after surface: {msg}"


def test_turn_switches_to_red():
    game = place_both(fresh_game())
    assert gs.current_team(game) == "blue"
    full_turn_blue(game)
    assert gs.current_team(game) == "red"


def test_turn_state_reset_after_end_turn():
    game = place_both(fresh_game())
    full_turn_blue(game)
    ts = game["turn_state"]
    assert ts["moved"]           == False
    assert ts["engineer_done"]   == False
    assert ts["first_mate_done"] == False
    assert ts["direction"]       is None


# ────────────────────────────────────────────────────────────────────────────
# 4. Surface
# ────────────────────────────────────────────────────────────────────────────

def test_surface_no_hp_cost():
    """RULEBOOK (TBT): surfacing does NOT cost HP."""
    game = place_both(fresh_game())
    gs.captain_surface(game, "blue")
    assert game["submarines"]["blue"]["health"] == 4


def test_surface_clears_trail():
    game = place_both(fresh_game(), blue_pos=(5, 4))
    gs.captain_move(game, "blue", "east")
    gs.engineer_mark(game, "blue", "east", 0)
    gs.first_mate_charge(game, "blue", "torpedo")
    gs.end_turn(game, "blue")
    # Red's turn — force back to blue without using broken turn_index hack
    game["active_team"] = "blue"
    game["turn_state"] = gs.make_turn_state()
    # Now surface blue
    gs.captain_surface(game, "blue")
    trail = game["submarines"]["blue"]["trail"]
    # Trail should only have current position (cleared)
    assert len(trail) == 1


def test_surface_announces_sector():
    game = place_both(fresh_game(), blue_pos=(5, 4))
    ok, _, events = gs.captain_surface(game, "blue")
    assert ok
    surfaced_ev = next(e for e in events if e["type"] == "surfaced")
    assert "sector" in surfaced_ev
    assert 1 <= surfaced_ev["sector"] <= 4  # TBT map alpha has 4 sectors (2×2)


def test_dive_clears_surfaced_flag():
    game = place_both(fresh_game())
    gs.captain_surface(game, "blue")
    assert game["submarines"]["blue"]["surfaced"] == True
    ok, _ = gs.captain_dive(game, "blue")
    assert ok
    assert game["submarines"]["blue"]["surfaced"] == False


# ────────────────────────────────────────────────────────────────────────────
# 5. Engineer
# ────────────────────────────────────────────────────────────────────────────

def test_engineer_must_mark_correct_direction():
    game = place_both(fresh_game())
    gs.captain_move(game, "blue", "east")
    ok, msg, _, _ = gs.engineer_mark(game, "blue", "north", 0)
    assert not ok
    assert "east" in msg.lower()


def test_engineer_cannot_mark_twice():
    game = place_both(fresh_game())
    gs.captain_move(game, "blue", "east")
    gs.engineer_mark(game, "blue", "east", 0)
    ok, msg, _, _ = gs.engineer_mark(game, "blue", "east", 1)
    assert not ok
    assert "already" in msg.lower()


def test_engineer_cannot_mark_without_move():
    game = place_both(fresh_game())
    ok, msg, _, _ = gs.engineer_mark(game, "blue", "east", 0)
    assert not ok
    assert "commander" in msg.lower() or "navigated" in msg.lower()


def test_engineer_marks_set_done_flag():
    game = place_both(fresh_game())
    gs.captain_move(game, "blue", "east")
    gs.engineer_mark(game, "blue", "east", 0)
    assert game["turn_state"]["engineer_done"] == True


def test_engineer_circuit_clear_no_damage():
    """Marking all C1 nodes should clear them without causing damage."""
    game = place_both(fresh_game())
    board = game["submarines"]["blue"]["engineering"]
    # C1 nodes: west[0], north[0], south[0], east[0]  — one per direction, all at index 0
    # Mark first 3 manually then verify circuit clears on 4th
    board["west"][0]["marked"]  = True
    board["north"][0]["marked"] = True
    board["south"][0]["marked"] = True
    # Mark east[0] to complete C1
    events = gs.engineer_mark_node(board, "east", 0)
    circuit_ev = [e for e in events if e["type"] == "circuit_cleared"]
    damage_ev  = [e for e in events if "damage" in e["type"]]
    assert len(circuit_ev) == 1, f"Expected circuit_cleared, got: {events}"
    assert len(damage_ev)  == 0, f"Expected no damage on circuit clear, got: {events}"
    # All C1 nodes should be unmarked now
    assert board["west"][0]["marked"]  == False
    assert board["east"][0]["marked"]  == False
    assert board["north"][0]["marked"] == False
    assert board["south"][0]["marked"] == False


def test_direction_damage_on_full_column():
    """Filling all 6 nodes in a direction causes 1 damage and clears that direction."""
    game = place_both(fresh_game())
    board = game["submarines"]["blue"]["engineering"]
    # Mark 5 east nodes
    for i in range(5):
        board["east"][i]["marked"] = True
    # Mark the last one
    events = gs.engineer_mark_node(board, "east", 5)
    dmg_ev = [e for e in events if e["type"] == "direction_damage"]
    assert len(dmg_ev) == 1
    assert dmg_ev[0]["damage"] == 1
    # East column should be cleared
    assert all(not board["east"][i]["marked"] for i in range(6))


# ────────────────────────────────────────────────────────────────────────────
# 6. First Mate
# ────────────────────────────────────────────────────────────────────────────

def test_fm_charge_increments_system():
    game = place_both(fresh_game())
    gs.captain_move(game, "blue", "east")
    gs.engineer_mark(game, "blue", "east", 0)
    assert game["submarines"]["blue"]["systems"]["torpedo"] == 0
    ok, msg, _ = gs.first_mate_charge(game, "blue", "torpedo")
    assert ok, msg
    assert game["submarines"]["blue"]["systems"]["torpedo"] == 1


def test_fm_cannot_charge_without_move():
    game = place_both(fresh_game())
    ok, msg, _ = gs.first_mate_charge(game, "blue", "torpedo")
    assert not ok


def test_fm_cannot_charge_twice():
    game = place_both(fresh_game())
    gs.captain_move(game, "blue", "east")
    gs.engineer_mark(game, "blue", "east", 0)
    gs.first_mate_charge(game, "blue", "torpedo")
    ok, msg, _ = gs.first_mate_charge(game, "blue", "sonar")
    assert not ok


def test_fm_cannot_overcharge():
    game = place_both(fresh_game())
    # Max torpedo = 6; manually set to max
    game["submarines"]["blue"]["systems"]["torpedo"] = 6
    gs.captain_move(game, "blue", "east")
    gs.engineer_mark(game, "blue", "east", 0)
    ok, msg, _ = gs.first_mate_charge(game, "blue", "torpedo")
    assert not ok
    assert "charged" in msg.lower()


def test_fm_system_ready_at_max():
    """A system is ready when charge == max_charge."""
    game = place_both(fresh_game())
    # Torpedo needs 6 charges
    for i in range(6):
        if i > 0:
            # Advance to blue's turn via surface hack
            game["turn_state"]["moved"] = False
            game["turn_state"]["direction"] = "east"
            game["turn_state"]["engineer_done"] = False
            game["turn_state"]["first_mate_done"] = False
            game["turn_index"] = 0   # keep on blue
        gs.captain_move(game, "blue", "east")
        gs.engineer_mark(game, "blue", "east", 0)
        gs.first_mate_charge(game, "blue", "torpedo")
        if i < 5:
            # hack turn back to blue
            game["turn_state"] = gs.make_turn_state()
            game["turn_index"] = 0
    assert game["submarines"]["blue"]["systems"]["torpedo"] == 6


# ────────────────────────────────────────────────────────────────────────────
# 7. Torpedo
# ────────────────────────────────────────────────────────────────────────────

def test_torpedo_direct_hit_2_damage():
    game = place_both(fresh_game(), blue_pos=(5,4), red_pos=(5,6))
    game["submarines"]["blue"]["systems"]["torpedo"] = 6
    game["turn_state"]["moved"] = True   # TBT: system used after announcing course
    ok, msg, events = gs.captain_fire_torpedo(game, "blue", 5, 6)
    assert ok, msg
    dmg = next(e for e in events if e["type"] == "damage")
    assert dmg["amount"] == 2
    assert game["submarines"]["red"]["health"] == 2


def test_torpedo_adjacent_1_damage():
    """Torpedo Chebyshev distance 1 from target deals 1 damage."""
    # blue at (5,4), red at (5,6), fire at (5,5)
    # Red: Chebyshev dist from (5,5) to (5,6) = 1 → 1 damage
    game = place_both(fresh_game(), blue_pos=(5,4), red_pos=(5,6))
    game["submarines"]["blue"]["systems"]["torpedo"] = 6
    game["turn_state"]["moved"] = True   # TBT: system used after announcing course
    ok, msg, events = gs.captain_fire_torpedo(game, "blue", 5, 5)
    assert ok, msg
    dmg = next(e for e in events if e["type"] == "damage" and e["team"] == "red")
    assert dmg["amount"] == 1


def test_torpedo_out_of_range():
    game = place_both(fresh_game(), blue_pos=(0,0), red_pos=(0,5))
    game["submarines"]["blue"]["systems"]["torpedo"] = 6
    game["turn_state"]["moved"] = True   # TBT: system used after announcing course
    ok, msg, _ = gs.captain_fire_torpedo(game, "blue", 0, 5)
    assert not ok
    assert "range" in msg.lower()


def test_torpedo_not_charged():
    game = place_both(fresh_game())
    game["turn_state"]["moved"] = True   # TBT: system used after announcing course
    ok, msg, _ = gs.captain_fire_torpedo(game, "blue", 5, 5)
    assert not ok
    assert "charged" in msg.lower()


def test_game_over_when_health_zero():
    game = place_both(fresh_game(), blue_pos=(5,4), red_pos=(5,6))
    game["submarines"]["red"]["health"] = 2
    game["submarines"]["blue"]["systems"]["torpedo"] = 6
    game["turn_state"]["moved"] = True   # TBT: system used after announcing course
    ok, _, events = gs.captain_fire_torpedo(game, "blue", 5, 6)
    assert ok
    game_over = next((e for e in events if e["type"] == "game_over"), None)
    assert game_over is not None
    assert game_over["winner"] == "blue"
    assert game["phase"] == "ended"


# ────────────────────────────────────────────────────────────────────────────
# 8. Mine
# ────────────────────────────────────────────────────────────────────────────

def test_mine_place_adjacent():
    game = place_both(fresh_game(), blue_pos=(5,4))
    game["submarines"]["blue"]["systems"]["mine"] = 6
    game["turn_state"]["moved"] = True   # TBT: system used after announcing course
    ok, msg, _ = gs.captain_place_mine(game, "blue", 5, 5)
    assert ok, msg
    assert [5, 5] in game["submarines"]["blue"]["mines"]


def test_mine_place_non_adjacent_rejected():
    game = place_both(fresh_game(), blue_pos=(5,4))
    game["submarines"]["blue"]["systems"]["mine"] = 6
    ok, msg, _ = gs.captain_place_mine(game, "blue", 5, 6)  # 2 cells away
    assert not ok


def test_mine_detonate_deals_damage():
    game = place_both(fresh_game(), blue_pos=(5,4), red_pos=(5,6))
    game["submarines"]["blue"]["systems"]["mine"] = 6
    game["turn_state"]["moved"] = True   # TBT: systems activate after course announced
    gs.captain_place_mine(game, "blue", 5, 5)
    # Detonate the mine just placed at index 0
    # Red at (5,6): Chebyshev distance from (5,5) to (5,6) = 1 → 1 damage
    ok, msg, events = gs.captain_detonate_mine(game, "blue", 0)
    assert ok, msg
    dmg = next((e for e in events if e["type"] == "damage" and e["team"] == "red"), None)
    assert dmg is not None


# ────────────────────────────────────────────────────────────────────────────
# 9. Stealth
# ────────────────────────────────────────────────────────────────────────────

def test_stealth_valid():
    game = place_both(fresh_game(), blue_pos=(5,4))
    game["submarines"]["blue"]["systems"]["stealth"] = 4
    ok, msg, events = gs.captain_use_stealth(game, "blue", "east", 2)
    assert ok, msg
    pos = game["submarines"]["blue"]["position"]
    assert pos == [5, 6]


def test_stealth_sets_eng_fm_done():
    """Stealth does NOT auto-set engineer/FM done — they still must act."""
    game = place_both(fresh_game(), blue_pos=(5,4))
    game["submarines"]["blue"]["systems"]["stealth"] = 4
    gs.captain_use_stealth(game, "blue", "east", 1)
    # Rulebook: engineer marks silently in stealth direction; FM charges one system
    assert game["turn_state"]["engineer_done"] == False
    assert game["turn_state"]["first_mate_done"] == False


def test_stealth_no_direction_set():
    """After stealth, engineer marks stealth dir and FM charges before end turn."""
    game = place_both(fresh_game(), blue_pos=(5,4))
    game["submarines"]["blue"]["systems"]["stealth"] = 4
    gs.captain_use_stealth(game, "blue", "east", 1)
    # Engineer marks in the (private) stealth direction; FM charges a system
    gs.engineer_mark(game, "blue", "east", 0)
    gs.first_mate_charge(game, "blue", "torpedo")
    ok, msg, _ = gs.end_turn(game, "blue")
    assert ok, msg


def test_stealth_max_4_moves():
    game = place_both(fresh_game(), blue_pos=(5,4))
    game["submarines"]["blue"]["systems"]["stealth"] = 4
    ok, msg, _ = gs.captain_use_stealth(game, "blue", "east", 5)
    assert not ok
    assert "4" in msg


def test_stealth_straight_line_only():
    """Stealth must be a single direction — mixed directions not possible with new API."""
    game = place_both(fresh_game(), blue_pos=(5,4))
    game["submarines"]["blue"]["systems"]["stealth"] = 4
    # Invalid direction string
    ok, msg, _ = gs.captain_use_stealth(game, "blue", "diagonal", 1)
    assert not ok
    assert "direction" in msg.lower() or "invalid" in msg.lower()


def test_stealth_zero_steps():
    """Stealth with 0 steps is valid (stay in place); eng+FM must still act."""
    game = place_both(fresh_game(), blue_pos=(5,4))
    game["submarines"]["blue"]["systems"]["stealth"] = 4
    ok, msg, events = gs.captain_use_stealth(game, "blue", "east", 0)
    assert ok, msg
    # Position unchanged
    pos = game["submarines"]["blue"]["position"]
    assert pos == [5, 4]
    # Stealth does NOT auto-set eng/FM done — they must still mark/charge
    assert game["turn_state"]["engineer_done"] == False
    assert game["turn_state"]["first_mate_done"] == False


def test_stealth_cannot_revisit():
    """Stealth cannot pass through a previously visited cell."""
    game = place_both(fresh_game(), blue_pos=(5,4))
    game["submarines"]["blue"]["systems"]["stealth"] = 4
    # Move east once normally, then try stealth back west through own trail
    gs.captain_move(game, "blue", "east")
    gs.engineer_mark(game, "blue", "east", 1)  # idx 1 = red (torpedo/mine), not yellow (stealth)
    gs.first_mate_charge(game, "blue", "torpedo")
    gs.end_turn(game, "blue")
    # Red's turn — force back to blue properly (active_team drives current_team())
    game["active_team"] = "blue"
    game["turn_state"] = gs.make_turn_state()
    # Blue stealth west would pass through (5,4) which is in trail
    game["submarines"]["blue"]["systems"]["stealth"] = 4
    ok, msg, _ = gs.captain_use_stealth(game, "blue", "west", 2)
    assert not ok
    assert "revisit" in msg.lower() or "cannot" in msg.lower()


# ────────────────────────────────────────────────────────────────────────────
# 10. Sonar & Drone
# ────────────────────────────────────────────────────────────────────────────

def test_sonar_result_has_correct_format():
    game = place_both(fresh_game(), blue_pos=(5,4), red_pos=(10,10))
    game["submarines"]["blue"]["systems"]["sonar"] = 6
    game["turn_state"]["moved"] = True   # TBT: system used after announcing course
    ok, msg, events = gs.captain_use_sonar(game, "blue")
    assert ok, msg
    # Should produce sonar-related events (sonar_activated)
    assert len(events) > 0


def test_drone_result_boolean():
    game = place_both(fresh_game(), blue_pos=(5,4), red_pos=(10,10))
    game["submarines"]["blue"]["systems"]["drone"] = 6
    game["turn_state"]["moved"] = True   # TBT: system used after announcing course
    ok, msg, events = gs.captain_use_drone(game, "blue", 5)
    assert ok, msg
    # Should produce drone-related events (drone_result with in_sector bool)
    drone_ev = next((e for e in events if e["type"] == "drone_result"), None)
    assert drone_ev is not None
    assert isinstance(drone_ev["in_sector"], bool)


# ────────────────────────────────────────────────────────────────────────────
# Run
# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import traceback
    tests = [
        # Placement
        test_placement_valid, test_placement_on_island_rejected,
        test_placement_out_of_bounds_rejected, test_placement_twice_rejected,
        test_both_placed_starts_game,
        # Movement
        test_move_valid, test_move_north_south_west_east,
        test_move_blocked_by_island, test_move_cannot_revisit_trail,
        test_move_not_your_turn, test_cannot_move_twice,
        # Turn gating
        test_cannot_end_turn_without_moving,
        test_cannot_end_turn_without_engineer_mark,
        test_cannot_end_turn_without_fm_charge,
        test_can_end_turn_after_all_roles,
        test_can_end_turn_after_surface_immediately,
        test_turn_switches_to_red, test_turn_state_reset_after_end_turn,
        # Surface
        test_surface_no_hp_cost, test_surface_clears_trail,
        test_surface_announces_sector, test_dive_clears_surfaced_flag,
        # Engineer
        test_engineer_must_mark_correct_direction,
        test_engineer_cannot_mark_twice,
        test_engineer_cannot_mark_without_move,
        test_engineer_marks_set_done_flag,
        test_engineer_circuit_clear_no_damage,
        test_direction_damage_on_full_column,
        # First mate
        test_fm_charge_increments_system, test_fm_cannot_charge_without_move,
        test_fm_cannot_charge_twice, test_fm_cannot_overcharge,
        test_fm_system_ready_at_max,
        # Torpedo
        test_torpedo_direct_hit_2_damage, test_torpedo_adjacent_1_damage,
        test_torpedo_out_of_range, test_torpedo_not_charged,
        test_game_over_when_health_zero,
        # Mine
        test_mine_place_adjacent, test_mine_place_non_adjacent_rejected,
        test_mine_detonate_deals_damage,
        # Stealth
        test_stealth_valid, test_stealth_sets_eng_fm_done,
        test_stealth_no_direction_set, test_stealth_max_4_moves,
        test_stealth_straight_line_only, test_stealth_zero_steps,
        test_stealth_cannot_revisit,
        # Sonar/Drone
        test_sonar_result_has_correct_format, test_drone_result_boolean,
    ]

    passed = 0
    failed = 0
    errors = []
    for fn in tests:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ❌ {fn.__name__}: {e}")
            errors.append((fn.__name__, traceback.format_exc()))
            failed += 1

    print(f"\n{'='*55}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    if errors:
        print("\nFailure details:")
        for name, tb in errors:
            print(f"\n--- {name} ---")
            print(tb)
    else:
        print("ALL TESTS PASSED!")
    sys.exit(0 if failed == 0 else 1)
