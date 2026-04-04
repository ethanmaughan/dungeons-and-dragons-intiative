"""Combat state machine: initiative, turns, enemy creation, and auto-resolution."""

import json
from pathlib import Path

from server.engine.dice import initiative_roll

MONSTERS_FILE = Path(__file__).parent.parent.parent / "data" / "srd" / "monsters_basic.json"

# Grid constants
CELL_SIZE = 32       # pixels per grid cell
FEET_PER_CELL = 5    # D&D: 1 cell = 5 feet
GRID_COLS = 25       # 25 columns
GRID_ROWS = 19       # 19 rows

DIRECTION_DELTAS = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}
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


def assign_combat_positions(all_combatants: list) -> dict:
    """Auto-place combatants on a grid: PCs on the left, enemies on the right."""
    pcs = [c for c in all_combatants if not c.is_enemy]
    enemies = [c for c in all_combatants if c.is_enemy]
    positions = {}

    pc_spacing = GRID_ROWS // (len(pcs) + 1) if pcs else GRID_ROWS // 2
    for i, c in enumerate(pcs):
        positions[str(c.id)] = {
            "col": 4,
            "row": pc_spacing * (i + 1),
            "sprite_url": c.sprite_url,
            "name": c.character_name,
            "is_enemy": False,
            "speed": c.speed or 30,
            "movement_remaining": c.speed or 30,
        }

    enemy_spacing = GRID_ROWS // (len(enemies) + 1) if enemies else GRID_ROWS // 2
    for i, c in enumerate(enemies):
        positions[str(c.id)] = {
            "col": 20,
            "row": enemy_spacing * (i + 1),
            "sprite_url": c.sprite_url,
            "name": c.character_name,
            "is_enemy": True,
            "speed": c.speed or 30,
            "movement_remaining": c.speed or 30,
        }

    return positions


def validate_move(char_id: int, direction: str, positions: dict, game_state) -> tuple[bool, str]:
    """Validate a movement request. Returns (ok, error_message)."""
    key = str(char_id)
    if key not in positions:
        return False, "Character not in combat"

    if game_state.current_turn_character_id != char_id:
        return False, "Not your turn"

    pos = positions[key]
    if pos["movement_remaining"] < FEET_PER_CELL:
        return False, "No movement remaining"

    if direction not in DIRECTION_DELTAS:
        return False, "Invalid direction"

    dcol, drow = DIRECTION_DELTAS[direction]
    new_col = pos["col"] + dcol
    new_row = pos["row"] + drow

    if not (0 <= new_col < GRID_COLS and 0 <= new_row < GRID_ROWS):
        return False, "Out of bounds"

    # Collision check
    for cid, cpos in positions.items():
        if cid != key and cpos["col"] == new_col and cpos["row"] == new_row:
            return False, "Cell occupied"

    return True, ""


def execute_move(char_id: int, direction: str, positions: dict) -> dict:
    """Execute a validated move. Mutates positions dict. Returns updated entry."""
    key = str(char_id)
    dcol, drow = DIRECTION_DELTAS[direction]
    pos = positions[key]
    pos["col"] += dcol
    pos["row"] += drow
    pos["movement_remaining"] -= FEET_PER_CELL
    return pos


def compute_enemy_movement(enemy_id: int, positions: dict) -> list[str]:
    """Compute moves for an enemy toward the nearest PC. Returns list of directions."""
    key = str(enemy_id)
    if key not in positions:
        return []

    pos = positions[key]
    ecol, erow = pos["col"], pos["row"]
    remaining = pos["movement_remaining"]

    # Find nearest alive PC
    pcs = [(cid, p) for cid, p in positions.items() if not p["is_enemy"]]
    if not pcs:
        return []

    nearest = min(pcs, key=lambda p: abs(p[1]["col"] - ecol) + abs(p[1]["row"] - erow))
    tcol, trow = nearest[1]["col"], nearest[1]["row"]

    moves = []
    cur_col, cur_row = ecol, erow

    while remaining >= FEET_PER_CELL:
        # Stop if adjacent (within 1 cell = melee range)
        if abs(cur_col - tcol) + abs(cur_row - trow) <= 1:
            break

        dcol = 1 if tcol > cur_col else (-1 if tcol < cur_col else 0)
        drow = 1 if trow > cur_row else (-1 if trow < cur_row else 0)

        # Prefer larger gap axis
        if abs(tcol - cur_col) >= abs(trow - cur_row) and dcol != 0:
            primary, fallback = ("right" if dcol > 0 else "left"), ("down" if drow > 0 else "up") if drow != 0 else None
        elif drow != 0:
            primary, fallback = ("down" if drow > 0 else "up"), ("right" if dcol > 0 else "left") if dcol != 0 else None
        else:
            break

        # Check primary direction
        d = DIRECTION_DELTAS[primary]
        nc, nr = cur_col + d[0], cur_row + d[1]
        occupied = any(p["col"] == nc and p["row"] == nr for cid, p in positions.items() if cid != key)

        if 0 <= nc < GRID_COLS and 0 <= nr < GRID_ROWS and not occupied:
            moves.append(primary)
            cur_col, cur_row = nc, nr
            remaining -= FEET_PER_CELL
        elif fallback:
            d = DIRECTION_DELTAS[fallback]
            nc, nr = cur_col + d[0], cur_row + d[1]
            occupied = any(p["col"] == nc and p["row"] == nr for cid, p in positions.items() if cid != key)
            if 0 <= nc < GRID_COLS and 0 <= nr < GRID_ROWS and not occupied:
                moves.append(fallback)
                cur_col, cur_row = nc, nr
                remaining -= FEET_PER_CELL
            else:
                break
        else:
            break

    return moves


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
    game_state.combat_positions = assign_combat_positions(all_combatants)

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
    game_state.combat_positions = {}

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

    # Reset movement for the new turn's character
    next_key = str(next_entry["character_id"])
    if game_state.combat_positions and next_key in game_state.combat_positions:
        positions = dict(game_state.combat_positions)
        positions[next_key]["movement_remaining"] = positions[next_key].get("speed", 30)
        game_state.combat_positions = positions

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
