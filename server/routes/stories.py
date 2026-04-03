"""Story API routes: list, assign, import, progress."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session as DBSession

from server.db.database import get_db
from server.db.models import StoryTemplate
from server.services.story_service import (
    assign_story,
    get_current_chapter,
    import_all_stories,
)

router = APIRouter(prefix="/api/stories", tags=["stories"])


@router.get("")
def list_stories(db: DBSession = Depends(get_db)):
    """List all available story templates."""
    stories = db.query(StoryTemplate).all()
    return [
        {
            "slug": s.slug,
            "title": s.title,
            "author": s.author,
            "synopsis": s.synopsis,
            "recommended_level": s.recommended_level,
            "recommended_players": s.recommended_players,
            "chapter_count": len(s.chapters),
        }
        for s in stories
    ]


@router.post("/import")
def import_stories(db: DBSession = Depends(get_db)):
    """Import all story JSON files from data/stories/."""
    stories = import_all_stories(db)
    return {"imported": [s.slug for s in stories], "count": len(stories)}


@router.post("/campaigns/{campaign_id}/assign")
def assign_story_to_campaign(
    campaign_id: int,
    story_slug: str,
    db: DBSession = Depends(get_db),
):
    """Assign a story to a campaign."""
    try:
        cs = assign_story(campaign_id, story_slug, db)
        return {"status": "assigned", "story": cs.story.title, "chapter": cs.current_chapter_number}
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})


@router.get("/campaigns/{campaign_id}/progress")
def get_story_progress(campaign_id: int, db: DBSession = Depends(get_db)):
    """Get the current chapter and objective progress for a campaign."""
    chapter = get_current_chapter(campaign_id, db)
    if not chapter:
        return JSONResponse(status_code=404, content={"error": "No story assigned"})
    return chapter
