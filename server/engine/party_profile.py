"""Party Profile — tracks behavioral tendencies across a campaign.

Records classified player behaviors, maintains a rolling window,
and generates natural language summaries for DM prompt injection.
"""


def get_or_create_party_profile(campaign_id: int, db):
    """Get or create the party profile for a campaign."""
    from server.db.models import PartyProfile

    profile = db.query(PartyProfile).filter(PartyProfile.campaign_id == campaign_id).first()
    if profile:
        return profile

    profile = PartyProfile(campaign_id=campaign_id)
    db.add(profile)
    db.flush()
    return profile


def record_behavior(profile, behavior_tag: str):
    """Record a classified behavior. Updates counts, rolling window, and tendencies."""
    from datetime import datetime, timezone

    # Update all-time counts
    counts = dict(profile.behavior_counts or {})
    counts[behavior_tag] = counts.get(behavior_tag, 0) + 1
    profile.behavior_counts = counts
    profile.total_actions_classified = (profile.total_actions_classified or 0) + 1

    # Update rolling window (last 20)
    recent = list(profile.recent_actions or [])
    recent.append(behavior_tag)
    if len(recent) > 20:
        recent = recent[-20:]
    profile.recent_actions = recent

    # Recalculate tendencies
    profile.dominant_tendency = max(counts, key=counts.get) if counts else "neutral"

    if recent:
        recent_counts = {}
        for tag in recent:
            recent_counts[tag] = recent_counts.get(tag, 0) + 1
        profile.recent_tendency = max(recent_counts, key=recent_counts.get)
    else:
        profile.recent_tendency = "neutral"

    profile.updated_at = datetime.now(timezone.utc)


TENDENCY_DESCRIPTIONS = {
    "aggressive": "The party tends to solve problems with force.",
    "threatening": "The party often uses threats and intimidation.",
    "deceptive": "The party frequently relies on deception and trickery.",
    "diplomatic": "The party tends to resolve conflicts through conversation.",
    "helpful": "The party goes out of their way to help those they meet.",
    "generous": "The party is known for their generosity.",
    "curious": "The party is inquisitive, always asking questions and investigating.",
    "intimidating": "The party has a reputation for being intimidating.",
    "respectful": "The party treats others with respect and courtesy.",
    "rude": "The party has a reputation for being rude and dismissive.",
    "heroic": "The party is known for acts of bravery and self-sacrifice.",
    "cowardly": "The party tends to avoid danger when possible.",
    "neutral": "The party has no strong behavioral tendencies yet.",
}


def get_profile_summary(profile) -> str | None:
    """Generate a natural language summary of the party's behavioral profile.

    Returns a 2-3 sentence string for DM prompt injection, or None if not enough data.
    """
    if not profile or (profile.total_actions_classified or 0) < 3:
        return None

    counts = profile.behavior_counts or {}
    total = profile.total_actions_classified or 1
    dominant = profile.dominant_tendency or "neutral"
    recent = profile.recent_tendency or "neutral"

    # Calculate percentage for dominant tendency
    dominant_pct = int((counts.get(dominant, 0) / total) * 100) if total > 0 else 0

    desc = TENDENCY_DESCRIPTIONS.get(dominant, TENDENCY_DESCRIPTIONS["neutral"])

    lines = [
        "## Party Behavioral Profile",
        f"Overall tendency: {dominant.title()} ({dominant_pct}% of interactions). {desc}",
    ]

    if recent != dominant:
        recent_desc = TENDENCY_DESCRIPTIONS.get(recent, "")
        lines.append(f"Recent tendency: {recent.title()}. {recent_desc}")

    # Top 3 behaviors for nuance
    if len(counts) > 1:
        sorted_behaviors = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:3]
        behavior_str = ", ".join(f"{tag} ({count})" for tag, count in sorted_behaviors)
        lines.append(f"Behavioral breakdown: {behavior_str}")

    return "\n".join(lines)
