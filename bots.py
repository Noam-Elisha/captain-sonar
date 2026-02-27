"""
Admiral Radar — Communication-aware AI bots for all 4 roles.

Each bot only has access to:
  1. Information on their board (role-appropriate game state)
  2. Communications received through TeamComms

Bots communicate via TeamComms:
  RadioOperatorBot  → Captain:  enemy position estimates
  CaptainBot        → FM:       charge priority
  CaptainBot        → Engineer: system protection priority
  FirstMateBot      → Captain:  system availability status
  EngineerBot       → Captain:  direction recommendations
"""

from __future__ import annotations   # enables PEP 604 | syntax on Python 3.8+

import random
import math
from typing import Optional, List, Tuple
from collections import Counter
from maps import get_sector
from game_state import (
    ENGINEERING_LAYOUT, CIRCUITS, RADIATION_NODES, SYSTEM_MAX_CHARGE,
    SYSTEM_COLORS, direction_delta, get_available_nodes,
)

DIRECTIONS = ["north", "south", "east", "west"]


# ── Utility helpers ────────────────────────────────────────────────────────────

def _get_valid_moves(row, col, trail_set, island_set, rows, cols, mine_set=None):
    """Return list of (direction, new_row, new_col) that are legal moves."""
    valid = []
    for d in DIRECTIONS:
        dr, dc = direction_delta(d)
        nr, nc = row + dr, col + dc
        if nr < 0 or nr >= rows or nc < 0 or nc >= cols:
            continue
        if (nr, nc) in island_set:
            continue
        if (nr, nc) in trail_set:
            continue
        if mine_set and (nr, nc) in mine_set:
            continue   # RULEBOOK: cannot move into own mine
        valid.append((d, nr, nc))
    return valid


def _count_future_moves(row, col, trail_set, island_set, rows, cols):
    return len(_get_valid_moves(row, col, trail_set, island_set, rows, cols))


# ── Radio Operator Bot ────────────────────────────────────────────────────────

class RadioOperatorBot:
    """
    Position-tracking radio operator.

    Maintains a set of possible enemy positions based on publicly
    announced information received through the communication system:
      - Enemy move directions (not stealth)
      - Enemy surface events (sector known, trail resets)
      - Enemy torpedo fire (exact coords → enemy within range 4)
      - Sonar results (1 true, 1 false piece of info about row/col/sector)
      - Drone results (enemy in sector or not)

    Only uses information received through TeamComms — never accesses
    the game state directly.
    """

    def __init__(self, team: str):
        self.team = team
        self.enemy_team = "red" if team == "blue" else "blue"
        self.initialized = False

        # Map info (set during initialize)
        self.rows = 0
        self.cols = 0
        self.sector_size = 8
        self.island_set: set = set()

        # Position tracking
        self.possible_positions: set = set()
        self.move_count = 0
        self.move_log: list[str] = []

    def initialize(self, map_def: dict):
        """Called when the game starts and map is known.
        The map is "on the RO's board" — they have it in front of them."""
        self.rows = map_def["rows"]
        self.cols = map_def["cols"]
        self.sector_size = map_def["sector_size"]
        self.island_set = set(tuple(p) for p in map_def["islands"])

        # All non-island cells are initially possible
        self.possible_positions = set()
        for r in range(self.rows):
            for c in range(self.cols):
                if (r, c) not in self.island_set:
                    self.possible_positions.add((r, c))
        self.initialized = True

    # ── Inbox processing ─────────────────────────────────────────────────────

    def process_inbox(self, messages: list):
        """Process messages from TeamComms inbox."""
        if not self.initialized:
            return
        for msg in messages:
            t = msg.get("type")
            if t == "enemy_move":
                self._process_enemy_move(msg["direction"])
            elif t == "enemy_surface":
                self._process_enemy_surface(msg["sector"])
            elif t == "enemy_torpedo":
                self._process_enemy_torpedo(msg["row"], msg["col"])
            elif t == "enemy_mine_placed":
                pass  # Location unknown — can't narrow positions
            elif t == "sonar_result":
                self._process_sonar_result(
                    msg["type1"], msg["val1"], msg["type2"], msg["val2"])
            elif t == "drone_result":
                self._process_drone_result(msg["sector"], msg["in_sector"])
            elif t == "friendly_torpedo":
                pass  # Noted (doesn't help track enemy)
            elif t == "friendly_mine_detonated":
                pass  # Noted
            elif t == "captain_position_request":
                pass  # Will send report after processing all messages

            if msg.get("human") and msg["type"] == "request_position_report":
                self._pending_report = True

    # ── Report generation ────────────────────────────────────────────────────

    def generate_report(self, comms) -> str:
        """Generate and send position report to captain via TeamComms.
        Returns the summary string for logging/chat."""
        if not self.initialized:
            return "Radio operator not yet initialized"

        report = self._compute_position_report()
        comms.ro_report_enemy_position(
            possible_positions=report["positions_sample"],
            certainty=report["certainty"],
            summary=report["summary"],
            best_guess=report["best_guess"],
        )
        return report["summary"]

    # ── Position tracking internals ──────────────────────────────────────────

    def _process_enemy_move(self, direction: str):
        """Enemy moved in given direction. Shift all possible positions."""
        dr, dc = direction_delta(direction)
        new_possible = set()
        for r, c in self.possible_positions:
            nr, nc = r + dr, c + dc
            if 0 <= nr < self.rows and 0 <= nc < self.cols:
                if (nr, nc) not in self.island_set:
                    new_possible.add((nr, nc))
        if new_possible:
            self.possible_positions = new_possible
        self.move_count += 1
        self.move_log.append(direction)

    def _process_enemy_surface(self, sector: int):
        """Enemy surfaced in sector. Reset to all positions in that sector."""
        self.possible_positions = set()
        for r in range(self.rows):
            for c in range(self.cols):
                if (r, c) not in self.island_set:
                    if get_sector(r, c, self.sector_size, self.cols) == sector:
                        self.possible_positions.add((r, c))
        self.move_count = 0
        self.move_log.clear()

    def _process_enemy_torpedo(self, row: int, col: int):
        """Enemy fired torpedo at (row, col). Enemy must be within range 4."""
        narrowed = {
            (r, c) for r, c in self.possible_positions
            if abs(r - row) + abs(c - col) <= 4
        }
        if narrowed:
            self.possible_positions = narrowed

    def _process_sonar_result(self, type1, val1, type2, val2):
        """Process sonar result: one is true, one is false (unknown which).
        Conservative approach: union of both hypotheses."""
        # Hypothesis A: type1 is true, type2 is false
        set_a = {
            (r, c) for r, c in self.possible_positions
            if self._matches(r, c, type1, val1) and not self._matches(r, c, type2, val2)
        }
        # Hypothesis B: type1 is false, type2 is true
        set_b = {
            (r, c) for r, c in self.possible_positions
            if not self._matches(r, c, type1, val1) and self._matches(r, c, type2, val2)
        }
        result = set_a | set_b
        if result:
            self.possible_positions = result

    def _process_drone_result(self, sector: int, in_sector: bool):
        """Process drone result. In sector: intersect. Not: exclude."""
        if in_sector:
            narrowed = {
                (r, c) for r, c in self.possible_positions
                if get_sector(r, c, self.sector_size, self.cols) == sector
            }
        else:
            narrowed = {
                (r, c) for r, c in self.possible_positions
                if get_sector(r, c, self.sector_size, self.cols) != sector
            }
        if narrowed:
            self.possible_positions = narrowed

    def _matches(self, row: int, col: int, info_type: str, value) -> bool:
        """Check if a position matches a sonar info piece."""
        if info_type == "row":
            return row == value
        elif info_type == "col":
            return col == value
        elif info_type == "sector":
            return get_sector(row, col, self.sector_size, self.cols) == value
        return False

    def _estimate_sectors(self) -> set:
        """Return the set of sectors that contain possible enemy positions."""
        sectors = set()
        for r, c in self.possible_positions:
            sectors.add(get_sector(r, c, self.sector_size, self.cols))
        return sectors

    def _compute_position_report(self) -> dict:
        """Compute a structured position report."""
        count = len(self.possible_positions)
        total = self.rows * self.cols - len(self.island_set)

        if count == 0:
            self._reset_positions()
            count = len(self.possible_positions)

        ratio = count / max(total, 1)
        if count == 1:
            certainty = "exact"
        elif count <= 5:
            certainty = "high"
        elif ratio <= 0.1:
            certainty = "medium"
        elif ratio <= 0.3:
            certainty = "low"
        else:
            certainty = "none"

        # Best guess: centroid of possible positions
        best = None
        if self.possible_positions:
            avg_r = sum(r for r, c in self.possible_positions) / count
            avg_c = sum(c for r, c in self.possible_positions) / count
            best = min(self.possible_positions,
                       key=lambda p: (p[0] - avg_r)**2 + (p[1] - avg_c)**2)

        # Summary
        sectors = self._estimate_sectors()
        sector_count = len(sectors)
        if count <= 5:
            summary = f"Enemy pinpointed to ~{count} cells in sector(s) {','.join(str(s) for s in sorted(sectors))}"
        elif count <= 30:
            summary = f"~{count} positions, likely sector(s) {','.join(str(s) for s in sorted(sectors))}"
        else:
            summary = f"Tracking {count} positions across {sector_count} sector(s)"

        # Sample positions for captain (limit message size)
        sample = list(self.possible_positions)
        if len(sample) > 50:
            sample = random.sample(sample, 50)

        return {
            "positions_sample": sample,
            "certainty": certainty,
            "summary": summary,
            "best_guess": best,
            "count": count,
        }

    def _reset_positions(self):
        """Reset to all valid positions (fallback when tracking breaks)."""
        self.possible_positions = set()
        for r in range(self.rows):
            for c in range(self.cols):
                if (r, c) not in self.island_set:
                    self.possible_positions.add((r, c))


# ── Captain Bot ────────────────────────────────────────────────────────────────

class CaptainBot:
    """
    Communication-aware captain.

    Knows (from own board):
      - Own position, trail, mines, systems, health
    Receives (from TeamComms):
      - RO: enemy position estimates
      - FM: system charge status
      - Engineer: direction recommendations
      - Event relay: enemy moves, torpedoes, surfaces, sonar/drone results
    Sends (via TeamComms):
      - To RO: position requests
      - To FM: charge priority
      - To Engineer: system protection priority
    """

    def __init__(self, team: str):
        self.team = team
        self.enemy_team = "red" if team == "blue" else "blue"

        # Knowledge from RO communications
        self.enemy_possible_positions: list = []
        self.enemy_best_guess: Optional[tuple] = None
        self.enemy_certainty: str = "none"
        self.known_enemy_sector: Optional[int] = None

        # Sonar/drone history (from comms relay)
        self.sonar_history: list = []
        self.drone_history: list = []

        # Engineer recommendations (from comms)
        self.recommended_directions: list = []

        # FM system report (from comms)
        self.last_systems_report: Optional[dict] = None

    # ── Inbox processing ─────────────────────────────────────────────────────

    def process_inbox(self, messages: list):
        """Process incoming TeamComms messages."""
        for msg in messages:
            t = msg.get("type")
            if t == "ro_position_report":
                self.enemy_possible_positions = msg.get("possible_positions", [])
                self.enemy_best_guess = msg.get("best_guess")
                self.enemy_certainty = msg.get("certainty", "none")
            elif t == "fm_systems_report":
                self.last_systems_report = msg.get("systems")
            elif t == "engineer_direction_rec":
                self.recommended_directions = msg.get("recommendations", [])
            elif t == "enemy_surface":
                self.known_enemy_sector = msg.get("sector")
            elif t == "sonar_result":
                self.sonar_history.append(msg)
            elif t == "drone_result":
                sector = msg.get("sector")
                in_sector = msg.get("in_sector")
                self.drone_history.append((sector, in_sector))
                if in_sector:
                    self.known_enemy_sector = sector
            # enemy_move, enemy_torpedo, enemy_mine_placed:
            # Captain hears these publicly; relies on RO for position tracking

    # ── Outgoing communications ──────────────────────────────────────────────

    def send_communications(self, comms, sub: dict, map_def: dict):
        """Send priority communications to FM and Engineer."""
        systems = sub.get("systems", {})

        # Tell FM what to prioritize charging
        priority = self._determine_charge_priority(systems)
        comms.captain_set_charge_priority(priority)

        # Tell Engineer which systems to protect
        protect = self._determine_system_protect(systems)
        comms.captain_set_system_protect(protect)

        # Request position update from RO
        comms.captain_request_position()

    def _determine_charge_priority(self, systems: dict) -> list:
        """Choose charge priority based on intel level."""
        if self.enemy_certainty in ("exact", "high"):
            # We know where the enemy is — prioritize weapons
            return ["torpedo", "mine", "drone", "sonar", "stealth"]
        elif self.enemy_certainty == "medium":
            # Decent idea of location — balance weapons and intel
            return ["torpedo", "drone", "sonar", "mine", "stealth"]
        else:
            # Low intel — prioritize detection systems
            return ["drone", "sonar", "torpedo", "mine", "stealth"]

    def _determine_system_protect(self, systems: dict) -> list:
        """Choose which systems to protect (tell engineer)."""
        protect = []

        def charge(s):
            v = systems.get(s, 0)
            return v.get("charge", 0) if isinstance(v, dict) else v

        # Protect systems that are close to being ready
        for sys_name in ["torpedo", "drone", "sonar", "mine"]:
            c = charge(sys_name)
            m = SYSTEM_MAX_CHARGE.get(sys_name, 6)
            if c >= m - 2:  # Close to ready
                protect.append(sys_name)

        if not protect:
            # Default: protect torpedo and whichever intel system is closer
            protect = ["torpedo"]
            if charge("drone") >= charge("sonar"):
                protect.append("drone")
            else:
                protect.append("sonar")

        return protect

    # ── Sonar response (when enemy uses sonar on us) ─────────────────────────

    def respond_sonar(self, own_sub: dict, map_def: dict) -> tuple:
        """
        Generate a sonar response (1 true, 1 false, different types).
        Returns (type1, val1, type2, val2).
        """
        er, ec = own_sub["position"]
        actual_sector = get_sector(er, ec, map_def["sector_size"], map_def["cols"])

        type_options = ["row", "col", "sector"]
        random.shuffle(type_options)
        type1, type2 = type_options[0], type_options[1]

        rows = map_def["rows"]
        cols = map_def["cols"]
        total_sectors = (math.ceil(rows / map_def["sector_size"])
                         * math.ceil(cols / map_def["sector_size"]))

        def true_val(t):
            if t == "row":    return er
            if t == "col":    return ec
            if t == "sector": return actual_sector
            return 0

        def false_val(t):
            if t == "row":
                options = [r for r in range(rows) if r != er]
                return random.choice(options) if options else (er + 1) % rows
            if t == "col":
                options = [c for c in range(cols) if c != ec]
                return random.choice(options) if options else (ec + 1) % cols
            if t == "sector":
                options = [s for s in range(1, total_sectors + 1) if s != actual_sector]
                return random.choice(options) if options else 1
            return 0

        # 50/50: type1 is true, type2 is false OR vice versa
        if random.random() < 0.5:
            val1 = true_val(type1)
            val2 = false_val(type2)
        else:
            val1 = false_val(type1)
            val2 = true_val(type2)

        return (type1, val1, type2, val2)

    # ── Placement ────────────────────────────────────────────────────────────

    def decide_placement(self, map_def: dict) -> tuple:
        """Choose a starting position — safe corner for our team."""
        rows = map_def["rows"]
        cols = map_def["cols"]
        island_set = set(tuple(p) for p in map_def["islands"])

        # Blue → top-left sector, Red → bottom-right sector
        if self.team == "blue":
            row_range = range(0, rows // 2)
            col_range = range(0, cols // 2)
        else:
            row_range = range(rows // 2, rows)
            col_range = range(cols // 2, cols)

        candidates = [
            (r, c) for r in row_range for c in col_range
            if (r, c) not in island_set
        ]
        if not candidates:
            candidates = [
                (r, c) for r in range(rows) for c in range(cols)
                if (r, c) not in island_set
            ]
        return random.choice(candidates)

    # ── Action decision ──────────────────────────────────────────────────────

    def decide_action(self, sub: dict, enemy_health: int,
                      map_def: dict, turn_state: dict) -> Optional[tuple]:
        """
        Decide the captain's next action.

        Returns a tuple:
          ("move",    direction)
          ("surface",)
          ("torpedo", row, col)
          ("drone",   sector)
          ("sonar",   ask_row, ask_col, ask_sector)
          ("stealth", direction, steps)
          ("end_turn",)
          None  → nothing to do (should not happen in normal flow)
        """
        pos = sub.get("position")
        if pos is None:
            return None

        r, c = pos
        trail_set = set(tuple(p) for p in sub.get("trail", []))
        mine_set  = set(tuple(m) for m in sub.get("mines", []))
        systems = sub.get("systems", {})
        rows = map_def["rows"]
        cols = map_def["cols"]
        island_set = set(tuple(p) for p in map_def["islands"])

        # Already surfaced — shouldn't happen (server handles dive), but guard
        if sub.get("surfaced"):
            return None

        # ── Movement ─────────────────────────────────────────────────────────
        valid = _get_valid_moves(r, c, trail_set, island_set, rows, cols, mine_set)

        if not valid:
            # Trapped — try stealth first, then surface
            stealth_charge = systems.get("stealth", 0)
            if isinstance(stealth_charge, dict):
                stealth_charge = stealth_charge.get("charge", 0)
            if stealth_charge >= SYSTEM_MAX_CHARGE["stealth"]:
                result = self._plan_stealth(r, c, trail_set, island_set, rows, cols, 4, mine_set)
                if result:
                    direction, steps = result
                    return ("stealth", direction, steps)
            return ("surface",)

        # Use stealth when only 1-2 valid moves remain and stealth is ready
        stealth_charge = systems.get("stealth", 0)
        if isinstance(stealth_charge, dict):
            stealth_charge = stealth_charge.get("charge", 0)
        if stealth_charge >= SYSTEM_MAX_CHARGE["stealth"] and len(valid) <= 2:
            result = self._plan_stealth(r, c, trail_set, island_set, rows, cols, 4, mine_set)
            if result and result[1] >= 2:
                direction, steps = result
                return ("stealth", direction, steps)

        # Build preferred directions from engineer recommendations
        preferred_dirs = set()
        for rec in self.recommended_directions:
            d = rec.get("direction")
            if d:
                preferred_dirs.add(d)

        # Greedy 1-step lookahead with engineer preference tie-breaking
        def move_score(m):
            future = _count_future_moves(
                m[1], m[2],
                trail_set | {(r, c)},
                island_set, rows, cols,
            )
            bonus = 2 if m[0] in preferred_dirs else 0
            return future * 10 + bonus

        best = max(valid, key=move_score)
        return ("move", best[0])

    # ── Post-move weapon decision ────────────────────────────────────────────

    def decide_weapon_action(self, sub: dict, map_def: dict) -> Optional[tuple]:
        """
        Decide whether to use a weapon system AFTER the captain has moved
        and engineer+FM have acted.
        Uses RO position reports from comms to choose targets.
        """
        pos = sub.get("position")
        if pos is None:
            return None
        r, c = pos
        systems = sub.get("systems", {})
        island_set = set(tuple(p) for p in map_def.get("islands", []))

        def charge(s):
            v = systems.get(s, 0)
            return v.get("charge", 0) if isinstance(v, dict) else v

        # Fire torpedo if charged and we have a good target from RO
        if charge("torpedo") >= SYSTEM_MAX_CHARGE["torpedo"]:
            target = self._best_torpedo_target(r, c, map_def, island_set)
            if target:
                return ("torpedo", target[0], target[1])

        # Use drone if charged and need more intel
        if (charge("drone") >= SYSTEM_MAX_CHARGE["drone"]
                and self.enemy_certainty in ("none", "low")):
            sector = self._best_drone_sector()
            return ("drone", sector)

        # Use sonar if charged and we don't have exact location
        if (charge("sonar") >= SYSTEM_MAX_CHARGE["sonar"]
                and self.enemy_certainty != "exact"):
            return ("sonar",)

        return None

    # ── Weapon helpers ───────────────────────────────────────────────────────

    def _best_torpedo_target(self, r, c, map_def, island_set):
        """Pick best torpedo target based on RO's position estimates."""
        # If we have a best guess from RO, try to target near it
        if self.enemy_best_guess:
            tr, tc = self.enemy_best_guess
            dist = abs(tr - r) + abs(tc - c)
            if 1 <= dist <= 4 and (tr, tc) not in island_set:
                return (tr, tc)

            # Best guess out of range — check all possible positions
            candidates = []
            for pos in self.enemy_possible_positions:
                pr, pc = pos if isinstance(pos, (list, tuple)) else (pos[0], pos[1])
                d = abs(pr - r) + abs(pc - c)
                if 1 <= d <= 4 and (pr, pc) not in island_set:
                    candidates.append((d, pr, pc))
            if candidates:
                candidates.sort()
                return (candidates[0][1], candidates[0][2])

        # Fall back to sector-based targeting
        if self.known_enemy_sector:
            return self._sector_torpedo_target(r, c, self.known_enemy_sector, map_def, island_set)

        return None

    def _sector_torpedo_target(self, r, c, sector, map_def, island_set):
        """Pick closest in-range cell inside the target sector."""
        sector_size = map_def["sector_size"]
        spr = math.ceil(map_def["cols"] / sector_size)
        sec_idx = sector - 1
        sr = sec_idx // spr
        sc = sec_idx % spr
        min_r, min_c = sr * sector_size, sc * sector_size

        candidates = []
        for dr in range(sector_size):
            for dc in range(sector_size):
                tr, tc = min_r + dr, min_c + dc
                dist = abs(tr - r) + abs(tc - c)
                if 0 < dist <= 4 and (tr, tc) not in island_set:
                    candidates.append((dist, tr, tc))
        if not candidates:
            return None
        candidates.sort()
        return (candidates[0][1], candidates[0][2])

    def _best_drone_sector(self):
        """Choose the most useful sector to scan with drone."""
        # If we have possible positions, find sector with most candidates
        if self.enemy_possible_positions:
            sector_counts: dict[int, int] = {}
            for pos in self.enemy_possible_positions:
                r, c = pos if isinstance(pos, (list, tuple)) else (pos[0], pos[1])
                # We need sector info — approximate with basic calc
                # (RO has the map, captain doesn't know sector_size directly,
                #  but for simplicity we use 8 which is the standard)
                s = get_sector(r, c, 8, 15)
                sector_counts[s] = sector_counts.get(s, 0) + 1
            if sector_counts:
                # Scan the sector with most possible positions
                return max(sector_counts, key=sector_counts.get)

        # Exclude sectors already confirmed empty by drone
        confirmed_not = set()
        for sector, in_sector in self.drone_history:
            if not in_sector:
                confirmed_not.add(sector)

        options = [s for s in range(1, 5) if s not in confirmed_not]
        if not options:
            options = list(range(1, 5))
        return random.choice(options)

    def _plan_stealth(self, r, c, trail_set, island_set, rows, cols, max_steps=4, mine_set=None):
        """
        Plan a straight-line stealth move (Silence rule: ONE direction only).
        Returns (direction, steps) tuple, or None if no valid move exists.
        """
        best_direction = None
        best_steps     = 0
        best_score     = -1

        for d in DIRECTIONS:
            dr, dc = direction_delta(d)
            cur_r, cur_c = r, c
            visited = set(trail_set)
            steps = 0

            for _ in range(max_steps):
                nr, nc = cur_r + dr, cur_c + dc
                if nr < 0 or nr >= rows or nc < 0 or nc >= cols:
                    break
                if (nr, nc) in island_set:
                    break
                if (nr, nc) in visited:
                    break
                if mine_set and (nr, nc) in mine_set:
                    break
                visited.add((nr, nc))
                cur_r, cur_c = nr, nc
                steps += 1

            if steps > 0:
                future = _count_future_moves(cur_r, cur_c, visited, island_set, rows, cols)
                score  = future * 10 + steps
                if score > best_score:
                    best_score     = score
                    best_direction = d
                    best_steps     = steps

        if best_direction is None:
            return None
        return (best_direction, best_steps)


# ── First Mate Bot ─────────────────────────────────────────────────────────────

class FirstMateBot:
    """
    Communication-aware first mate.

    Knows (from own board): own systems charge levels.
    Receives from captain: which systems to prioritize charging.
    Sends to captain: current system charge status.
    """

    DEFAULT_PRIORITY = ["torpedo", "mine", "sonar", "drone", "stealth"]

    def __init__(self, team: str):
        self.team = team
        self.charge_priority = list(self.DEFAULT_PRIORITY)

    # ── Inbox processing ─────────────────────────────────────────────────────

    def process_inbox(self, messages: list):
        """Process TeamComms messages from captain."""
        for msg in messages:
            if msg.get("type") == "charge_priority":
                new_priority = msg.get("priority")
                if new_priority and isinstance(new_priority, list):
                    self.charge_priority = new_priority

            if msg.get("human") and msg["type"] == "set_charge_priority":
                self.charge_priority = msg.get("system")

    # ── Outgoing communications ──────────────────────────────────────────────

    def send_communications(self, comms, systems: dict):
        """Report current system status to captain via TeamComms."""
        # Build a clean status dict
        status = {}
        for sys_name, max_c in SYSTEM_MAX_CHARGE.items():
            v = systems.get(sys_name, 0)
            if isinstance(v, dict):
                cur = v.get("charge", 0)
            else:
                cur = v
            status[sys_name] = {
                "charge": cur,
                "max": max_c,
                "ready": cur >= max_c,
            }
        comms.fm_report_systems(status)

    # ── Charge decision ──────────────────────────────────────────────────────

    def decide_charge(self, systems: dict) -> Optional[str]:
        """Return the name of the system to charge, following captain's priority."""
        for sys_name in self.charge_priority:
            info = systems.get(sys_name, 0)
            if isinstance(info, dict):
                cur = info.get("charge", 0)
                max_c = info.get("max", SYSTEM_MAX_CHARGE.get(sys_name, 99))
            else:
                cur = info
                max_c = SYSTEM_MAX_CHARGE.get(sys_name, 99)
            if cur < max_c:
                return sys_name
        return None

    @staticmethod
    def describe_charge(system: str) -> str:
        return f"Charged {system}"


# ── Engineer Bot ───────────────────────────────────────────────────────────────

class EngineerBot:
    """
    Communication-aware engineer.

    Knows (from own board): engineering board state + direction to mark.
    Receives from captain: which systems to prioritize keeping available.
    Sends to captain: direction recommendations to clear protected systems.

    Strategy with communications:
      1. Complete a circuit if possible (clears nodes, no damage).
      2. Avoid marking nodes whose color matches a protected system.
      3. Avoid radiation nodes.
      4. Among remaining, prefer partial-circuit nodes.
    """

    def __init__(self, team: str):
        self.team = team
        self.protect_systems: list = []   # system names captain wants protected

    # ── Inbox processing ─────────────────────────────────────────────────────

    def process_inbox(self, messages: list):
        """Process TeamComms messages from captain."""
        for msg in messages:
            if msg.get("type") == "system_protect":
                new_protect = msg.get("systems")
                if new_protect and isinstance(new_protect, list):
                    self.protect_systems = new_protect

            if msg.get("human") and msg["type"] == "set_system_protect":
                system = msg.get("system")
                if system:
                    self.protect_systems = [system]

    # ── Outgoing communications ──────────────────────────────────────────────

    def send_communications(self, comms, board: dict):
        """Send direction recommendations to captain via TeamComms."""
        recommendations = self._analyze_directions(board)
        if recommendations:
            comms.engineer_recommend_directions(recommendations)

    def _analyze_directions(self, board: dict) -> list:
        """Analyze which directions would be safest for the captain to move.
        Considers which nodes are available and whether they affect protected systems."""
        # Determine which colors to protect
        protect_colors = set()
        for sys_name in self.protect_systems:
            color = SYSTEM_COLORS.get(sys_name)
            if color:
                protect_colors.add(color)

        recommendations = []

        for direction in DIRECTIONS:
            available = get_available_nodes(board, direction)
            if not available:
                continue

            score = 0
            reason_parts = []

            # Bonus: can complete a circuit in this direction
            for idx in available:
                node = ENGINEERING_LAYOUT[direction][idx]
                cid = node.get("circuit")
                if cid is not None:
                    circuit_nodes = CIRCUITS[cid]
                    others_done = all(
                        board[d][i]["marked"]
                        for d, i in circuit_nodes
                        if not (d == direction and i == idx)
                    )
                    if others_done:
                        score += 10
                        reason_parts.append(f"can complete C{cid}")

            # Count safe nodes (not protected color, not radiation)
            safe_nodes = [
                i for i in available
                if ENGINEERING_LAYOUT[direction][i]["color"] not in protect_colors
                and ENGINEERING_LAYOUT[direction][i]["color"] != "radiation"
            ]
            score += len(safe_nodes) * 2

            if not safe_nodes:
                score -= 5
                reason_parts.append("risky — only protected/radiation nodes")
            else:
                reason_parts.append(f"{len(safe_nodes)} safe nodes")

            if score > 0:
                recommendations.append({
                    "direction": direction,
                    "reason": "; ".join(reason_parts),
                    "score": score,
                })

        recommendations.sort(key=lambda r: r["score"], reverse=True)
        return recommendations[:2]   # Top 2

    # ── Mark decision ────────────────────────────────────────────────────────

    def decide_mark(self, board: dict, direction: str) -> Optional[int]:
        """Return the node index to mark, respecting captain's system priorities."""
        available = get_available_nodes(board, direction)
        if not available:
            return None

        # Determine protected colors from captain's priority
        protect_colors = set()
        for sys_name in self.protect_systems:
            color = SYSTEM_COLORS.get(sys_name)
            if color:
                protect_colors.add(color)

        # Strategy 1: complete a circuit (safe — just clears nodes)
        for idx in available:
            node = ENGINEERING_LAYOUT[direction][idx]
            cid = node.get("circuit")
            if cid is not None:
                circuit_nodes = CIRCUITS[cid]
                others_done = all(
                    board[d][i]["marked"]
                    for d, i in circuit_nodes
                    if not (d == direction and i == idx)
                )
                if others_done:
                    return idx  # completing this circuit!

        # Strategy 2: avoid protected colors AND radiation
        safe = [
            i for i in available
            if ENGINEERING_LAYOUT[direction][i]["color"] not in protect_colors
            and ENGINEERING_LAYOUT[direction][i]["color"] != "radiation"
        ]
        if safe:
            # Among safe, prefer nodes that are part of a circuit
            circ = [i for i in safe if ENGINEERING_LAYOUT[direction][i].get("circuit")]
            return circ[0] if circ else safe[0]

        # Strategy 3: avoid radiation at least
        non_rad = [
            i for i in available
            if ENGINEERING_LAYOUT[direction][i]["color"] != "radiation"
        ]
        if non_rad:
            circ = [i for i in non_rad if ENGINEERING_LAYOUT[direction][i].get("circuit")]
            return circ[0] if circ else non_rad[0]

        # Strategy 4: only radiation nodes left — pick first
        return available[0]

    @staticmethod
    def describe_mark(direction: str, index: int) -> str:
        return f"Marked {direction} node {index}"
