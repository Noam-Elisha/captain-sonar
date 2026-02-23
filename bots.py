"""
Captain Sonar — Rule-based AI bots for all 4 roles.

Each bot only has access to information appropriate to its role:
  CaptainBot      : own position/trail/systems/health + enemy health/last-surfaced-sector
  FirstMateBot    : own systems + own health
  EngineerBot     : own engineering board + direction to mark (from server)
  RadioOperatorBot: publicly-announced directions, surface announcements, weapon events
"""

from __future__ import annotations   # enables PEP 604 | syntax on Python 3.8+

import random
from typing import Optional, List, Tuple
from maps import get_sector
from game_state import (
    ENGINEERING_LAYOUT, CIRCUITS, RADIATION_NODES, SYSTEM_MAX_CHARGE,
    direction_delta, get_available_nodes,
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


# ── Captain Bot ────────────────────────────────────────────────────────────────

class CaptainBot:
    """
    Rule-based captain.
    Knows: own sub state (position, trail, systems, health, mines).
    Knows: enemy health + sector only if enemy surfaced.
    Does NOT know: enemy exact position or trail.
    """

    CHARGE_PRIORITY = ["torpedo", "mine", "sonar", "drone", "stealth"]

    def __init__(self, team: str):
        self.team = team
        self.enemy_team = "red" if team == "blue" else "blue"
        self.known_enemy_sector = None   # type: Optional[int]  last confirmed sector
        self.sonar_history: list = []                # (row_match, col_match, sector_match)
        self.drone_history: list = []                # (sector, in_sector)

    # ── Knowledge updates (called by server after events) ──────────────────────

    def update_sonar_result(self, type1: str, val1, type2: str, val2):
        """Store a sonar result (new format: 2 pieces of info from enemy captain, 1 true 1 false)."""
        self.sonar_history.append((type1, val1, type2, val2))

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
        import math
        # RULEBOOK: TBT mode has 4 sectors (2×2). Use ceiling division to match get_sector().
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

    def update_drone_result(self, sector: int, in_sector: bool):
        self.drone_history.append((sector, in_sector))
        if in_sector:
            self.known_enemy_sector = sector

    def update_enemy_surfaced(self, sector: int):
        self.known_enemy_sector = sector

    # ── Placement ──────────────────────────────────────────────────────────────

    def decide_placement(self, map_def: dict) -> tuple:
        """Choose a starting position — safe corner for our team."""
        rows = map_def["rows"]
        cols = map_def["cols"]
        island_set = set(tuple(p) for p in map_def["islands"])

        # Blue → top-left quadrant, Red → bottom-right quadrant
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

    # ── Action decision ────────────────────────────────────────────────────────

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

        # ── Movement ───────────────────────────────────────────────────────────
        # NOTE: Weapon systems (torpedo/drone/sonar) are decided AFTER moving;
        # call decide_weapon_action() post-move (after eng+FM have acted).

        valid = _get_valid_moves(r, c, trail_set, island_set, rows, cols, mine_set)

        if not valid:
            # Completely trapped (blackout — must surface per rulebook)
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

        # Greedy 1-step lookahead: maximise future valid moves
        best = max(
            valid,
            key=lambda m: _count_future_moves(
                m[1], m[2],
                trail_set | {(r, c)},
                island_set, rows, cols,
            ),
        )
        return ("move", best[0])

    # ── Post-move weapon decision ──────────────────────────────────────────────

    def decide_weapon_action(self, sub: dict, map_def: dict) -> Optional[tuple]:
        """
        Decide whether to use a weapon system AFTER the captain has moved
        and engineer+FM have acted.

        RULEBOOK TBT: systems activate after each course announcement.
        Returns ("torpedo", row, col), ("drone", sector), ("sonar",), or None.
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

        # Fire torpedo if charged and we have a sector target
        if charge("torpedo") >= SYSTEM_MAX_CHARGE["torpedo"] and self.known_enemy_sector:
            target = self._best_torpedo_target(r, c, self.known_enemy_sector, map_def, island_set)
            if target:
                return ("torpedo", target[0], target[1])

        # Use drone if charged and sector unknown
        # RULEBOOK: TBT mode has 4 sectors (1-4), not 9
        if charge("drone") >= SYSTEM_MAX_CHARGE["drone"] and self.known_enemy_sector is None:
            sector = random.randint(1, 4)
            return ("drone", sector)

        # Use sonar if charged (interactive flow: enemy captain responds)
        if charge("sonar") >= SYSTEM_MAX_CHARGE["sonar"]:
            return ("sonar",)

        return None

    # ── Weapon helpers ─────────────────────────────────────────────────────────

    def _best_torpedo_target(self, r, c, sector, map_def, island_set):
        """Pick the closest in-range cell inside the target sector."""
        sector_size = map_def["sector_size"]
        spr = map_def["cols"] // sector_size
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

    def _plan_stealth(self, r, c, trail_set, island_set, rows, cols, max_steps=4, mine_set=None):
        """
        Plan a straight-line stealth move (Silence rule: ONE direction only).
        Returns (direction, steps) tuple, or None if no valid move exists.
        Picks the direction that maximises open space at the destination.
        """
        best_direction = None
        best_steps     = 0
        best_score     = -1

        for d in DIRECTIONS:
            dr, dc = direction_delta(d)
            cur_r, cur_c = r, c
            visited = set(trail_set)   # trail already contains current position
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
                    break   # cannot move into own mine
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
    Rule-based first mate.
    Knows: own systems + own health only.
    Charges in a fixed priority order.
    """

    PRIORITY = ["torpedo", "mine", "sonar", "drone", "stealth"]

    def __init__(self, team: str):
        self.team = team

    def decide_charge(self, systems: dict) -> Optional[str]:
        """Return the name of the system to charge, or None if all full."""
        for sys_name in self.PRIORITY:
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
        return {
            "torpedo": "charging torpedoes 🚀",
            "mine":    "charging mine launcher 💣",
            "sonar":   "charging sonar 📡",
            "drone":   "charging drone 🛸",
            "stealth": "charging stealth drive 👻",
        }.get(system, f"charging {system}")


# ── Engineer Bot ───────────────────────────────────────────────────────────────

class EngineerBot:
    """
    Rule-based engineer.
    Knows: own engineering board + direction to mark.
    Strategy:
      1. Complete a circuit (clears nodes, no damage) if possible.
      2. Avoid radiation nodes when non-radiation nodes are available.
      3. Prefer partial-circuit nodes over plain nodes.
    """

    def __init__(self, team: str):
        self.team = team

    def decide_mark(self, board: dict, direction: str) -> Optional[int]:
        """Return the node index to mark, or None if no valid node."""
        available = get_available_nodes(board, direction)
        if not available:
            return None

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

        # Strategy 2: avoid radiation
        non_rad = [
            i for i in available
            if ENGINEERING_LAYOUT[direction][i]["color"] != "radiation"
        ]
        if non_rad:
            # Among non-radiation, prefer nodes that are part of a circuit
            circ = [i for i in non_rad if ENGINEERING_LAYOUT[direction][i].get("circuit")]
            return circ[0] if circ else non_rad[0]

        # Strategy 3: only radiation nodes left — pick first
        return available[0]

    @staticmethod
    def describe_mark(direction: str, index: int) -> str:
        node = ENGINEERING_LAYOUT[direction][index]
        color = node["color"]
        cid   = node.get("circuit")
        tag   = f"/C{cid}" if cid else ""
        return f"marking {direction.upper()} node {index} [{color}{tag}]"


# ── Radio Operator Bot ─────────────────────────────────────────────────────────

class RadioOperatorBot:
    """
    Passive tracker: records publicly-announced enemy movements.
    Generates commentary for the event log.
    """

    def __init__(self, team: str):
        self.team = team
        self.enemy_team = "red" if team == "blue" else "blue"
        self.move_log: list[str] = []    # direction strings
        self.surface_sectors: list[int] = []
        self.torpedo_count = 0
        self.drone_sectors: list[int] = []

    def record_direction(self, direction: str):
        self.move_log.append(direction)

    def record_surface(self, sector: int):
        self.surface_sectors.append(sector)
        self.move_log.clear()   # trail reset — start tracking fresh

    def record_torpedo(self, row: int, col: int):
        self.torpedo_count += 1

    def record_drone(self, sector: int):
        self.drone_sectors.append(sector)

    def generate_commentary(self) -> str:
        """Produce a concise analysis message."""
        total = len(self.move_log)
        if not self.move_log and not self.surface_sectors:
            return "No enemy contact yet — watching all sectors 👁"

        parts = []
        if self.surface_sectors:
            parts.append(f"last surfaced sector {self.surface_sectors[-1]}")

        if total >= 1:
            # Direction histogram for recent moves
            recent = self.move_log[-6:]
            from collections import Counter
            cnt = Counter(recent)
            dominant, freq = cnt.most_common(1)[0]
            parts.append(f"moving mostly {dominant} ({freq}/{len(recent)} recent moves)")

        if self.torpedo_count:
            parts.append(f"fired {self.torpedo_count} torpedo(es)")

        if not parts:
            return "Tracking enemy — no clear pattern yet"

        return "Enemy: " + ", ".join(parts)
