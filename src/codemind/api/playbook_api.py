"""
Playbook CRUD API — Create, Read, Update, Delete, Publish, Install.

Provides REST endpoints for the PlaybookStore and Composer.
"""

import json
import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from .auth import require_user

router = APIRouter(prefix="/api/v1/playbooks", tags=["playbooks"])

# Lazy reference — set during server init
_db = None


def init_playbook_api(db):
    """Initialize with database reference."""
    global _db
    _db = db
    # Seed built-in playbooks on first run
    _seed_builtins()


# ── Request / Response Models ──────────────────────────────────────

class PlaybookCreateRequest(BaseModel):
    name: str
    description: str = ""
    when_to_use: str = ""
    category: str = "analysis"
    complexity: str = "medium"
    icon: str = "Brain"
    color: str = "violet"
    system_prompt: str = "You are a helpful coding assistant."
    search_strategy: dict = Field(default_factory=lambda: {"mode": "hybrid", "limit": 100, "min_score": 0.3, "queries": []})
    output_schema: dict = Field(default_factory=dict)
    behavior: dict = Field(default_factory=lambda: {"exclude_test_files": False, "grounding_fence": False, "inject_repo_metadata": False})
    examples: list = Field(default_factory=list)
    anti_patterns: list = Field(default_factory=list)
    quality_rubric: list = Field(default_factory=list)
    evaluation_rules: list = Field(default_factory=list)
    templates: list = Field(default_factory=list)
    requires_repo: bool = True
    tags: list = Field(default_factory=list)


class PlaybookUpdateRequest(BaseModel):
    description: Optional[str] = None
    when_to_use: Optional[str] = None
    category: Optional[str] = None
    complexity: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    system_prompt: Optional[str] = None
    search_strategy: Optional[dict] = None
    output_schema: Optional[dict] = None
    behavior: Optional[dict] = None
    examples: Optional[list] = None
    anti_patterns: Optional[list] = None
    quality_rubric: Optional[list] = None
    evaluation_rules: Optional[list] = None
    templates: Optional[list] = None
    requires_repo: Optional[bool] = None
    tags: Optional[list] = None


class PlaybookResponse(BaseModel):
    id: str
    name: str
    version: str
    description: str
    when_to_use: str
    category: str
    complexity: str
    author: str
    author_user_id: str | None = None
    is_builtin: bool
    is_published: bool
    icon: str
    color: str
    system_prompt: str
    search_strategy: dict
    output_schema: dict
    behavior: dict
    examples: list
    anti_patterns: list
    quality_rubric: list
    evaluation_rules: list
    templates: list
    requires_repo: bool
    tags: list
    downloads: int
    rating: float
    created_at: int
    updated_at: int
    likes_count: int


# ── Helpers ────────────────────────────────────────────────────────

def _json_loads(val, default=None):
    """Safely parse a JSON string."""
    if val is None:
        return default if default is not None else []
    if isinstance(val, (list, dict)):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else []


def _row_to_dict(row) -> dict:
    """Convert SQLAlchemy row to API response dict."""
    return {
        "id": row.id,
        "name": row.name,
        "version": row.version or "1.0",
        "description": row.description or "",
        "when_to_use": row.when_to_use or "",
        "category": row.category or "analysis",
        "complexity": row.complexity or "medium",
        "author": row.author or "user",
        "author_user_id": row.author_user_id,
        "is_builtin": bool(row.is_builtin),
        "is_published": bool(row.is_published),
        "icon": row.icon or "Brain",
        "color": row.color or "violet",
        "system_prompt": row.system_prompt or "",
        "search_strategy": _json_loads(row.search_strategy, {}),
        "output_schema": _json_loads(row.output_schema, {}),
        "behavior": _json_loads(row.behavior, {}),
        "examples": _json_loads(row.examples),
        "anti_patterns": _json_loads(row.anti_patterns),
        "quality_rubric": _json_loads(row.quality_rubric),
        "evaluation_rules": _json_loads(row.evaluation_rules),
        "templates": _json_loads(row.templates),
        "requires_repo": bool(row.requires_repo),
        "tags": _json_loads(row.tags),
        "downloads": row.downloads or 0,
        "rating": row.rating or 0.0,
        "created_at": row.created_at or 0,
        "updated_at": row.updated_at or 0,
        "likes_count": row.likes_count or 0,
    }


def _seed_builtins():
    """Seed built-in playbooks from filesystem on first run."""
    from ..playbooks.registry import PlaybookRegistry

    session = _db.get_session()
    try:
        from ..storage.database import PlaybookStoreModel

        # We will now upsert (sync) local MD files on every startup
        # instead of skipping if already seeded.

        registry = PlaybookRegistry()
        icon_map = {
            "analyze_codebase": ("Code2", "blue"),
            "explore_codebase": ("Compass", "teal"),
            "evaluate_build_vs_reuse": ("Scale", "emerald"),
            "design_solution": ("Layers", "indigo"),
            "generate_catalog": ("Package", "orange"),
            "search_catalogs": ("Search", "amber"),
        }
        requires_repo_map = {
            "search_catalogs": False,
            "design_solution": False,
            "evaluate_build_vs_reuse": False,
        }
        template_map = {
            "analyze_codebase": [
                {"label": "Architecture overview", "prompt": "What is the high-level architecture of this codebase?"},
                {"label": "API endpoints", "prompt": "List all API endpoints with their request/response schemas"},
                {"label": "Key design patterns", "prompt": "What design patterns are used in this codebase?"},
            ],
            "explore_codebase": [
                {"label": "Project structure", "prompt": "Show me the project structure and main entry points"},
                {"label": "Find authentication logic", "prompt": "Where is the authentication and authorization logic?"},
                {"label": "Database schema", "prompt": "What is the database schema and data model?"},
            ],
            "search_catalogs": [
                {"label": "Agent framework", "prompt": "Find components for building an AI agent framework"},
                {"label": "REST API gateway", "prompt": "Find a REST API gateway or proxy service"},
            ],
            "design_solution": [
                {"label": "E-commerce platform", "prompt": "Design an e-commerce platform with catalog, cart, payments"},
                {"label": "ML pipeline", "prompt": "Design an ML pipeline with data ingestion, training, and serving"},
            ],
            "evaluate_build_vs_reuse": [
                {"label": "Auth service", "prompt": "Should I build or reuse an authentication service?"},
            ],
            "generate_catalog": [
                {"label": "Full catalog entry", "prompt": "Generate a comprehensive catalog entry for this repository"},
            ],
        }

        now = int(time.time())
        updated_count = 0
        inserted_count = 0
        
        # Keep track of active playbook names
        active_playbooks = set()
        
        for name in registry.list_playbooks():
            active_playbooks.add(name)
            pb = registry.get_playbook(name)
            if not pb:
                continue
            icon, color = icon_map.get(name, ("Brain", "violet"))
            
            existing = session.query(PlaybookStoreModel).filter_by(name=pb.name, is_builtin=1).first()
            if existing:
                # Update existing built-in to sync with local file changes
                existing.version = pb.version
                existing.description = pb.description
                existing.when_to_use = pb.when_to_use
                existing.category = pb.category
                existing.complexity = pb.complexity_level
                existing.system_prompt = pb.system_prompt
                existing.search_strategy = json.dumps(pb.search_strategy.model_dump() if pb.search_strategy else {})
                existing.output_schema = json.dumps(pb.output_schema)
                existing.behavior = json.dumps(pb.behavioral_flags)
                existing.examples = json.dumps(pb.examples)
                existing.anti_patterns = json.dumps(pb.anti_patterns)
                existing.quality_rubric = json.dumps(pb.quality_rubric)
                existing.evaluation_rules = json.dumps(pb.evaluation_rules)
                existing.templates = json.dumps(template_map.get(name, []))
                existing.requires_repo = 0 if not requires_repo_map.get(name, True) else 1
                existing.tags = json.dumps([pb.category, pb.complexity_level])
                existing.updated_at = now
                updated_count += 1
            else:
                row = PlaybookStoreModel(
                    id=str(uuid.uuid4()),
                    name=pb.name,
                    version=pb.version,
                    description=pb.description,
                    when_to_use=pb.when_to_use,
                    category=pb.category,
                    complexity=pb.complexity_level,
                    author="system",
                    is_builtin=1,
                    is_published=1,
                    icon=icon,
                    color=color,
                    system_prompt=pb.system_prompt,
                    search_strategy=json.dumps(pb.search_strategy.model_dump() if pb.search_strategy else {}),
                    output_schema=json.dumps(pb.output_schema),
                    behavior=json.dumps(pb.behavioral_flags),
                    examples=json.dumps(pb.examples),
                    anti_patterns=json.dumps(pb.anti_patterns),
                    quality_rubric=json.dumps(pb.quality_rubric),
                    evaluation_rules=json.dumps(pb.evaluation_rules),
                    templates=json.dumps(template_map.get(name, [])),
                    requires_repo=0 if not requires_repo_map.get(name, True) else 1,
                    tags=json.dumps([pb.category, pb.complexity_level]),
                    downloads=0,
                    rating=0.0,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                inserted_count += 1

        # Delete any built-in playbooks from the DB that are no longer in the active registry
        deleted_count = session.query(PlaybookStoreModel).filter(
            PlaybookStoreModel.is_builtin == 1,
            PlaybookStoreModel.name.notin_(list(active_playbooks))
        ).delete(synchronize_session=False)

        session.commit()
        print(f"[PLAYBOOK_API] ✓ Synced built-in playbooks (Updated: {updated_count}, Inserted: {inserted_count}, Deleted: {deleted_count})")
    except Exception as e:
        session.rollback()
        print(f"[PLAYBOOK_API] Seed error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()


# ── Routes ─────────────────────────────────────────────────────────

@router.get("")
async def list_playbooks(
    category: Optional[str] = None, 
    published_only: bool = False,
    user: dict = Depends(require_user)
):
    """List playbooks visible to the user.
    
    Includes:
    - User's own playbooks
    - Built-in system playbooks
    - Publicly published playbooks
    """
    from ..storage.database import PlaybookStoreModel
    from sqlalchemy import or_

    session = _db.get_session()
    try:
        q = session.query(PlaybookStoreModel)
        
        # Ownership/Visibility filter
        q = q.filter(or_(
            PlaybookStoreModel.author_user_id == user["user_id"],
            PlaybookStoreModel.is_builtin == 1,
            PlaybookStoreModel.is_published == 1
        ))

        if category:
            q = q.filter_by(category=category)
        if published_only:
            q = q.filter_by(is_published=1)
        rows = q.order_by(PlaybookStoreModel.name).all()
        return [_row_to_dict(r) for r in rows]
    finally:
        session.close()


@router.get("/store")
async def browse_store():
    """Browse published playbooks in the PlaybookStore."""
    from ..storage.database import PlaybookStoreModel

    session = _db.get_session()
    try:
        rows = session.query(PlaybookStoreModel).filter_by(is_published=1).order_by(
            PlaybookStoreModel.downloads.desc()
        ).all()
        return [_row_to_dict(r) for r in rows]
    finally:
        session.close()


@router.get("/{playbook_id}")
async def get_playbook(playbook_id: str):
    """Get a single playbook by ID."""
    from ..storage.database import PlaybookStoreModel

    session = _db.get_session()
    try:
        row = session.query(PlaybookStoreModel).filter_by(id=playbook_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Playbook not found")
        return _row_to_dict(row)
    finally:
        session.close()


@router.post("")
async def create_playbook(req: PlaybookCreateRequest, user: dict = Depends(require_user)):
    """Create a new user playbook."""
    from ..storage.database import PlaybookStoreModel

    session = _db.get_session()
    try:
        # Check name uniqueness
        existing = session.query(PlaybookStoreModel).filter_by(name=req.name).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Playbook '{req.name}' already exists")

        now = int(time.time())
        row = PlaybookStoreModel(
            id=str(uuid.uuid4()),
            name=req.name,
            version="1.0",
            description=req.description,
            when_to_use=req.when_to_use,
            category=req.category,
            complexity=req.complexity,
            author=user["full_name"] or "user",
            author_user_id=user["user_id"],
            is_builtin=0,
            is_published=0,
            icon=req.icon,
            color=req.color,
            system_prompt=req.system_prompt,
            search_strategy=json.dumps(req.search_strategy),
            output_schema=json.dumps(req.output_schema),
            behavior=json.dumps(req.behavior),
            examples=json.dumps(req.examples),
            anti_patterns=json.dumps(req.anti_patterns),
            quality_rubric=json.dumps(req.quality_rubric),
            evaluation_rules=json.dumps(req.evaluation_rules),
            templates=json.dumps(req.templates),
            requires_repo=1 if req.requires_repo else 0,
            tags=json.dumps(req.tags),
            downloads=0,
            rating=0.0,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _row_to_dict(row)
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.put("/{playbook_id}")
async def update_playbook(playbook_id: str, req: PlaybookUpdateRequest, user: dict = Depends(require_user)):
    """Update an existing playbook."""
    from ..storage.database import PlaybookStoreModel

    session = _db.get_session()
    try:
        row = session.query(PlaybookStoreModel).filter_by(id=playbook_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Playbook not found")

        # Update only provided fields
        updates = req.model_dump(exclude_unset=True)
        json_fields = {"search_strategy", "output_schema", "behavior", "examples",
                       "anti_patterns", "quality_rubric", "evaluation_rules", "templates", "tags"}
        for key, val in updates.items():
            if key in json_fields:
                setattr(row, key, json.dumps(val))
            elif key == "requires_repo":
                row.requires_repo = 1 if val else 0
            else:
                setattr(row, key, val)
        row.updated_at = int(time.time())

        session.commit()
        session.refresh(row)
        return _row_to_dict(row)
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.delete("/custom/all")
async def delete_all_custom_playbooks(user: dict = Depends(require_user)):
    """Delete all custom playbooks created by the current user."""
    from ..storage.database import PlaybookStoreModel

    session = _db.get_session()
    try:
        deleted_count = session.query(PlaybookStoreModel).filter_by(
            is_builtin=0,
            author_user_id=user["user_id"]
        ).delete()
        session.commit()
        return {"status": "deleted", "count": deleted_count}
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.delete("/{playbook_id}")
async def delete_playbook(playbook_id: str, user: dict = Depends(require_user)):
    """Delete a user playbook (cannot delete built-ins)."""
    from ..storage.database import PlaybookStoreModel

    session = _db.get_session()
    try:
        row = session.query(PlaybookStoreModel).filter_by(id=playbook_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Playbook not found")
        if row.is_builtin:
            raise HTTPException(status_code=403, detail="Cannot delete built-in playbooks")
        session.delete(row)
        session.commit()
        return {"status": "deleted", "id": playbook_id}
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()



@router.post("/{playbook_id}/publish")
async def publish_playbook(playbook_id: str, user: dict = Depends(require_user)):
    """Publish a playbook to the store."""
    from ..storage.database import PlaybookStoreModel

    session = _db.get_session()
    try:
        row = session.query(PlaybookStoreModel).filter_by(id=playbook_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Playbook not found")
        if row.author_user_id != user["user_id"]:
            raise HTTPException(status_code=403, detail="You can only publish your own playbooks")
        row.is_published = 1
        row.updated_at = int(time.time())
        session.commit()
        return {"status": "published", "id": playbook_id}
    finally:
        session.close()


@router.post("/{playbook_id}/unpublish")
async def unpublish_playbook(playbook_id: str, user: dict = Depends(require_user)):
    """Unpublish a playbook from the store."""
    from ..storage.database import PlaybookStoreModel

    session = _db.get_session()
    try:
        row = session.query(PlaybookStoreModel).filter_by(id=playbook_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Playbook not found")
        if row.is_builtin:
            raise HTTPException(status_code=403, detail="Cannot unpublish built-in playbooks")
        if row.author_user_id != user["user_id"]:
            raise HTTPException(status_code=403, detail="You can only unpublish your own playbooks")
        row.is_published = 0
        row.updated_at = int(time.time())
        session.commit()
        return {"status": "unpublished", "id": playbook_id}
    except HTTPException:
        raise
    finally:
        session.close()


@router.post("/{playbook_id}/clone")
async def clone_playbook(playbook_id: str, user: dict = Depends(require_user)):
    """Clone an existing playbook as a new user playbook."""
    from ..storage.database import PlaybookStoreModel

    session = _db.get_session()
    try:
        source = session.query(PlaybookStoreModel).filter_by(id=playbook_id).first()
        if not source:
            raise HTTPException(status_code=404, detail="Source playbook not found")

        # Find a unique name
        base_name = f"{source.name}_copy"
        name = base_name
        counter = 2
        while session.query(PlaybookStoreModel).filter_by(name=name).first():
            name = f"{base_name}_{counter}"
            counter += 1

        now = int(time.time())
        clone = PlaybookStoreModel(
            id=str(uuid.uuid4()),
            name=name,
            version="1.0",
            description=source.description,
            when_to_use=source.when_to_use,
            category=source.category,
            complexity=source.complexity,
            author=user["full_name"] or "user",
            author_user_id=user["user_id"],
            is_builtin=0,
            is_published=0,
            icon=source.icon,
            color=source.color,
            system_prompt=source.system_prompt,
            search_strategy=source.search_strategy,
            output_schema=source.output_schema,
            behavior=source.behavior,
            examples=source.examples,
            anti_patterns=source.anti_patterns,
            quality_rubric=source.quality_rubric,
            evaluation_rules=source.evaluation_rules,
            templates=source.templates,
            requires_repo=source.requires_repo,
            tags=source.tags,
            downloads=0,
            rating=0.0,
            created_at=now,
            updated_at=now,
        )
        session.add(clone)
        session.commit()
        session.refresh(clone)
        return _row_to_dict(clone)
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.post("/{playbook_id}/like")
async def like_playbook(playbook_id: str, user: dict = Depends(require_user)):
    """Increment like count for a playbook.
    
    Simple aggregate counter – if you later add user identity,
    enforce per-user uniqueness in a separate join table.
    """
    from ..storage.database import PlaybookStoreModel

    session = _db.get_session()
    try:
        row = session.query(PlaybookStoreModel).filter_by(id=playbook_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Playbook not found")

        row.likes_count = (row.likes_count or 0) + 1
        row.updated_at = int(time.time())

        session.commit()
        session.refresh(row)
        return _row_to_dict(row)
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()
