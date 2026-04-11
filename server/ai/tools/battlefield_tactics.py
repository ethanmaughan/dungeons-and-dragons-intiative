"""Battlefield Tactics Tool — pure algorithmic grid analysis.

Replaces the simple compute_enemy_movement() with rich spatial analysis:
target scoring, flanking detection, tactical movement, flee checks.
Zero AI calls.
"""

from __future__ import annotations

import secrets

from server.engine.combat import (
    DIRECTION_DELTAS,
    FEET_PER_CELL,
    GRID_COLS,
    GRID_ROWS,
)

FRONTLINE_CLASSES = {"fighter", "barbarian", "paladin", "ranger", "monk"}
BACKLINE_CLASSES = {"wizard", "sorcerer", "warlock", "bard", "cleric", "druid"}


# ---- Geometry helpers ----


def _manhattan(pos_a: dict, pos_b: dict) -> int:
    return abs(pos_a["col"] - pos_b["col"]) + abs(pos_a["row"] - pos_b["row"])


def _cells_reachable(speed_ft: int) -> int:
    return speed_ft // FEET_PER_CELL


def _can_reach_melee(enemy_pos: dict, target_pos: dict, reach_ft: int, speed_ft: int) -> bool:
    reach_cells = max(reach_ft // FEET_PER_CELL, 1)
    dist = _manhattan(enemy_pos, target_pos)
    move_cells = _cells_reachable(speed_ft)
    return dist <= move_cells + reach_cells


def _is_adjacent(pos_a: dict, pos_b: dict) -> bool:
    return _manhattan(pos_a, pos_b) <= 1


# ---- Target scoring ----


def _score_pc_target(
    pc,
    enemy_pos: dict,
    pc_pos: dict,
    monster_data: dict,
    all_positions: dict,
    int_score: int,
) -> tuple[float, str]:
    """Score a PC as a target. Returns (priority_score, reason)."""
    dist = _manhattan(enemy_pos, pc_pos)

    # Mindless: distance is the ONLY factor
    if int_score <= 5:
        return (100.0 - dist, "closest target")

    score = 0.0
    reasons = []

    # Distance factor — closer is better for melee, farther for ranged
    has_ranged = any(
        a.get("type") == "ranged" for a in monster_data.get("actions", [])
    )
    if has_ranged:
        score += max(0, dist - 2) * 3  # Prefer farther targets for ranged
        reasons.append("ranged advantage")
    else:
        score += (20 - dist) * 5  # Prefer closer targets for melee
        reasons.append("melee proximity")

    # HP fraction — low HP targets are appealing (finish them off)
    if pc.hp_max > 0:
        hp_frac = pc.hp_current / pc.hp_max
        if hp_frac < 0.3:
            score += 25
            reasons.append("low HP")
        elif hp_frac < 0.6:
            score += 10

    # AC — low AC is easier to hit
    if pc.ac <= 12:
        score += 15
        reasons.append("low AC")
    elif pc.ac <= 14:
        score += 5

    # Caster class — squishy backliners are priority
    if pc.char_class.lower() in BACKLINE_CLASSES:
        score += 12
        reasons.append("squishy caster")

    # Flanking bonus — ally adjacent to this target
    ally_ids = [
        cid for cid, p in all_positions.items()
        if p.get("is_enemy") and int(cid) != 0  # exclude self handled by caller
    ]
    for aid in ally_ids:
        ally_pos = all_positions[aid]
        if _is_adjacent(ally_pos, pc_pos):
            score += 10
            reasons.append("flanking opportunity")
            break

    return (score, ", ".join(reasons) if reasons else "default")


# ---- Flanking detection ----


def _find_flanking_opportunity(
    enemy_id: int,
    enemy_pos: dict,
    all_positions: dict,
    characters: list,
) -> dict:
    """Check if this enemy can flank a target with an ally."""
    key = str(enemy_id)
    ally_enemies = {
        cid: p for cid, p in all_positions.items()
        if p.get("is_enemy") and cid != key
    }
    pc_positions = {
        cid: p for cid, p in all_positions.items()
        if not p.get("is_enemy")
    }

    for pc_id, pc_pos in pc_positions.items():
        for ally_id, ally_pos in ally_enemies.items():
            if _is_adjacent(ally_pos, pc_pos):
                # Check if we can get to the opposite side
                opp_col = pc_pos["col"] + (pc_pos["col"] - ally_pos["col"])
                opp_row = pc_pos["row"] + (pc_pos["row"] - ally_pos["row"])
                if 0 <= opp_col < GRID_COLS and 0 <= opp_row < GRID_ROWS:
                    occupied = any(
                        p["col"] == opp_col and p["row"] == opp_row
                        for cid, p in all_positions.items() if cid != key
                    )
                    if not occupied:
                        pc_char = next(
                            (c for c in characters if str(c.id) == pc_id),
                            None,
                        )
                        return {
                            "available": True,
                            "flank_partner_id": int(ally_id),
                            "flank_target_id": int(pc_id),
                            "flank_target_name": pc_char.character_name if pc_char else "unknown",
                        }

    return {"available": False, "flank_partner_id": None, "flank_target_id": None}


# ---- Tactical movement ----


def _compute_tactical_movement(
    enemy_id: int,
    enemy_pos: dict,
    target_pos: dict,
    all_positions: dict,
    speed_ft: int,
    action_type: str,
    reach_ft: int = 5,
) -> list[str]:
    """Compute movement path toward (melee) or away from (ranged kite) target."""
    key = str(enemy_id)
    remaining = speed_ft
    cur_col, cur_row = enemy_pos["col"], enemy_pos["row"]
    tcol, trow = target_pos["col"], target_pos["row"]
    moves = []

    while remaining >= FEET_PER_CELL:
        dist = abs(cur_col - tcol) + abs(cur_row - trow)

        if action_type == "melee":
            reach_cells = max(reach_ft // FEET_PER_CELL, 1)
            if dist <= reach_cells:
                break  # Close enough to attack
        elif action_type == "ranged":
            # Try to maintain 3-4 cells distance
            if 3 <= dist <= 5:
                break
            if dist < 3:
                # Move AWAY from target
                tcol_away = cur_col + (cur_col - tcol)
                trow_away = cur_row + (cur_row - trow)
                tcol, trow = tcol_away, trow_away

        dcol = 1 if tcol > cur_col else (-1 if tcol < cur_col else 0)
        drow = 1 if trow > cur_row else (-1 if trow < cur_row else 0)

        if abs(tcol - cur_col) >= abs(trow - cur_row) and dcol != 0:
            primary = "right" if dcol > 0 else "left"
            fallback = ("down" if drow > 0 else "up") if drow != 0 else None
        elif drow != 0:
            primary = "down" if drow > 0 else "up"
            fallback = ("right" if dcol > 0 else "left") if dcol != 0 else None
        else:
            break

        # Try primary direction
        d = DIRECTION_DELTAS[primary]
        nc, nr = cur_col + d[0], cur_row + d[1]
        occupied = any(
            p["col"] == nc and p["row"] == nr
            for cid, p in all_positions.items() if cid != key
        )

        if 0 <= nc < GRID_COLS and 0 <= nr < GRID_ROWS and not occupied:
            moves.append(primary)
            cur_col, cur_row = nc, nr
            remaining -= FEET_PER_CELL
        elif fallback:
            d = DIRECTION_DELTAS[fallback]
            nc, nr = cur_col + d[0], cur_row + d[1]
            occupied = any(
                p["col"] == nc and p["row"] == nr
                for cid, p in all_positions.items() if cid != key
            )
            if 0 <= nc < GRID_COLS and 0 <= nr < GRID_ROWS and not occupied:
                moves.append(fallback)
                cur_col, cur_row = nc, nr
                remaining -= FEET_PER_CELL
            else:
                break
        else:
            break

    return moves


def _compute_flee_movement(
    enemy_id: int,
    enemy_pos: dict,
    all_positions: dict,
    speed_ft: int,
) -> list[str]:
    """Move away from the nearest PC."""
    key = str(enemy_id)
    pcs = [(cid, p) for cid, p in all_positions.items() if not p.get("is_enemy")]
    if not pcs:
        return []

    nearest = min(pcs, key=lambda p: _manhattan(enemy_pos, p[1]))
    _, pc_pos = nearest

    # Invert target — move in opposite direction
    flee_col = enemy_pos["col"] + (enemy_pos["col"] - pc_pos["col"])
    flee_row = enemy_pos["row"] + (enemy_pos["row"] - pc_pos["row"])

    fake_target = {"col": flee_col, "row": flee_row}
    return _compute_tactical_movement(
        enemy_id, enemy_pos, fake_target, all_positions, speed_ft, "melee"
    )


# ---- Flee check ----


def _should_flee(enemy, monster_data: dict, alive_ally_count: int) -> bool:
    """Check if an enemy should flee based on personality and health."""
    int_score = enemy.int_score or 10
    if int_score <= 5:
        return False  # Mindless monsters never flee

    tactics = monster_data.get("tactics", "").lower()
    if "flee" not in tactics and "retreat" not in tactics and "self-preserving" not in tactics:
        return False

    hp_frac = enemy.hp_current / max(enemy.hp_max, 1)

    # Flee if below 25% HP
    if hp_frac <= 0.25:
        return True

    # Flee if below 50% HP AND all allies are dead
    if hp_frac <= 0.5 and alive_ally_count == 0:
        return True

    return False


# ---- Main entry point ----


def analyze_battlefield(
    enemy_id: int,
    positions: dict,
    characters: list,
    monster_data: dict,
    round_number: int = 1,
) -> dict:
    """Analyze the battlefield and return tactical recommendations.

    Pure Python, no AI calls. Returns a structured dict with:
    - recommended target + movement path
    - threat assessment
    - flanking opportunities
    - ranked target priority list
    """
    key = str(enemy_id)
    if key not in positions:
        return _default_fallback(characters, monster_data)

    enemy_pos = positions[key]
    enemy_char = next((c for c in characters if c.id == enemy_id), None)
    if not enemy_char:
        return _default_fallback(characters, monster_data)

    int_score = enemy_char.int_score or 10
    speed = enemy_pos.get("speed", 30)
    reach = 5
    for action in monster_data.get("actions", []):
        if action.get("type") == "melee" and action.get("reach", 5) > reach:
            reach = action["reach"]

    # Find alive PCs
    alive_pcs = []
    for cid, p in positions.items():
        if p.get("is_enemy"):
            continue
        pc = next((c for c in characters if str(c.id) == cid and c.hp_current > 0), None)
        if pc:
            alive_pcs.append((cid, p, pc))

    if not alive_pcs:
        return _default_fallback(characters, monster_data)

    # Count alive allies
    alive_allies = sum(
        1 for cid, p in positions.items()
        if p.get("is_enemy") and cid != key
        and any(c.id == int(cid) and c.hp_current > 0 for c in characters)
    )

    # Flee check
    if _should_flee(enemy_char, monster_data, alive_allies):
        flee_moves = _compute_flee_movement(enemy_id, enemy_pos, positions, speed)
        return {
            "recommended_target_id": None,
            "recommended_target_name": None,
            "recommended_action_type": "flee",
            "movement_path": flee_moves,
            "threat_assessment": {
                "nearest_pc_distance": min(_manhattan(enemy_pos, p) for _, p, _ in alive_pcs),
                "can_reach_melee_this_turn": False,
                "enemy_hp_fraction": enemy_char.hp_current / max(enemy_char.hp_max, 1),
                "outnumbered": len(alive_pcs) > alive_allies + 1,
            },
            "flanking_opportunity": {"available": False, "flank_partner_id": None, "flank_target_id": None},
            "target_priority_list": [],
        }

    # Determine preferred action type (melee vs ranged)
    has_ranged = any(a.get("type") == "ranged" for a in monster_data.get("actions", []))
    nearest_dist = min(_manhattan(enemy_pos, p) for _, p, _ in alive_pcs)
    # Use ranged if we have it AND aren't already in melee
    action_type = "ranged" if has_ranged and nearest_dist > 1 else "melee"

    # Score all targets
    scored_targets = []
    for cid, pc_pos, pc in alive_pcs:
        score, reason = _score_pc_target(
            pc, enemy_pos, pc_pos, monster_data, positions, int_score,
        )
        scored_targets.append({
            "character_id": int(cid),
            "character_name": pc.character_name,
            "score": score,
            "reason": reason,
            "distance": _manhattan(enemy_pos, pc_pos),
            "can_reach": _can_reach_melee(enemy_pos, pc_pos, reach, speed),
        })

    scored_targets.sort(key=lambda t: t["score"], reverse=True)

    # Pick best reachable target (prefer reachable, but if none can be reached, pick best overall)
    best = scored_targets[0]
    for t in scored_targets:
        if t["can_reach"]:
            best = t
            break

    target_pos = positions.get(str(best["character_id"]), enemy_pos)

    # Compute movement
    movement_path = _compute_tactical_movement(
        enemy_id, enemy_pos, target_pos, positions, speed, action_type, reach,
    )

    # Flanking check (only for intelligent enemies)
    flanking = {"available": False, "flank_partner_id": None, "flank_target_id": None}
    if int_score >= 8 and alive_allies > 0:
        flanking = _find_flanking_opportunity(enemy_id, enemy_pos, positions, characters)

    return {
        "recommended_target_id": best["character_id"],
        "recommended_target_name": best["character_name"],
        "recommended_action_type": action_type,
        "movement_path": movement_path,
        "threat_assessment": {
            "nearest_pc_distance": nearest_dist,
            "can_reach_melee_this_turn": best["can_reach"],
            "enemy_hp_fraction": enemy_char.hp_current / max(enemy_char.hp_max, 1),
            "outnumbered": len(alive_pcs) > alive_allies + 1,
        },
        "flanking_opportunity": flanking,
        "target_priority_list": scored_targets[:4],
    }


def _default_fallback(characters: list, monster_data: dict) -> dict:
    """Fallback when positions are missing — pick first alive PC by name."""
    pcs = [c for c in characters if not c.is_npc and not c.is_enemy and c.hp_current > 0]
    target = pcs[0] if pcs else None
    return {
        "recommended_target_id": target.id if target else None,
        "recommended_target_name": target.character_name if target else None,
        "recommended_action_type": "melee",
        "movement_path": [],
        "threat_assessment": {
            "nearest_pc_distance": 1,
            "can_reach_melee_this_turn": True,
            "enemy_hp_fraction": 1.0,
            "outnumbered": False,
        },
        "flanking_opportunity": {"available": False, "flank_partner_id": None, "flank_target_id": None},
        "target_priority_list": [],
    }
