"""Character creation helpers: stat calculation, starting data, and finalization."""

import json
from pathlib import Path

# Standard array for ability scores
STANDARD_ARRAY = [15, 14, 13, 12, 10, 8]

# Starting HP by class (level 1: hit die max + CON modifier)
CLASS_HIT_DIE = {
    "barbarian": 12, "fighter": 10, "paladin": 10, "ranger": 10,
    "bard": 8, "cleric": 8, "druid": 8, "monk": 8, "rogue": 8, "warlock": 8,
    "sorcerer": 6, "wizard": 6,
}

# Starting AC assumptions by class
CLASS_BASE_AC = {
    "barbarian": 13, "fighter": 16, "paladin": 18, "ranger": 15,
    "bard": 12, "cleric": 16, "druid": 12, "monk": 12, "rogue": 13, "warlock": 12,
    "sorcerer": 10, "wizard": 10,
}

# Primary ability by class (for standard array assignment)
CLASS_PRIMARY_ABILITIES = {
    "barbarian": ["str", "con", "dex", "wis", "cha", "int"],
    "fighter": ["str", "con", "dex", "wis", "cha", "int"],
    "paladin": ["str", "cha", "con", "wis", "dex", "int"],
    "ranger": ["dex", "wis", "con", "str", "int", "cha"],
    "bard": ["cha", "dex", "con", "wis", "int", "str"],
    "cleric": ["wis", "con", "str", "cha", "dex", "int"],
    "druid": ["wis", "con", "dex", "int", "cha", "str"],
    "monk": ["dex", "wis", "con", "str", "cha", "int"],
    "rogue": ["dex", "int", "con", "cha", "wis", "str"],
    "warlock": ["cha", "con", "dex", "wis", "int", "str"],
    "sorcerer": ["cha", "con", "dex", "wis", "int", "str"],
    "wizard": ["int", "dex", "con", "wis", "cha", "str"],
}

# Racial ability bonuses
RACIAL_BONUSES = {
    "human": {"str": 1, "dex": 1, "con": 1, "int": 1, "wis": 1, "cha": 1},
    "elf": {"dex": 2},
    "high elf": {"dex": 2, "int": 1},
    "wood elf": {"dex": 2, "wis": 1},
    "dark elf": {"dex": 2, "cha": 1},
    "dwarf": {"con": 2},
    "hill dwarf": {"con": 2, "wis": 1},
    "mountain dwarf": {"con": 2, "str": 2},
    "halfling": {"dex": 2},
    "lightfoot halfling": {"dex": 2, "cha": 1},
    "stout halfling": {"dex": 2, "con": 1},
    "dragonborn": {"str": 2, "cha": 1},
    "tiefling": {"cha": 2, "int": 1},
    "half-orc": {"str": 2, "con": 1},
    "gnome": {"int": 2},
    "half-elf": {"cha": 2},
}

# Class starting data cache
CLASS_DATA_FILE = Path(__file__).parent.parent.parent / "data" / "srd" / "class_starting_data.json"
_class_data_cache = None


def get_class_starting_data(char_class: str) -> dict:
    """Load starting equipment, spells, and features for a class."""
    global _class_data_cache
    if _class_data_cache is None:
        _class_data_cache = json.loads(CLASS_DATA_FILE.read_text())
    return _class_data_cache.get(char_class.lower(), {})


def assign_ability_scores(char_class: str) -> dict:
    """Assign standard array scores based on class priorities."""
    class_key = char_class.lower()
    priority = CLASS_PRIMARY_ABILITIES.get(class_key, ["str", "dex", "con", "int", "wis", "cha"])
    scores = {}
    for i, ability in enumerate(priority):
        scores[ability] = STANDARD_ARRAY[i]
    return scores


def apply_racial_bonuses(scores: dict, race: str) -> dict:
    """Apply racial ability score bonuses."""
    race_key = race.lower()
    bonuses = RACIAL_BONUSES.get(race_key, {})
    for ability, bonus in bonuses.items():
        if ability in scores:
            scores[ability] += bonus
    return scores


def calculate_starting_hp(char_class: str, con_score: int) -> int:
    """Calculate level 1 HP: max hit die + CON modifier."""
    hit_die = CLASS_HIT_DIE.get(char_class.lower(), 8)
    con_mod = (con_score - 10) // 2
    return hit_die + con_mod


def get_starting_ac(char_class: str) -> int:
    """Get starting AC for a class (assumes starting equipment)."""
    return CLASS_BASE_AC.get(char_class.lower(), 10)


def finalize_character(character, choices: dict = None, game_state=None) -> None:
    """Apply stats, equipment, spells, and features to a character. Mark creation complete."""
    if choices:
        if choices.get("race"):
            character.race = choices["race"]
        if choices.get("class"):
            character.char_class = choices["class"]
        if choices.get("name"):
            character.character_name = choices["name"]

    # Assign ability scores
    scores = assign_ability_scores(character.char_class)
    scores = apply_racial_bonuses(scores, character.race)
    character.str_score = scores.get("str", 10)
    character.dex_score = scores.get("dex", 10)
    character.con_score = scores.get("con", 10)
    character.int_score = scores.get("int", 10)
    character.wis_score = scores.get("wis", 10)
    character.cha_score = scores.get("cha", 10)

    # Calculate derived stats
    character.hp_max = calculate_starting_hp(character.char_class, character.con_score)
    character.hp_current = character.hp_max
    character.ac = get_starting_ac(character.char_class)
    character.level = 1
    character.proficiency_bonus = 2
    character.speed = 30

    # Populate starting equipment, spells, and features from class data
    class_data = get_class_starting_data(character.char_class)
    character.inventory = class_data.get("equipment", [])
    character.features = class_data.get("features", [])

    cantrips = class_data.get("cantrips", [])
    spells = class_data.get("spells", [])
    character.spells = cantrips + spells

    slots = class_data.get("spell_slots", {})
    character.spell_slots = slots
    character.spell_slots_current = dict(slots)

    # Mark complete
    character.creation_complete = True

    if game_state:
        game_state.game_mode = "exploration"
        game_state.creation_step = None
        game_state.environment_description = "Your adventure begins..."
