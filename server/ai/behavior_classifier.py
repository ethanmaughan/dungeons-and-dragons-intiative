"""Behavior Classifier — tags player actions with behavioral tendencies.

Lightweight agent (keyword match first, Haiku fallback) that classifies
player actions as aggressive, diplomatic, helpful, deceptive, etc.
Runs after each exploration turn to feed the NPC disposition system.
"""

import json
import traceback
from pathlib import Path

from server.config import AI_BACKEND, ANTHROPIC_API_KEY, COMBAT_INTENT_MODEL

PROMPTS_DIR = Path(__file__).parent / "prompts"
BEHAVIOR_SYSTEM = (PROMPTS_DIR / "behavior_classifier.txt").read_text()

if AI_BACKEND == "claude":
    import anthropic
    _classifier_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

CLASSIFIER_MODEL = COMBAT_INTENT_MODEL  # Haiku — same as other lightweight agents

BEHAVIOR_KEYWORDS = {
    "aggressive": ["attack", "kill", "destroy", "smash", "punch", "fight", "stab", "murder"],
    "threatening": ["or else", "threaten", "warn you", "dare you", "intimidate", "suffer"],
    "deceptive": ["lie", "trick", "deceive", "bluff", "disguise", "forge", "pretend", "fake"],
    "diplomatic": ["negotiate", "agree", "compromise", "peace", "alliance", "reason", "persuade", "convince"],
    "helpful": ["help", "assist", "save", "rescue", "protect", "heal", "guide", "give directions"],
    "generous": ["donate", "gift", "offer", "share", "pay extra", "tip", "buy them"],
    "curious": ["investigate", "examine", "ask about", "explore", "inspect", "search", "what is", "tell me about"],
    "intimidating": ["glare", "loom", "scare", "growl", "flex", "tower over"],
    "respectful": ["bow", "thank", "please", "sir", "madam", "honored", "respect", "grateful"],
    "rude": ["insult", "spit", "mock", "laugh at", "ignore", "shut up", "idiot", "fool"],
    "heroic": ["sacrifice", "defend the", "shield them", "stand between", "risk my life", "protect the"],
    "cowardly": ["flee", "hide", "run away", "cower", "abandon", "sneak past"],
}


def _keyword_classify(action_text: str) -> str | None:
    """Fast keyword scan. Returns best-matching behavior tag or None."""
    text = action_text.lower()
    best_tag = None
    best_count = 0

    for tag, keywords in BEHAVIOR_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in text)
        if count > best_count:
            best_count = count
            best_tag = tag

    return best_tag if best_count > 0 else None


def _detect_npc_name(text: str, npc_names: list[str]) -> str | None:
    """Check if any known NPC name appears in the text."""
    text_lower = text.lower()
    for name in npc_names:
        if name.lower() in text_lower:
            return name
    return None


async def classify_action(
    action_text: str,
    narration_text: str,
    npc_names: list[str] | None = None,
) -> dict:
    """Classify a player action's behavioral tone.

    Returns: {"behavior": "diplomatic", "involves_npc": True, "npc_name": "Elder Maren"}
    """
    npc_names = npc_names or []

    # Fast path: keyword match
    keyword_result = _keyword_classify(action_text)
    npc_match = _detect_npc_name(action_text + " " + narration_text, npc_names)

    if keyword_result and keyword_result != "curious":
        # Clear keyword match (curious is too common to trust blindly)
        return {
            "behavior": keyword_result,
            "involves_npc": npc_match is not None,
            "npc_name": npc_match,
        }

    # AI path: Haiku for ambiguous cases
    if AI_BACKEND == "claude":
        try:
            context = f"Player action: {action_text}\nDM response: {narration_text}"
            if npc_names:
                context += f"\nNPCs in scene: {', '.join(npc_names)}"

            response = await _classifier_client.messages.create(
                model=CLASSIFIER_MODEL,
                max_tokens=100,
                system=BEHAVIOR_SYSTEM,
                messages=[{"role": "user", "content": context}],
            )

            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            result = json.loads(raw)
            if "behavior" in result:
                return result

        except Exception:
            traceback.print_exc()

    # Fallback: use keyword result or default to neutral
    return {
        "behavior": keyword_result or "neutral",
        "involves_npc": npc_match is not None,
        "npc_name": npc_match,
    }
