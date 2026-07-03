from datetime import datetime
from typing import Dict, Any, List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from supabase import Client

from api.auth import get_current_user, get_request_scoped_db
from api.routes.sites import SiteResponse, is_user_admin

router = APIRouter(prefix="/sites", tags=["publish"])


def verify_site_authorization(db: Client, user_id: str, site_id: UUID) -> Dict[str, Any]:
    """
    Helper to verify that the site exists and the user is either the owner or an admin.
    Raises 404 if the site does not exist or access is denied.
    """
    is_admin = is_user_admin(db, user_id)
    query = db.table("sites").select("*").eq("id", str(site_id))
    if not is_admin:
        query = query.eq("owner_id", user_id)

    try:
        res = query.execute()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed: {str(e)}"
        )

    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Site not found or access denied."
        )
    return res.data[0]


@router.post("/{site_id}/publish", response_model=SiteResponse)
async def publish_site(
    site_id: UUID,
    db: Client = Depends(get_request_scoped_db),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Publishes a site by updating its status to 'published'.
    Scoped to the owner or an admin.
    Also creates a publication revision snapshot.
    """
    user_id = user["sub"]
    original_site = verify_site_authorization(db, user_id, site_id)

    update_data = {
        "status": "published",
        "updated_at": datetime.utcnow().isoformat()
    }

    try:
        res = db.table("sites").update(update_data).eq("id", str(site_id)).execute()
        if not res.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Update operation did not return updated metadata."
            )
        published_site = res.data[0]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to publish site: {str(e)}"
        )

    # 1. Query revisions for the highest revision_number
    rev_number = 1
    try:
        rev_res = db.table("revisions").select("revision_number").eq("site_id", str(site_id)).order("revision_number", desc=True).limit(1).execute()
        if rev_res.data:
            rev_number = int(rev_res.data[0]["revision_number"]) + 1
    except Exception as e:
        # Rollback
        try:
            db.table("sites").update({
                "status": original_site.get("status"),
                "updated_at": original_site.get("updated_at")
            }).eq("id", str(site_id)).execute()
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to publish site during revision number query: {str(e)}"
        )

    # 2. Fetch all active sections
    try:
        sections_res = db.table("site_sections").select("*").eq("site_id", str(site_id)).order("position").execute()
        sections_list = sections_res.data or []
    except Exception as e:
        # Rollback
        try:
            db.table("sites").update({
                "status": original_site.get("status"),
                "updated_at": original_site.get("updated_at")
            }).eq("id", str(site_id)).execute()
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to publish site during sections snapshot: {str(e)}"
        )

    # 3. Insert publication snapshot into revisions
    revision_data = {
        "site_id": str(site_id),
        "revision_number": rev_number,
        "site_snapshot": published_site,
        "sections_snapshot": sections_list
    }

    try:
        db.table("revisions").insert(revision_data).execute()
    except Exception as e:
        # Rollback
        try:
            db.table("sites").update({
                "status": original_site.get("status"),
                "updated_at": original_site.get("updated_at")
            }).eq("id", str(site_id)).execute()
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to insert publication snapshot revision: {str(e)}"
        )

    return published_site


@router.post("/{site_id}/unpublish", response_model=SiteResponse)
async def unpublish_site(
    site_id: UUID,
    db: Client = Depends(get_request_scoped_db),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Unpublishes a site by updating its status to 'draft'.
    Scoped to the owner or an admin.
    """
    user_id = user["sub"]
    verify_site_authorization(db, user_id, site_id)

    update_data = {
        "status": "draft",
        "updated_at": datetime.utcnow().isoformat()
    }

    try:
        res = db.table("sites").update(update_data).eq("id", str(site_id)).execute()
        if not res.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Update operation did not return updated metadata."
            )
        return res.data[0]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to unpublish site: {str(e)}"
        )


# --- Pydantic Schemas for Revisions ---

class RevisionMetadataResponse(BaseModel):
    id: UUID
    site_id: UUID
    revision_number: int
    created_at: datetime

    class Config:
        from_attributes = True


class RevisionDetailResponse(BaseModel):
    id: UUID
    site_id: UUID
    revision_number: int
    site_snapshot: Dict[str, Any]
    sections_snapshot: List[Dict[str, Any]]
    created_at: datetime

    class Config:
        from_attributes = True


# --- Endpoints for Revisions ---

@router.get("/{site_id}/revisions", response_model=List[RevisionMetadataResponse])
async def get_site_revisions(
    site_id: UUID,
    db: Client = Depends(get_request_scoped_db),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Retrieve a list of revisions metadata for the site.
    Scoped to site owners or admins.
    """
    user_id = user["sub"]
    verify_site_authorization(db, user_id, site_id)

    try:
        res = db.table("revisions").select("id", "site_id", "revision_number", "created_at").eq("site_id", str(site_id)).order("revision_number", desc=True).execute()
        return res.data or []
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve site revisions: {str(e)}"
        )


@router.get("/revisions/{revision_id}", response_model=RevisionDetailResponse)
async def get_revision_details(
    revision_id: UUID,
    db: Client = Depends(get_request_scoped_db),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Retrieve the full details/snapshots of a specific revision.
    Verifies site ownership or admin permissions.
    """
    user_id = user["sub"]
    try:
        res = db.table("revisions").select("*").eq("id", str(revision_id)).execute()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to query revision: {str(e)}"
        )

    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Revision not found."
        )

    revision = res.data[0]
    site_id_str = revision["site_id"]
    
    # Verify site ownership or admin permissions
    verify_site_authorization(db, user_id, UUID(site_id_str))

    return revision
