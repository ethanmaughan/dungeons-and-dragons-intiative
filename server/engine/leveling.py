"""XP thresholds, level-up logic, and stat progression."""

from server.engine.character import CLASS_HIT_DIE, get_class_starting_data

# XP required to reach each level (cumulative)
XP_THRESHOLDS = {
    1: 0,
    2: 300,
    3: 900,
    4: 2700,
    5: 6500,
    6: 14000,
    7: 23000,
    8: 34000,
    9: 48000,
    10: 64000,
    11: 85000,
    12: 100000,
    13: 120000,
    14: 140000,
    15: 165000,
    16: 195000,
    17: 225000,
    18: 265000,
    19: 305000,
    20: 355000,
}

# Proficiency bonus by level
PROFICIENCY_BY_LEVEL = {
    1: 2, 2: 2, 3: 2, 4: 2,
    5: 3, 6: 3, 7: 3, 8: 3,
    9: 4, 10: 4, 11: 4, 12: 4,
    13: 5, 14: 5, 15: 5, 16: 5,
    17: 6, 18: 6, 19: 6, 20: 6,
}

# Spell slots by class level (for full casters)
FULL_CASTER_SLOTS = {
    1: {"1": 2},
    2: {"1": 3},
    3: {"1": 4, "2": 2},
    4: {"1": 4, "2": 3},
    5: {"1": 4, "2": 3, "3": 2},
    6: {"1": 4, "2": 3, "3": 3},
    7: {"1": 4, "2": 3, "3": 3, "4": 1},
    8: {"1": 4, "2": 3, "3": 3, "4": 2},
    9: {"1": 4, "2": 3, "3": 3, "4": 3, "5": 1},
    10: {"1": 4, "2": 3, "3": 3, "4": 3, "5": 2},
}

# Half casters get slots at half the rate
HALF_CASTER_SLOTS = {
    1: {},
    2: {"1": 2},
    3: {"1": 3},
    4: {"1": 3},
    5: {"1": 4, "2": 2},
    6: {"1": 4, "2": 2},
    7: {"1": 4, "2": 3},
    8: {"1": 4, "2": 3},
    9: {"1": 4, "2": 3, "3": 2},
    10: {"1": 4, "2": 3, "3": 3},
}

# Warlock pact magic (few slots but higher level)
WARLOCK_SLOTS = {
    1: {"1": 1},
    2: {"1": 2},
    3: {"2": 2},
    4: {"2": 2},
    5: {"3": 2},
    6: {"3": 2},
    7: {"4": 2},
    8: {"4": 2},
    9: {"5": 2},
    10: {"5": 2},
}

FULL_CASTERS = {"wizard", "sorcerer", "bard", "cleric", "druid"}
HALF_CASTERS = {"paladin", "ranger"}

# CR to XP reward
CR_XP_REWARDS = {
    0: 10, 0.125: 25, 0.25: 50, 0.5: 100,
    1: 200, 2: 450, 3: 700, 4: 1100, 5: 1800,
    6: 2300, 7: 2900, 8: 3900, 9: 5000, 10: 5900,
}


def xp_for_cr(cr: float) -> int:
    """Get XP reward for a given challenge rating."""
    return CR_XP_REWARDS.get(cr, int(cr * 200))


def level_for_xp(xp: int) -> int:
    """Determine what level a character should be based on their XP."""
    level = 1
    for lvl, threshold in sorted(XP_THRESHOLDS.items()):
        if xp >= threshold:
            level = lvl
    return min(level, 20)


def check_level_up(character) -> dict | None:
    """Check if a character should level up. Returns level-up info or None."""
    current_level = character.level
    new_level = level_for_xp(character.xp)

    if new_level <= current_level:
        return None

    # Level up!
    old_level = current_level
    character.level = new_level

    # Update proficiency bonus
    character.proficiency_bonus = PROFICIENCY_BY_LEVEL.get(new_level, 2)

    # Increase HP (hit die average + CON modifier per level gained)
    hit_die = CLASS_HIT_DIE.get(character.char_class.lower(), 8)
    con_mod = (character.con_score - 10) // 2
    hp_per_level = (hit_die // 2) + 1 + con_mod  # Average roll + 1 + CON mod
    levels_gained = new_level - old_level
    hp_increase = hp_per_level * levels_gained
    character.hp_max += hp_increase
    character.hp_current += hp_increase  # Heal the increase

    # Update spell slots
    char_class = character.char_class.lower()
    new_slots = {}
    if char_class in FULL_CASTERS:
        new_slots = FULL_CASTER_SLOTS.get(new_level, character.spell_slots or {})
    elif char_class in HALF_CASTERS:
        new_slots = HALF_CASTER_SLOTS.get(new_level, character.spell_slots or {})
    elif char_class == "warlock":
        new_slots = WARLOCK_SLOTS.get(new_level, character.spell_slots or {})

    if new_slots:
        character.spell_slots = new_slots
        character.spell_slots_current = dict(new_slots)

    return {
        "old_level": old_level,
        "new_level": new_level,
        "hp_increase": hp_increase,
        "new_proficiency": character.proficiency_bonus,
        "new_spell_slots": new_slots if new_slots else None,
    }
