"""Combat state machine: initiative, turns, enemy creation, and auto-resolution."""

import json
from pathlib import Path

from server.engine.dice import initiative_roll

MONSTERS_FILE = Path(__file__).parent.parent.parent / "data" / "srd" / "monsters_basic.json"
_monsters_cache = None


def get_monster_stats(name: str) -> dict | None:
    """Look up a monster by name from the SRD data."""
    global _monsters_cache
    if _monsters_cache is None:
        _monsters_cache = json.loads(MONSTERS_FILE.read_text())

    # Try exact key match, then fuzzy match
    key = name.lower().replace(" ", "_")
    if key in _monsters_cache:
        return _monsters_cache[key]

    # Fuzzy: find any key containing the search term
    for k, v in _monsters_cache.items():
        if name.lower() in k or name.lower() in v["name"].lower():
            return v

    return None


def create_enemy_characters(enemy_names: list, campaign_id: int, db) -> list:
    """Create temporary Character rows for enemies in combat.
    Stores full monster data in npc_personality for the enemy agent to read."""
    from server.db.models import Character

    enemies = []
    name_counts = {}

    for name in enemy_names:
        # Handle duplicate names (goblin, goblin → Goblin 1, Goblin 2)
        name_counts[name] = name_counts.get(name, 0) + 1
        count = name_counts[name]
        display_name = f"{name.title()} {count}" if enemy_names.count(name) > 1 else name.title()

        stats = get_monster_stats(name)
        if stats:
            # Store full monster data for enemy agent access
            monster_data = {
                "attack_bonus": stats.get("attack_bonus", 3),
                "damage": stats.get("damage", "1d6+1"),
                "cr": stats.get("cr", 0.25),
                "actions": stats.get("actions", []),
                "traits": stats.get("traits", []),
                "tactics": stats.get("tactics", "Attacks the closest enemy."),
            }

            enemy = Character(
                campaign_id=campaign_id,
                character_name=display_name,
                race="Monster",
                char_class=name.title(),
                level=1,
                hp_current=stats["hp"],
                hp_max=stats["hp"],
                ac=stats["ac"],
                speed=stats.get("speed", 30),
                str_score=stats.get("str", 10),
                dex_score=stats.get("dex", 10),
                con_score=stats.get("con", 10),
                int_score=stats.get("int", 10),
                wis_score=stats.get("wis", 10),
                cha_score=stats.get("cha", 10),
                is_npc=False,
                is_enemy=True,
                npc_personality=monster_data,
            )
        else:
            # Unknown monster — use generic stats
            enemy = Character(
                campaign_id=campaign_id,
                character_name=display_name,
                race="Monster",
                char_class=name.title(),
                level=1,
                hp_current=15,
                hp_max=15,
                ac=13,
                str_score=12,
                dex_score=12,
                con_score=12,
                is_npc=False,
                is_enemy=True,
                npc_personality={
                    "attack_bonus": 3,
                    "damage": "1d6+1",
                    "cr": 0.25,
                    "actions": [{"name": "Attack", "type": "melee", "attack_bonus": 3, "damage": "1d6+1", "reach": 5}],
                    "traits": [],
                    "tactics": "Attacks the closest enemy.",
                },
            )

        db.add(enemy)
        enemies.append(enemy)

    db.flush()
    return enemies


def roll_all_initiative(characters: list) -> list[dict]:
    """Roll initiative for all combatants and return sorted order."""
    initiative_order = []

    for c in characters:
        if c.hp_current <= 0:
            continue
        result = initiative_roll(c.dex_score)
        initiative_order.append({
            "character_id": c.id,
            "character_name": c.character_name,
            "initiative": result["total"],
            "is_enemy": c.is_enemy,
        })

    # Sort by initiative (highest first), break ties by DEX
    initiative_order.sort(key=lambda x: x["initiative"], reverse=True)
    return initiative_order


def start_combat(enemy_names: list, characters: list, game_state, campaign_id: int, db) -> dict:
    """Start combat: create enemies, roll initiative, update game state."""
    # Guard: don't start combat if already in combat
    if game_state.game_mode == "combat":
        return {
            "enemies": [],
            "initiative_order": game_state.initiative_order or [],
            "initiative_summary": "(Combat already in progress)",
        }

    # Create enemy characters
    enemies = create_enemy_characters(enemy_names, campaign_id, db)

    # Combine all combatants (PCs + enemies)
    all_combatants = [c for c in characters if not c.is_enemy and c.hp_current > 0] + enemies

    # Roll initiative ONCE
    initiative_order = roll_all_initiative(all_combatants)

    # Update game state
    game_state.game_mode = "combat"
    game_state.initiative_order = initiative_order
    game_state.round_number = 1
    game_state.current_turn_character_id = initiative_order[0]["character_id"] if initiative_order else None

    # Build initiative summary for narration
    init_lines = []
    for entry in initiative_order:
        init_lines.append(f"{entry['character_name']}: {entry['initiative']}")

    return {
        "enemies": enemies,
        "initiative_order": initiative_order,
        "initiative_summary": "\n".join(init_lines),
    }


def end_combat(game_state, characters, db):
    """End combat: clean up state, remove dead enemies, reset death saves."""
    game_state.game_mode = "exploration"
    game_state.initiative_order = []
    game_state.round_number = 0
    game_state.current_turn_character_id = None

    for c in characters:
        if c.is_enemy:
            # Remove enemy characters from the database
            db.delete(c)
        elif not c.is_npc:
            # Reset death saves for PCs
            c.death_saves = {"successes": 0, "failures": 0}
            conditions = list(c.conditions or [])
            for cond in ("dying", "stable"):
                if cond in conditions:
                    conditions.remove(cond)
            c.conditions = conditions


def advance_turn(game_state) -> dict | None:
    """Advance to the next turn in initiative order. Returns the next combatant info."""
    order = game_state.initiative_order
    if not order:
        return None

    # Find current position
    current_id = game_state.current_turn_character_id
    current_idx = 0
    for i, entry in enumerate(order):
        if entry["character_id"] == current_id:
            current_idx = i
            break

    # Move to next
    next_idx = (current_idx + 1) % len(order)
    if next_idx == 0:
        game_state.round_number += 1

    next_entry = order[next_idx]
    game_state.current_turn_character_id = next_entry["character_id"]

    return next_entry


def is_enemy_turn(game_state) -> bool:
    """Check if it's currently an enemy's turn."""
    if not game_state.initiative_order:
        return False

    current_id = game_state.current_turn_character_id
    for entry in game_state.initiative_order:
        if entry["character_id"] == current_id:
            return entry.get("is_enemy", False)
    return False


def all_enemies_dead(characters: list) -> bool:
    """Check if all enemies in combat are dead."""
    enemies = [c for c in characters if c.is_enemy]
    if not enemies:
        return True
    return all(c.hp_current <= 0 for c in enemies)


def all_pcs_down(characters: list) -> bool:
    """Check if all player characters are at 0 HP (unconscious/dying/dead)."""
    pcs = [c for c in characters if not c.is_npc and not c.is_enemy]
    if not pcs:
        return True
    return all(c.hp_current <= 0 for c in pcs)


def get_enemy_monster_data(enemy) -> dict:
    """Extract monster combat data from an enemy Character's npc_personality field."""
    data = enemy.npc_personality or {}
    return {
        "attack_bonus": data.get("attack_bonus", 3),
        "damage": data.get("damage", "1d6+1"),
        "cr": data.get("cr", 0.25),
        "actions": data.get("actions", []),
        "traits": data.get("traits", []),
        "tactics": data.get("tactics", "Attacks the closest enemy."),
    }
