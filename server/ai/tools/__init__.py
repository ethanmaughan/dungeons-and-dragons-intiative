"""AI tool modules for Foray's DM orchestrator.

Contains transient in-memory state objects (EncounterState, EnvironmentState)
and tool modules for battlefield analysis, enemy personality, learning,
NPC guidance, and environment adaptation.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class EncounterState:
    """Ephemeral combat-scoped state for within-encounter learning.

    Created at combat start, serialized to game_state.active_effects between
    player turns, discarded when end_combat() is called. Never persisted to DB.
    """

    def __init__(self, campaign_id: int, enemy_types: list[str]):
        self.campaign_id = campaign_id
        self.enemy_types = enemy_types
        self.round_number: int = 1
        self.observed_spells: list[str] = []
        self.observed_actions: list[str] = []
        self.total_damage_to_enemies: int = 0
        self.total_damage_to_party: int = 0

    def record_player_action(self, action_tag: str, spell_name: str | None = None):
        self.observed_actions.append(action_tag)
        if spell_name:
            self.observed_spells.append(spell_name)

    def record_damage(self, target_is_enemy: bool, amount: int):
        if target_is_enemy:
            self.total_damage_to_enemies += amount
        else:
            self.total_damage_to_party += amount

    def get_current_fight_summary(self) -> dict:
        """Snapshot for feeding into enemy_personality tool."""
        return {
            "round": self.round_number,
            "observed_spells": self.observed_spells[-5:],
            "observed_actions": self.observed_actions[-5:],
            "damage_ratio": (
                self.total_damage_to_enemies / max(self.total_damage_to_party, 1)
            ),
        }

    def to_snapshot(self) -> dict:
        """Serialize to a dict for JSON storage in active_effects."""
        return {
            "campaign_id": self.campaign_id,
            "enemy_types": self.enemy_types,
            "round_number": self.round_number,
            "observed_spells": self.observed_spells,
            "observed_actions": self.observed_actions,
            "total_damage_to_enemies": self.total_damage_to_enemies,
            "total_damage_to_party": self.total_damage_to_party,
        }

    @classmethod
    def from_snapshot(cls, data: dict) -> EncounterState:
        """Restore from a serialized snapshot."""
        state = cls(data["campaign_id"], data["enemy_types"])
        state.round_number = data.get("round_number", 1)
        state.observed_spells = data.get("observed_spells", [])
        state.observed_actions = data.get("observed_actions", [])
        state.total_damage_to_enemies = data.get("total_damage_to_enemies", 0)
        state.total_damage_to_party = data.get("total_damage_to_party", 0)
        return state


@dataclass
class EnvironmentTimer:
    """A timed environmental event that fires on a specific round."""
    event_key: str
    description: str
    trigger_on_round: int
    recurring: bool = False
    interval: int = 0
    triggered: bool = False
    narrative_template: str = ""
    mechanical_note: str = ""


class EnvironmentState:
    """Ephemeral combat-scoped environment tracking.

    Created at combat start alongside EncounterState.
    Discarded when end_combat() is called.
    """

    def __init__(self):
        self.timers: list[EnvironmentTimer] = []
        self.active_hazards: list[str] = []
        self.triggered_events: list[str] = []
        self.spell_effects: list[dict] = []

    def register_timer(self, timer: EnvironmentTimer) -> None:
        self.timers.append(timer)

    def tick(self, current_round: int) -> list[str]:
        """Check timers and return narrative strings for events that trigger this round."""
        narratives = []
        for timer in self.timers:
            if timer.triggered and not timer.recurring:
                continue
            if current_round >= timer.trigger_on_round:
                if timer.recurring:
                    rounds_since = current_round - timer.trigger_on_round
                    if rounds_since % max(timer.interval, 1) != 0:
                        continue
                timer.triggered = True
                self.triggered_events.append(timer.event_key)
                if timer.narrative_template:
                    narratives.append(timer.narrative_template)
                if timer.mechanical_note:
                    self.active_hazards.append(timer.mechanical_note)
        return narratives

    def register_spell_effect(self, spell_name: str, effect_type: str) -> str | None:
        """React to a spell and return a narrative description if applicable."""
        self.spell_effects.append({"spell": spell_name, "type": effect_type})

        EFFECT_NARRATIVES = {
            "fire": f"The flames from {spell_name} scorch the ground, leaving blackened stone and smoldering embers.",
            "thunder": f"The shockwave from {spell_name} sends debris flying, cracking the floor with spiderweb fractures.",
            "ice": f"A sheet of frost spreads where {spell_name} struck, coating the ground in a treacherous glaze of ice.",
            "lightning": f"Lightning from {spell_name} arcs across the chamber, leaving the air thick with ozone.",
        }
        return EFFECT_NARRATIVES.get(effect_type)

    def to_snapshot(self) -> dict:
        """Serialize for JSON storage in active_effects."""
        return {
            "timers": [
                {
                    "event_key": t.event_key,
                    "description": t.description,
                    "trigger_on_round": t.trigger_on_round,
                    "recurring": t.recurring,
                    "interval": t.interval,
                    "triggered": t.triggered,
                    "narrative_template": t.narrative_template,
                    "mechanical_note": t.mechanical_note,
                }
                for t in self.timers
            ],
            "active_hazards": self.active_hazards,
            "triggered_events": self.triggered_events,
            "spell_effects": self.spell_effects,
        }

    @classmethod
    def from_snapshot(cls, data: dict) -> EnvironmentState:
        """Restore from a serialized snapshot."""
        state = cls()
        for t in data.get("timers", []):
            timer = EnvironmentTimer(
                event_key=t["event_key"],
                description=t["description"],
                trigger_on_round=t["trigger_on_round"],
                recurring=t.get("recurring", False),
                interval=t.get("interval", 0),
                triggered=t.get("triggered", False),
                narrative_template=t.get("narrative_template", ""),
                mechanical_note=t.get("mechanical_note", ""),
            )
            state.timers.append(timer)
        state.active_hazards = data.get("active_hazards", [])
        state.triggered_events = data.get("triggered_events", [])
        state.spell_effects = data.get("spell_effects", [])
        return state
