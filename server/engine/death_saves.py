"""D&D 5e death saving throw mechanics.

When a PC drops to 0 HP they become unconscious and start making death saves
at the start of each of their turns. 3 successes = stabilize, 3 failures = dead.
"""

from server.engine.dice import roll


def is_dying(character) -> bool:
    """True if at 0 HP and actively dying (not stable, not dead)."""
    if character.hp_current > 0:
        return False
    conditions = character.conditions or []
    return "dead" not in conditions and "stable" not in conditions


def is_dead(character) -> bool:
    """True if the character has died (3 death save failures)."""
    return "dead" in (character.conditions or [])


def is_stable(character) -> bool:
    """True if unconscious but stabilized (3 successes or Spare the Dying)."""
    return "stable" in (character.conditions or [])


def reset_death_saves(character):
    """Zero out death save counters and remove dying/stable conditions."""
    character.death_saves = {"successes": 0, "failures": 0}
    conditions = list(character.conditions or [])
    for cond in ("dying", "stable"):
        if cond in conditions:
            conditions.remove(cond)
    character.conditions = conditions


def _set_dying(character):
    """Mark a character as unconscious and dying. Resets death saves."""
    character.death_saves = {"successes": 0, "failures": 0}
    conditions = list(character.conditions or [])
    for cond in ("unconscious", "dying"):
        if cond not in conditions:
            conditions.append(cond)
    # Remove stable if present (e.g. taking damage while stable)
    if "stable" in conditions:
        conditions.remove("stable")
    character.conditions = conditions


def roll_death_save(character) -> dict:
    """Roll a death saving throw for a dying character.

    Returns dict with: outcome, roll, successes, failures, narration.
    """
    result = roll("1d20")
    d20 = result["rolls"][0]

    saves = dict(character.death_saves or {"successes": 0, "failures": 0})
    name = character.character_name

    # Natural 20: regain 1 HP, wake up
    if d20 == 20:
        character.hp_current = 1
        saves = {"successes": 0, "failures": 0}
        character.death_saves = saves
        conditions = list(character.conditions or [])
        for cond in ("unconscious", "dying", "stable"):
            if cond in conditions:
                conditions.remove(cond)
        character.conditions = conditions
        return {
            "outcome": "nat20",
            "roll": d20,
            "successes": 0,
            "failures": saves["failures"],
            "narration": (
                f"\n{name} makes a death saving throw... **Natural 20!** "
                f"{name}'s eyes snap open — they regain 1 HP and are back in the fight!"
            ),
        }

    # Natural 1: two failures
    if d20 == 1:
        saves["failures"] = min(saves["failures"] + 2, 3)
        character.death_saves = saves

        if saves["failures"] >= 3:
            conditions = list(character.conditions or [])
            if "dying" in conditions:
                conditions.remove("dying")
            if "dead" not in conditions:
                conditions.append("dead")
            character.conditions = conditions
            return {
                "outcome": "dead",
                "roll": d20,
                "successes": saves["successes"],
                "failures": saves["failures"],
                "narration": (
                    f"\n{name} makes a death saving throw... **Natural 1!** Two failures. "
                    f"{name} has died."
                ),
            }

        return {
            "outcome": "nat1",
            "roll": d20,
            "successes": saves["successes"],
            "failures": saves["failures"],
            "narration": (
                f"\n{name} makes a death saving throw... **Natural 1!** Two failures. "
                f"(Successes: {saves['successes']}/3, Failures: {saves['failures']}/3)"
            ),
        }

    # 10+ = success
    if d20 >= 10:
        saves["successes"] = saves["successes"] + 1
        character.death_saves = saves

        if saves["successes"] >= 3:
            conditions = list(character.conditions or [])
            if "dying" in conditions:
                conditions.remove("dying")
            if "stable" not in conditions:
                conditions.append("stable")
            character.conditions = conditions
            return {
                "outcome": "stabilized",
                "roll": d20,
                "successes": saves["successes"],
                "failures": saves["failures"],
                "narration": (
                    f"\n{name} makes a death saving throw... rolled {d20} — Success! "
                    f"{name} has stabilized. They are unconscious but no longer dying."
                ),
            }

        return {
            "outcome": "success",
            "roll": d20,
            "successes": saves["successes"],
            "failures": saves["failures"],
            "narration": (
                f"\n{name} makes a death saving throw... rolled {d20} — Success. "
                f"(Successes: {saves['successes']}/3, Failures: {saves['failures']}/3)"
            ),
        }

    # Below 10 = failure
    saves["failures"] = saves["failures"] + 1
    character.death_saves = saves

    if saves["failures"] >= 3:
        conditions = list(character.conditions or [])
        if "dying" in conditions:
            conditions.remove("dying")
        if "dead" not in conditions:
            conditions.append("dead")
        character.conditions = conditions
        return {
            "outcome": "dead",
            "roll": d20,
            "successes": saves["successes"],
            "failures": saves["failures"],
            "narration": (
                f"\n{name} makes a death saving throw... rolled {d20} — Failure. "
                f"{name} has died."
            ),
        }

    return {
        "outcome": "failure",
        "roll": d20,
        "successes": saves["successes"],
        "failures": saves["failures"],
        "narration": (
            f"\n{name} makes a death saving throw... rolled {d20} — Failure. "
            f"(Successes: {saves['successes']}/3, Failures: {saves['failures']}/3)"
        ),
    }


def apply_damage_at_zero_hp(character, is_critical: bool = False) -> dict:
    """Apply automatic death save failure(s) when a dying character takes damage.

    Melee hits on unconscious targets within 5 feet are auto-crits in 5e (2 failures).
    """
    saves = dict(character.death_saves or {"successes": 0, "failures": 0})
    name = character.character_name
    failures_added = 2 if is_critical else 1

    saves["failures"] = min(saves["failures"] + failures_added, 3)
    character.death_saves = saves

    if saves["failures"] >= 3:
        conditions = list(character.conditions or [])
        if "dying" in conditions:
            conditions.remove("dying")
        if "dead" not in conditions:
            conditions.append("dead")
        character.conditions = conditions
        return {
            "failures_added": failures_added,
            "is_dead": True,
            "narration": (
                f"\n({name} takes damage while dying — "
                f"{'2 automatic failures (critical hit)' if is_critical else '1 automatic failure'}. "
                f"{name} has died.)"
            ),
        }

    return {
        "failures_added": failures_added,
        "is_dead": False,
        "narration": (
            f"\n({name} takes damage while dying — "
            f"{'2 automatic failures (critical hit)' if is_critical else '1 automatic failure'}. "
            f"Failures: {saves['failures']}/3)"
        ),
    }


def apply_healing_at_zero_hp(character, amount: int) -> dict:
    """Heal a dying/stable character: wake them up, clear death saves."""
    name = character.character_name
    character.hp_current = min(amount, character.hp_max)
    character.death_saves = {"successes": 0, "failures": 0}

    conditions = list(character.conditions or [])
    for cond in ("unconscious", "dying", "stable"):
        if cond in conditions:
            conditions.remove(cond)
    character.conditions = conditions

    return {
        "new_hp": character.hp_current,
        "narration": (
            f"({name} is healed for {amount} HP while unconscious — "
            f"they regain consciousness! HP: 0 → {character.hp_current})"
        ),
    }
