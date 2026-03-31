"""Server-side dice rolling engine. All rolls use cryptographic randomness."""

import re
import secrets


def roll(notation: str) -> dict:
    """Roll dice from standard notation like '2d6+3', 'd20', '4d8-1'.

    Returns: {"notation": "2d6+3", "rolls": [4, 2], "modifier": 3, "total": 9}
    """
    notation = notation.strip().lower()
    match = re.match(r"(\d*)d(\d+)([+-]\d+)?", notation)
    if not match:
        raise ValueError(f"Invalid dice notation: {notation}")

    count = int(match.group(1) or 1)
    sides = int(match.group(2))
    modifier = int(match.group(3) or 0)

    rolls = [secrets.randbelow(sides) + 1 for _ in range(count)]

    return {
        "notation": notation,
        "rolls": rolls,
        "modifier": modifier,
        "total": sum(rolls) + modifier,
    }


def ability_modifier(score: int) -> int:
    """Calculate ability modifier from score."""
    return (score - 10) // 2


def ability_check(score: int, proficiency_bonus: int = 0, proficient: bool = False) -> dict:
    """Roll a d20 ability check with modifier and optional proficiency."""
    mod = ability_modifier(score)
    if proficient:
        mod += proficiency_bonus

    result = roll("1d20")
    result["modifier"] = mod
    result["total"] = result["rolls"][0] + mod
    return result


def attack_roll(attack_bonus: int) -> dict:
    """Roll a d20 attack roll."""
    result = roll("1d20")
    result["modifier"] = attack_bonus
    result["total"] = result["rolls"][0] + attack_bonus
    result["critical"] = result["rolls"][0] == 20
    result["fumble"] = result["rolls"][0] == 1
    return result


def initiative_roll(dex_score: int) -> dict:
    """Roll initiative (d20 + DEX modifier)."""
    return ability_check(dex_score)


def saving_throw(score: int, proficiency_bonus: int = 0, proficient: bool = False) -> dict:
    """Roll a saving throw."""
    return ability_check(score, proficiency_bonus, proficient)
