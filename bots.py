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

def _get_valid_moves(row, col, trail_set, island_set, rows, cols):
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

    def update_sonar_result(self, row_match: bool, col_match: bool, sector_match: bool):
        self.sonar_history.append((row_match, col_match, sector_match))
        # If sector matched, update known sector
        if sector_match and self.sonar_history:
            pass  # sector info comes through drone; sonar needs more inference

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
          ("stealth", [moves])
          ("end_turn",)
          None  → nothing to do (should not happen in normal flow)
        """
        pos = sub.get("position")
        if pos is None:
            return None

        r, c = pos
        trail_set = set(tuple(p) for p in sub.get("trail", []))
        systems = sub.get("systems", {})
        rows = map_def["rows"]
        cols = map_def["cols"]
        island_set = set(tuple(p) for p in map_def["islands"])

        # Already surfaced — shouldn't happen (server handles dive), but guard
        if sub.get("surfaced"):
            return None

        # ── Pre-move weapon checks ─────────────────────────────────────────────

        # Fire torpedo if charged and we have a sector target
        torpedo_charge = systems.get("torpedo", 0)
        if isinstance(torpedo_charge, dict):
            torpedo_charge = torpedo_charge.get("charge", 0)
        if torpedo_charge >= SYSTEM_MAX_CHARGE["torpedo"] and self.known_enemy_sector:
            target = self._best_torpedo_target(r, c, self.known_enemy_sector, map_def, island_set)
            if target:
                return ("torpedo", target[0], target[1])

        # Use drone if charged and sector unknown
        drone_charge = systems.get("drone", 0)
        if isinstance(drone_charge, dict):
            drone_charge = drone_charge.get("charge", 0)
        if drone_charge >= SYSTEM_MAX_CHARGE["drone"] and self.known_enemy_sector is None:
            sector = random.randint(1, 9)
            return ("drone", sector)

        # Use sonar if charged
        sonar_charge = systems.get("sonar", 0)
        if isinstance(sonar_charge, dict):
            sonar_charge = sonar_charge.get("charge", 0)
        if sonar_charge >= SYSTEM_MAX_CHARGE["sonar"]:
            sector = self.known_enemy_sector if self.known_enemy_sector else random.randint(1, 9)
            return ("sonar", None, None, sector)

        # ── Movement ───────────────────────────────────────────────────────────

        valid = _get_valid_moves(r, c, trail_set, island_set, rows, cols)

        if not valid:
            # Completely trapped
            stealth_charge = systems.get("stealth", 0)
            if isinstance(stealth_charge, dict):
                stealth_charge = stealth_charge.get("charge", 0)
            if stealth_charge >= SYSTEM_MAX_CHARGE["stealth"]:
                stealth_moves = self._plan_stealth(r, c, trail_set, island_set, rows, cols, 4)
                if stealth_moves:
                    return ("stealth", stealth_moves)
            return ("surface",)

        # Use stealth when only 1-2 valid moves remain and stealth is ready
        stealth_charge = systems.get("stealth", 0)
        if isinstance(stealth_charge, dict):
            stealth_charge = stealth_charge.get("charge", 0)
        if stealth_charge >= SYSTEM_MAX_CHARGE["stealth"] and len(valid) <= 2:
            stealth_moves = self._plan_stealth(r, c, trail_set, island_set, rows, cols, 4)
            if len(stealth_moves) >= 2:
                return ("stealth", stealth_moves)

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

    def _plan_stealth(self, r, c, trail_set, island_set, rows, cols, max_steps=4):
        """Plan up to max_steps stealth moves toward open space."""
        moves = []
        cur_r, cur_c = r, c
        cur_trail = set(trail_set)

        for _ in range(max_steps):
            valid = _get_valid_moves(cur_r, cur_c, cur_trail, island_set, rows, cols)
            if not valid:
                break
            best = max(
                valid,
                key=lambda m: _count_future_moves(
                    m[1], m[2],
                    cur_trail | {(cur_r, cur_c)},
                    island_set, rows, cols,
                ),
            )
            moves.append(best[0])
            cur_trail.add((cur_r, cur_c))
            cur_r, cur_c = best[1], best[2]

        return moves


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
