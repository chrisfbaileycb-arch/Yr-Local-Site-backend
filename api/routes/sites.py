from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator
from postgrest.exceptions import APIError
from supabase import Client

from api.auth import get_current_user, get_request_scoped_db
from api.ssr_renderer import validate_brand_color

router = APIRouter(prefix="/sites", tags=["sites"])


# --- Pydantic Schemas for Sections ---

class SiteSectionCreate(BaseModel):
    kind: Literal['hero', 'about', 'services', 'gallery', 'testimonials', 'contact', 'cta', 'footer', 'custom_html']
    position: int = Field(default=0, ge=0, description="Display position/order of the section")
    content: Dict[str, Any] = Field(default_factory=dict, description="Section specific content payload")


class SiteSectionResponse(BaseModel):
    id: UUID
    site_id: UUID
    kind: Literal['hero', 'about', 'services', 'gallery', 'testimonials', 'contact', 'cta', 'footer', 'custom_html']
    position: int
    content: Dict[str, Any]
    updated_at: datetime

    class Config:
        from_attributes = True


# --- Pydantic Schemas for Products ---

class ProductCreate(BaseModel):
    name: str = Field(..., min_length=1)
    description: str
    price_label: str = Field(..., min_length=1)
    payment_link_url: str = Field(..., pattern=r"^https?://[^\s/$.?#].[^\s]*$")
    active: bool = True

    @field_validator("name", "price_label")
    @classmethod
    def check_not_empty_whitespace(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field cannot be empty or solely whitespace.")
        return v


class ProductUpdate(BaseModel):
    id: Optional[UUID] = None
    name: Optional[str] = None
    description: Optional[str] = None
    price_label: Optional[str] = None
    payment_link_url: Optional[str] = Field(None, pattern=r"^https?://[^\s/$.?#].[^\s]*$")
    active: Optional[bool] = None

    @field_validator("name", "price_label")
    @classmethod
    def check_not_empty_whitespace(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("Field cannot be empty or solely whitespace.")
        return v


class ProductResponse(BaseModel):
    id: UUID
    site_id: UUID
    name: str
    description: str
    price_label: str
    payment_link_url: str
    active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# --- Pydantic Schemas for Sites ---

class SiteBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", description="Lowercase URL-safe slug")
    status: Literal['draft', 'published'] = 'draft'
    seo_title: Optional[str] = Field(None, max_length=60)
    seo_description: Optional[str] = Field(None, max_length=155)
    og_image_url: Optional[str] = None
    brand_color: Optional[str] = None
    # ── Client management ──────────────────────────────────────
    client_name: Optional[str] = None
    client_email: Optional[str] = None
    custom_domain: Optional[str] = None
    monthly_rate: Optional[float] = None
    client_notes: Optional[str] = None
    # ── Deployment tracking ────────────────────────────────────
    netlify_site_id: Optional[str] = None
    deployment_url: Optional[str] = None
    deployment_status: Optional[str] = 'not_deployed'
    last_deployed_at: Optional[datetime] = None


class SiteCreate(SiteBase):
    sections: Optional[List[SiteSectionCreate]] = None
    products: Optional[List[ProductCreate]] = None

    @field_validator("brand_color")
    @classmethod
    def check_brand_color(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not validate_brand_color(v):
            raise ValueError("Invalid brand color format. Must be hex, oklch, rgb, rgba, hsl, or hsla.")
        return v


class SiteUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    slug: Optional[str] = Field(None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    status: Optional[Literal['draft', 'published']] = None
    seo_title: Optional[str] = Field(None, max_length=60)
    seo_description: Optional[str] = Field(None, max_length=155)
    og_image_url: Optional[str] = None
    brand_color: Optional[str] = None
    sections: Optional[List[SiteSectionCreate]] = None
    products: Optional[List[ProductUpdate]] = None
    # ── Client management ──────────────────────────────────────
    client_name: Optional[str] = None
    client_email: Optional[str] = None
    custom_domain: Optional[str] = None
    monthly_rate: Optional[float] = None
    client_notes: Optional[str] = None

    @field_validator("brand_color")
    @classmethod
    def check_brand_color(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not validate_brand_color(v):
            raise ValueError("Invalid brand color format. Must be hex, oklch, rgb, rgba, hsl, or hsla.")
        return v


class SiteResponse(SiteBase):
    id: UUID
    owner_id: UUID
    created_at: datetime
    updated_at: datetime
    products: List[ProductResponse] = []

    class Config:
        from_attributes = True


class SiteDetailResponse(SiteResponse):
    sections: List[SiteSectionResponse] = []


# --- Helper functions ---

def is_user_admin(db: Client, user_id: str) -> bool:
    """Helper to check if the authenticated user has the 'admin' role."""
    try:
        roles_res = db.table("user_roles").select("role").eq("user_id", user_id).execute()
        return any(r.get("role") == "admin" for r in roles_res.data)
    except Exception:
        return False


# --- Endpoints ---

@router.get("", response_model=List[SiteResponse])
async def list_sites(
    db: Client = Depends(get_request_scoped_db),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """
    List all sites.
    - Admins can list all sites.
    - Regular users can only list sites where owner_id matches their user ID.
    - Fetches associated products using a single bulk database query to avoid N+1 query problem.
    """
    user_id = user["sub"]
    is_admin = is_user_admin(db, user_id)

    query = db.table("sites").select("*")
    if not is_admin:
        query = query.eq("owner_id", user_id)

    try:
        res = query.execute()
        sites = res.data or []
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed: {str(e)}"
        )

    if sites:
        site_ids = [str(s["id"]) for s in sites]
        try:
            products_res = db.table("products").select("*").in_("site_id", site_ids).execute()
            products_list = products_res.data or []
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Database query failed for products: {str(e)}"
            )

        products_by_site = {}
        for prod in products_list:
            sid = prod.get("site_id")
            if sid:
                products_by_site.setdefault(sid, []).append(prod)

        for site in sites:
            sid = str(site["id"])
            site["products"] = products_by_site.get(sid, [])

    return sites


@router.get("/{site_id}", response_model=SiteDetailResponse)
async def get_site(
    site_id: UUID,
    db: Client = Depends(get_request_scoped_db),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Retrieve a specific site's metadata, sections, and products.
    """
    user_id = user["sub"]
    is_admin = is_user_admin(db, user_id)

    query = db.table("sites").select("*").eq("id", str(site_id))
    if not is_admin:
        query = query.eq("owner_id", user_id)

    try:
        site_res = query.execute()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed: {str(e)}"
        )

    if not site_res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Site not found or access denied."
        )

    site = site_res.data[0]

    # Fetch sections ordered by position
    try:
        sections_res = db.table("site_sections").select("*").eq("site_id", str(site_id)).order("position").execute()
        sections = sections_res.data or []
        sections.sort(key=lambda s: s.get("position", 0))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed for sections: {str(e)}"
        )

    # Fetch products
    try:
        products_res = db.table("products").select("*").eq("site_id", str(site_id)).execute()
        products = products_res.data or []
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed for products: {str(e)}"
        )

    return {**site, "sections": sections, "products": products}


@router.post("", response_model=SiteDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_site(
    payload: SiteCreate,
    db: Client = Depends(get_request_scoped_db),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Create a new site with optional nested sections and products.
    """
    owner_id = user["sub"]
    site_data = {
        "owner_id": owner_id,
        "name": payload.name,
        "slug": payload.slug,
        "status": payload.status,
        "seo_title": payload.seo_title,
        "seo_description": payload.seo_description,
        "og_image_url": payload.og_image_url,
        "brand_color": payload.brand_color,
    }

    try:
        site_res = db.table("sites").insert(site_data).execute()
    except Exception as e:
        err_msg = str(e).lower()
        if "duplicate" in err_msg or "23505" in err_msg or "unique" in err_msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Slug '{payload.slug}' is already in use."
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create site metadata: {str(e)}"
        )

    if not site_res.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create site: database did not return any data."
        )

    created_site = site_res.data[0]
    site_id = created_site["id"]

    inserted_sections = []
    if payload.sections:
        sections_data = [
            {
                "site_id": site_id,
                "kind": sec.kind,
                "position": sec.position,
                "content": sec.content
            }
            for sec in payload.sections
        ]
        try:
            sec_res = db.table("site_sections").insert(sections_data).execute()
            inserted_sections = sec_res.data or []
            inserted_sections.sort(key=lambda s: s.get("position", 0))
        except Exception as e:
            try:
                db.table("sites").delete().eq("id", site_id).execute()
            except Exception:
                pass
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to insert site sections: {str(e)}"
            )

    inserted_products = []
    if payload.products:
        products_data = [
            {
                "site_id": site_id,
                "name": prod.name,
                "description": prod.description,
                "price_label": prod.price_label,
                "payment_link_url": prod.payment_link_url,
                "active": prod.active,
            }
            for prod in payload.products
        ]
        try:
            prod_res = db.table("products").insert(products_data).execute()
            inserted_products = prod_res.data or []
        except Exception as e:
            try:
                db.table("sites").delete().eq("id", site_id).execute()
            except Exception:
                pass
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to insert products: {str(e)}"
            )

    return {**created_site, "sections": inserted_sections, "products": inserted_products}


@router.put("/{site_id}", response_model=SiteDetailResponse)
async def update_site(
    site_id: UUID,
    payload: SiteUpdate,
    db: Client = Depends(get_request_scoped_db),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Update site metadata, sections, and products with rollbacks.
    """
    user_id = user["sub"]
    is_admin = is_user_admin(db, user_id)

    # 1. Verify existence and permission
    query = db.table("sites").select("*").eq("id", str(site_id))
    if not is_admin:
        query = query.eq("owner_id", user_id)

    try:
        check_res = query.execute()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed: {str(e)}"
        )

    if not check_res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Site not found or access denied."
        )

    original_site = check_res.data[0]

    # Gather updates
    update_data = {}
    for field in ["name", "slug", "status", "seo_title", "seo_description", "og_image_url", "brand_color"]:
        val = getattr(payload, field, None)
        if val is not None:
            update_data[field] = val

    # Fetch and backup existing sections
    old_sections_backup = []
    if payload.sections is not None:
        try:
            backup_res = db.table("site_sections").select("*").eq("site_id", str(site_id)).execute()
            old_sections_backup = backup_res.data or []
        except Exception as backup_err:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to backup existing sections before update: {str(backup_err)}"
            )

    # Fetch and backup existing products
    old_products_backup = []
    try:
        backup_products_res = db.table("products").select("*").eq("site_id", str(site_id)).execute()
        old_products_backup = backup_products_res.data or []
    except Exception as backup_err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to backup existing products before update: {str(backup_err)}"
        )

    # Verify cross-site product access
    if payload.products is not None:
        backup_product_ids = {UUID(p["id"]) if isinstance(p["id"], str) else p["id"] for p in old_products_backup}
        for prod in payload.products:
            if prod.id is not None:
                if prod.id not in backup_product_ids:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Product ID does not belong to this site."
                    )

    updated_site = original_site
    # Check if metadata, sections, or products is being updated
    if update_data or payload.sections is not None or payload.products is not None:
        if not update_data:
            update_data = {}
        update_data["updated_at"] = datetime.utcnow().isoformat()
        try:
            site_res = db.table("sites").update(update_data).eq("id", str(site_id)).execute()
            if not site_res.data:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to update site metadata: database returned no data."
                )
            updated_site = site_res.data[0]
        except Exception as e:
            err_msg = str(e).lower()
            if "duplicate" in err_msg or "23505" in err_msg or "unique" in err_msg:
                slug_val = update_data.get("slug") or payload.slug or original_site.get("slug")
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Slug '{slug_val}' is already in use."
                )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to update site metadata: {str(e)}"
            )

        try:
            # Update sections if provided
            if payload.sections is not None:
                db.table("site_sections").delete().eq("site_id", str(site_id)).execute()

                inserted_sections = []
                if payload.sections:
                    sections_data = [
                        {
                            "site_id": str(site_id),
                            "kind": sec.kind,
                            "position": sec.position,
                            "content": sec.content
                        }
                        for sec in payload.sections
                    ]
                    sec_res = db.table("site_sections").insert(sections_data).execute()
                    inserted_sections = sec_res.data or []
                    inserted_sections.sort(key=lambda s: s.get("position", 0))
                updated_site["sections"] = inserted_sections
            else:
                # Load existing
                sec_res = db.table("site_sections").select("*").eq("site_id", str(site_id)).order("position").execute()
                sections = sec_res.data or []
                sections.sort(key=lambda s: s.get("position", 0))
                updated_site["sections"] = sections

            # Update products if provided
            if payload.products is not None:
                payload_product_ids = {prod.id for prod in payload.products if prod.id is not None}
                
                # Delete products not in the payload
                ids_to_delete = [p["id"] for p in old_products_backup if (UUID(p["id"]) if isinstance(p["id"], str) else p["id"]) not in payload_product_ids]
                if ids_to_delete:
                    db.table("products").delete().in_("id", [str(i) for i in ids_to_delete]).execute()

                # Insert or update products
                for prod in payload.products:
                    if prod.id is None:
                        prod_data = {
                            "site_id": str(site_id),
                            "name": prod.name,
                            "description": prod.description,
                            "price_label": prod.price_label,
                            "payment_link_url": prod.payment_link_url,
                            "active": prod.active if prod.active is not None else True,
                        }
                        db.table("products").insert(prod_data).execute()
                    else:
                        prod_data = {}
                        for field in ["name", "description", "price_label", "payment_link_url", "active"]:
                            val = getattr(prod, field, None)
                            if val is not None:
                                prod_data[field] = val
                        prod_data["updated_at"] = datetime.utcnow().isoformat()
                        db.table("products").update(prod_data).eq("id", str(prod.id)).execute()

                # Fetch updated products
                products_res = db.table("products").select("*").eq("site_id", str(site_id)).execute()
                updated_site["products"] = products_res.data or []
            else:
                # Load existing
                products_res = db.table("products").select("*").eq("site_id", str(site_id)).execute()
                updated_site["products"] = products_res.data or []

        except Exception as e:
            # Revert metadata
            metadata_rollback_error = None
            if update_data:
                try:
                    revert_data = {
                        field: original_site.get(field)
                        for field in update_data
                    }
                    db.table("sites").update(revert_data).eq("id", str(site_id)).execute()
                except Exception as rollback_meta_err:
                    metadata_rollback_error = rollback_meta_err

            # Revert sections
            try:
                db.table("site_sections").delete().eq("site_id", str(site_id)).execute()
            except Exception:
                pass

            sections_restore_error = None
            if old_sections_backup:
                try:
                    restore_data = [
                        {
                            "id": str(sec["id"]),
                            "site_id": str(sec["site_id"]),
                            "kind": sec["kind"],
                            "position": sec["position"],
                            "content": sec["content"]
                        }
                        for sec in old_sections_backup
                    ]
                    for i, sec in enumerate(old_sections_backup):
                        if "created_at" in sec:
                            restore_data[i]["created_at"] = sec["created_at"]
                        if "updated_at" in sec:
                            restore_data[i]["updated_at"] = sec["updated_at"]

                    db.table("site_sections").insert(restore_data).execute()
                except Exception as restore_err:
                    sections_restore_error = restore_err

            # Revert products
            try:
                db.table("products").delete().eq("site_id", str(site_id)).execute()
            except Exception:
                pass

            products_restore_error = None
            if old_products_backup:
                try:
                    restore_prod_data = [
                        {
                            "id": str(p["id"]),
                            "site_id": str(p["site_id"]),
                            "name": p["name"],
                            "description": p["description"],
                            "price_label": p["price_label"],
                            "payment_link_url": p["payment_link_url"],
                            "active": p["active"],
                            "created_at": p["created_at"],
                            "updated_at": p["updated_at"]
                        }
                        for p in old_products_backup
                    ]
                    db.table("products").insert(restore_prod_data).execute()
                except Exception as restore_prod_err:
                    products_restore_error = restore_prod_err

            if metadata_rollback_error or sections_restore_error or products_restore_error:
                detail_msg = f"Failed to perform update: {str(e)}."
                if metadata_rollback_error:
                    detail_msg += f" CRITICAL: Failed to rollback site metadata changes: {str(metadata_rollback_error)}."
                if sections_restore_error:
                    detail_msg += f" CRITICAL: Failed to restore backup sections: {str(sections_restore_error)}."
                if products_restore_error:
                    detail_msg += f" CRITICAL: Failed to restore backup products: {str(products_restore_error)}."
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=detail_msg
                )

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to perform update: {str(e)}"
            )
    else:
        # Load sections and products
        try:
            sec_res = db.table("site_sections").select("*").eq("site_id", str(site_id)).order("position").execute()
            sections = sec_res.data or []
            sections.sort(key=lambda s: s.get("position", 0))
            updated_site["sections"] = sections

            products_res = db.table("products").select("*").eq("site_id", str(site_id)).execute()
            updated_site["products"] = products_res.data or []
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch sections or products: {str(e)}"
            )

    return updated_site


@router.delete("/{site_id}", status_code=status.HTTP_200_OK)
async def delete_site(
    site_id: UUID,
    db: Client = Depends(get_request_scoped_db),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Delete a specific site.
    - Validates ownership or admin privileges first.
    - Cascade deletes site_sections and products automatically via DB foreign key rule.
    """
    user_id = user["sub"]
    is_admin = is_user_admin(db, user_id)

    query = db.table("sites").select("id").eq("id", str(site_id))
    if not is_admin:
        query = query.eq("owner_id", user_id)

    try:
        check_res = query.execute()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed: {str(e)}"
        )

    if not check_res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Site not found or access denied."
        )

    try:
        db.table("sites").delete().eq("id", str(site_id)).execute()
        return {"status": "deleted", "site_id": site_id}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete site: {str(e)}"
        )
