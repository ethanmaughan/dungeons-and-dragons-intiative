"""Adaptive Environment Tool — narrative environment reactions + turn-based pressure.

Pure Python, zero AI calls. Tracks timers and spell effects to generate
narrative descriptions that the DM AI incorporates into combat.
"""

from __future__ import annotations

from server.ai.tools import EnvironmentState, EnvironmentTimer


# ---- Spell → environment effect mapping ----

SPELL_EFFECTS: dict[str, str] = {
    # Fire
    "fire bolt": "fire",
    "burning hands": "fire",
    "fireball": "fire",
    "scorching ray": "fire",
    "flame blade": "fire",
    "produce flame": "fire",
    "heat metal": "fire",
    # Thunder / Force
    "thunderwave": "thunder",
    "shatter": "thunder",
    "thunderclap": "thunder",
    "eldritch blast": "thunder",
    # Ice / Cold
    "ray of frost": "ice",
    "ice knife": "ice",
    "sleet storm": "ice",
    "cone of cold": "ice",
    "frostbite": "ice",
    # Lightning
    "lightning bolt": "lightning",
    "witch bolt": "lightning",
    "call lightning": "lightning",
    "shocking grasp": "lightning",
}


def detect_spell_effects(
    dice_rolls: list[dict],
    environment_state: EnvironmentState,
) -> list[str]:
    """Scan dice_rolls for spell casts and register environmental effects.

    Returns list of new narrative strings for this round.
    """
    narratives = []
    seen_effects = set()

    for roll_data in dice_rolls:
        spell_name = roll_data.get("spell", "").lower().strip()
        if not spell_name:
            continue

        effect_type = SPELL_EFFECTS.get(spell_name)
        if effect_type and effect_type not in seen_effects:
            seen_effects.add(effect_type)
            narr = environment_state.register_spell_effect(spell_name, effect_type)
            if narr:
                narratives.append(narr)

    return narratives


# ---- Timer registration at combat start ----


def register_combat_timers(environment_state: EnvironmentState, game_state) -> None:
    """Register default timers based on the environment description.

    Called once at combat start. Deterministic templates, no AI calls.
    """
    desc = (getattr(game_state, "environment_description", "") or "").lower()

    if "torch" in desc or "lantern" in desc or "candlelight" in desc:
        environment_state.register_timer(EnvironmentTimer(
            event_key="light_fading",
            description="Light source growing dim",
            trigger_on_round=5,
            narrative_template="The torchlight flickers dangerously, casting wild shadows across the walls. Visibility is worsening.",
            mechanical_note="Dim light — ranged attacks beyond 30 feet have disadvantage.",
        ))

    if "cave" in desc or "dungeon" in desc or "underground" in desc:
        environment_state.register_timer(EnvironmentTimer(
            event_key="distant_sounds",
            description="Sounds from deeper in the dungeon",
            trigger_on_round=6,
            narrative_template="Distant echoes reverberate through the tunnels — footsteps, or something heavier. Reinforcements may be approaching.",
        ))
        environment_state.register_timer(EnvironmentTimer(
            event_key="reinforcement_warning",
            description="Reinforcements closing in",
            trigger_on_round=10,
            narrative_template="The sound of approaching creatures grows louder. Whatever lurks deeper in these tunnels has taken notice of the battle.",
            mechanical_note="Potential reinforcements — DM may introduce additional enemies.",
        ))

    if "water" in desc or "flood" in desc or "sewer" in desc:
        environment_state.register_timer(EnvironmentTimer(
            event_key="rising_water",
            description="Water level rising",
            trigger_on_round=4,
            recurring=True,
            interval=3,
            narrative_template="The water level rises another inch, sloshing around boots and making footing treacherous.",
            mechanical_note="Difficult terrain — movement costs double in flooded areas.",
        ))

    if "crumbling" in desc or "unstable" in desc or "ruins" in desc:
        environment_state.register_timer(EnvironmentTimer(
            event_key="structural_collapse",
            description="Structure becoming unstable",
            trigger_on_round=7,
            narrative_template="A section of ceiling crumbles, sending dust and debris raining down. The structure groans ominously.",
            mechanical_note="DC 12 DEX save or take 1d6 bludgeoning damage from falling debris.",
        ))

    if "fire" in desc or "burning" in desc or "inferno" in desc:
        environment_state.register_timer(EnvironmentTimer(
            event_key="spreading_flames",
            description="Fire spreading through the area",
            trigger_on_round=3,
            recurring=True,
            interval=2,
            narrative_template="The flames spread further, licking hungrily at new fuel. The heat intensifies.",
            mechanical_note="Fire hazard — creatures ending turn adjacent take 1d4 fire damage.",
        ))


# ---- Round-end processing ----


def process_round_end(
    environment_state: EnvironmentState,
    current_round: int,
    game_state,
) -> dict:
    """Process round-end environmental effects.

    Called by combat_orchestrator after all turns in a round resolve.
    Pure algorithmic — no AI calls.

    Returns:
        {
            "narrative_additions": list[str],
            "mechanical_notes": list[str],
            "timers_triggered": list[str],
        }
    """
    # Tick timers
    timer_narratives = environment_state.tick(current_round)

    # Collect mechanical notes from newly triggered timers
    mechanical_notes = []
    for timer in environment_state.timers:
        if timer.event_key in environment_state.triggered_events and timer.mechanical_note:
            if timer.mechanical_note not in mechanical_notes:
                mechanical_notes.append(timer.mechanical_note)

    return {
        "narrative_additions": timer_narratives,
        "mechanical_notes": mechanical_notes,
        "timers_triggered": list(environment_state.triggered_events),
    }
