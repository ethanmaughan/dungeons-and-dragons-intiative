"""Enemy Learning Tool — global per-species player pattern tracking.

Read during combat (fast DB lookup), write after combat ends.
No AI calls. Weighted averaging merges new data into global profiles.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from server.db.models import EnemyTypeTactics, EncounterLog


def get_player_patterns(enemy_type: str, db) -> dict:
    """Read global learning data for this enemy type.

    Returns empty-state dict if no data exists yet (first encounter).
    Called during combat before the personality tool's Haiku call.
    """
    normalized = enemy_type.lower().strip()
    row = db.query(EnemyTypeTactics).filter(
        EnemyTypeTactics.enemy_type == normalized
    ).first()

    if not row or row.total_encounters == 0:
        return {"total_encounters": 0}

    patterns = row.player_patterns or {}
    counters = row.effective_counters or {}

    # Build human-readable summaries for the personality prompt
    spell_freq = patterns.get("spell_frequency", {})
    action_freq = patterns.get("action_patterns", {})
    positioning = patterns.get("positioning", {})

    # Top 3 spells by frequency
    common_spells = sorted(spell_freq, key=spell_freq.get, reverse=True)[:3]

    # Top 3 actions
    common_actions = sorted(action_freq, key=action_freq.get, reverse=True)[:3]

    # Dominant positioning tendency
    pos_tendency = None
    if positioning:
        pos_tendency = max(positioning, key=positioning.get)

    # Win rate
    total = row.total_encounters
    win_rate = row.win_count / total if total > 0 else None

    return {
        "common_spells": common_spells,
        "common_actions": common_actions,
        "positioning_tendency": pos_tendency,
        "win_rate_against_party": win_rate,
        "effective_counters": list(counters.get("effective_against", [])),
        "total_encounters": total,
    }


def record_encounter_data(
    campaign_id: int,
    enemy_type: str,
    encounter_state,
    outcome: str,
    db,
) -> None:
    """Record encounter data after combat ends.

    1. Appends raw data to EncounterLog.
    2. Merges into EnemyTypeTactics with weighted averaging.
    Called by end_combat() — synchronous, no AI calls.
    """
    normalized = enemy_type.lower().strip()

    # Build raw encounter data
    enc_data = {
        "player_actions": encounter_state.observed_actions,
        "spells_used": encounter_state.observed_spells,
        "damage_dealt_to_enemy": encounter_state.total_damage_to_enemies,
        "damage_taken_by_party": encounter_state.total_damage_to_party,
        "rounds": encounter_state.round_number,
    }

    # 1. Raw log entry
    log = EncounterLog(
        campaign_id=campaign_id,
        enemy_type=normalized,
        encounter_data=enc_data,
        outcome=outcome,
        rounds_elapsed=encounter_state.round_number,
    )
    db.add(log)

    # 2. Get or create the global tactics row
    row = db.query(EnemyTypeTactics).filter(
        EnemyTypeTactics.enemy_type == normalized
    ).first()

    if not row:
        row = EnemyTypeTactics(
            enemy_type=normalized,
            player_patterns={},
            effective_counters={},
            total_encounters=0,
            win_count=0,
            loss_count=0,
        )
        db.add(row)
        db.flush()

    # 3. Merge new data into aggregated patterns
    _merge_encounter_into_tactics(row, encounter_state, outcome)
    row.updated_at = datetime.now(timezone.utc)

    db.commit()


def _merge_encounter_into_tactics(
    row: EnemyTypeTactics,
    encounter_state,
    outcome: str,
) -> None:
    """Merge ephemeral EncounterState into persistent EnemyTypeTactics.

    Uses weighted merge: new data gets 0.3 weight, existing gets 0.7.
    For counters, new data is appended and the list is deduplicated.
    """
    patterns = dict(row.player_patterns or {})
    counters = dict(row.effective_counters or {})

    # Update encounter counts
    row.total_encounters = (row.total_encounters or 0) + 1
    if outcome == "victory":
        row.win_count = (row.win_count or 0) + 1
    elif outcome == "defeat":
        row.loss_count = (row.loss_count or 0) + 1

    # Merge spell frequency
    existing_spells = patterns.get("spell_frequency", {})
    new_spells = dict(Counter(encounter_state.observed_spells))
    merged_spells = _weighted_merge_counts(existing_spells, new_spells)
    patterns["spell_frequency"] = merged_spells

    # Merge action patterns
    existing_actions = patterns.get("action_patterns", {})
    new_actions = dict(Counter(encounter_state.observed_actions))
    merged_actions = _weighted_merge_counts(existing_actions, new_actions)
    patterns["action_patterns"] = merged_actions

    # Infer effective counters from victories
    if outcome == "victory" and encounter_state.observed_spells:
        effective = counters.get("effective_against", [])
        for spell in set(encounter_state.observed_spells):
            if spell not in effective:
                effective.append(spell)
        # Keep list bounded
        counters["effective_against"] = effective[-10:]

    row.player_patterns = patterns
    row.effective_counters = counters


def _weighted_merge_counts(existing: dict, new: dict, new_weight: float = 0.3) -> dict:
    """Merge two count dicts with weighted averaging."""
    merged = dict(existing)
    old_weight = 1.0 - new_weight

    for key, new_count in new.items():
        old_count = merged.get(key, 0)
        merged[key] = round(old_count * old_weight + new_count * new_weight, 1)

    return merged
