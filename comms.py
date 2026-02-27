"""
Admiral Radar – Team Communication System.

Provides structured message-passing between roles on the same team.
Works identically for bot-to-bot, bot-to-human, and human-to-bot communication.

Each team has a TeamComms instance with:
  - Event relay: public game events routed to appropriate roles
  - Inter-role messaging: structured messages between teammates

Message flow (from COMMUNICATION_SYSTEM.md):
  Radio Operator receives:
    From enemy captain (via event relay): moves, mine placed, torpedo coords,
      sonar response, drone response
    From friendly captain (via event relay): mine activation, torpedo coords
  Radio Operator sends:
    To Captain: potential enemy location (position report)

  Captain receives:
    From enemy captain (via event relay): same as RO
    From friendly RO: possible enemy locations
    From friendly FM: which systems are charged/available
    From friendly Engineer: direction recommendations
  Captain sends:
    To RO: request for location/certainty
    To FM: which system to prioritize charging
    To Engineer: which systems to prioritize keeping available

  First Mate receives:
    From friendly Captain: which systems to prioritize charging
  First Mate sends:
    To Captain: which systems are charged/available

  Engineer receives:
    From friendly Captain: which systems to prioritize keeping open
  Engineer sends:
    To Captain: directions to clear prioritized system
"""

from __future__ import annotations
from typing import Optional


class TeamComms:
    """Communication hub for a single team's internal role-to-role messaging.

    Messages are posted to per-role inboxes. Bots read their inbox each tick;
    human players receive SocketIO events emitted by the server when messages
    are posted.
    """

    def __init__(self, team: str):
        self.team = team
        self.enemy_team = "red" if team == "blue" else "blue"

        # Per-role message inboxes (list of dicts)
        self._inboxes: dict[str, list] = {
            "captain": [],
            "first_mate": [],
            "engineer": [],
            "radio_operator": [],
        }

    # ── Core messaging primitives ────────────────────────────────────────────

    def post(self, to_role: str, msg: dict):
        """Post a message to a role's inbox."""
        if to_role in self._inboxes:
            self._inboxes[to_role].append(msg)

    def read_inbox(self, role: str) -> list:
        """Read and clear all messages for a role."""
        msgs = self._inboxes.get(role, [])[:]
        if role in self._inboxes:
            self._inboxes[role].clear()
        return msgs

    def peek_inbox(self, role: str) -> list:
        """Read messages without clearing."""
        return self._inboxes.get(role, [])[:]

    def peek_latest(self, role: str, msg_type: str) -> Optional[dict]:
        """Get the most recent message of a specific type for a role."""
        for msg in reversed(self._inboxes.get(role, [])):
            if msg.get("type") == msg_type:
                return msg
        return None

    # ── Human player messaging ────────────────────────────────────────────────

    def post_from_human(self, from_role: str, msg_type: str, data: dict = None):
        """Post a message from a human player into the comms system."""
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

    # ── Enemy event relay ────────────────────────────────────────────────────
    # Called by the server when public game events happen.
    # These go to BOTH radio_operator AND captain (per COMMUNICATION_SYSTEM.md).

    def relay_enemy_move(self, direction: str):
        """Enemy captain announced a move direction (not stealth)."""
        msg = {"type": "enemy_move", "direction": direction}
        self.post("radio_operator", msg)
        self.post("captain", msg)

    def relay_enemy_mine_placed(self):
        """Enemy placed a mine – location unknown."""
        msg = {"type": "enemy_mine_placed"}
        self.post("radio_operator", msg)
        self.post("captain", msg)

    def relay_enemy_torpedo(self, row: int, col: int):
        """Enemy fired torpedo at exact coordinates."""
        msg = {"type": "enemy_torpedo", "row": row, "col": col}
        self.post("radio_operator", msg)
        self.post("captain", msg)

    def relay_enemy_surface(self, sector: int):
        """Enemy surfaced in given sector."""
        msg = {"type": "enemy_surface", "sector": sector}
        self.post("radio_operator", msg)
        self.post("captain", msg)

    def relay_sonar_result(self, type1: str, val1, type2: str, val2):
        """Sonar result received (enemy captain's response)."""
        msg = {
            "type": "sonar_result",
            "type1": type1, "val1": val1,
            "type2": type2, "val2": val2,
        }
        self.post("radio_operator", msg)
        self.post("captain", msg)

    def relay_drone_result(self, sector: int, in_sector: bool):
        """Drone scan result received."""
        msg = {"type": "drone_result", "sector": sector, "in_sector": in_sector}
        self.post("radio_operator", msg)
        self.post("captain", msg)

    # ── Friendly event relay ─────────────────────────────────────────────────
    # Own team events that RO needs for context.

    def relay_friendly_torpedo(self, row: int, col: int):
        """Own captain fired torpedo at exact coordinates."""
        self.post("radio_operator", {
            "type": "friendly_torpedo", "row": row, "col": col,
        })

    def relay_friendly_mine_detonated(self, row: int, col: int):
        """Own captain detonated mine at exact coordinates."""
        self.post("radio_operator", {
            "type": "friendly_mine_detonated", "row": row, "col": col,
        })

    # ── Inter-role communication ─────────────────────────────────────────────

    # RO → Captain: potential enemy location
    def ro_report_enemy_position(self, possible_positions: list,
                                  certainty: str, summary: str,
                                  best_guess: tuple = None):
        """Radio operator reports estimated enemy position to captain."""
        self.post("captain", {
            "type": "ro_position_report",
            "possible_positions": possible_positions,
            "certainty": certainty,   # "exact" | "high" | "medium" | "low" | "none"
            "summary": summary,
            "best_guess": best_guess,  # (row, col) or None
        })

    # Captain → RO: request for location/certainty
    def captain_request_position(self):
        """Captain asks RO for a position update."""
        self.post("radio_operator", {"type": "captain_position_request"})

    # Captain → FM: which system to prioritize charging
    def captain_set_charge_priority(self, priority: list):
        """Captain tells FM which systems to prioritize.
        priority: ordered list of system names, e.g. ["torpedo", "drone", "sonar"]
        """
        self.post("first_mate", {
            "type": "charge_priority",
            "priority": priority,
        })

    # Captain → FM: tell FM when to use sonar/drone
    def captain_request_system_use(self, system: str, params: dict = None):
        """Captain tells FM to activate sonar or drone."""
        self.post("first_mate", {
            "type": "use_system",
            "system": system,
            "params": params or {},
        })

    # Captain → Engineer: which systems to prioritize keeping available
    def captain_set_system_protect(self, systems: list):
        """Captain tells engineer which systems to keep unblocked.
        systems: list of system names, e.g. ["torpedo", "sonar"]
        """
        self.post("engineer", {
            "type": "system_protect",
            "systems": systems,
        })

    # FM → Captain: which systems are charged and available
    def fm_report_systems(self, systems: dict):
        """FM reports current system charge status.
        systems: {name: {"charge": int, "max": int, "ready": bool}}
        """
        self.post("captain", {
            "type": "fm_systems_report",
            "systems": systems,
        })

    # Engineer → Captain: direction recommendations to clear systems
    def engineer_recommend_directions(self, recommendations: list):
        """Engineer recommends directions that help clear/protect systems.
        recommendations: [{"direction": str, "reason": str, "score": int}]
        """
        self.post("captain", {
            "type": "engineer_direction_rec",
            "recommendations": recommendations,
        })
