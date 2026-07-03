import asyncio
import json
import logging
import urllib.request
import urllib.error
from uuid import UUID
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from supabase import Client

from db.client import get_db
from api.auth import get_current_user, get_request_scoped_db, get_optional_user
from api.routes.sites import is_user_admin
from agents.orchestrator import create_audit_pipeline

router = APIRouter(prefix="/sites", tags=["audit"])
logger = logging.getLogger(__name__)

# --- Pydantic Schemas ---

class AuditRequest(BaseModel):
    url: str = Field(..., description="The URL of the website to audit")
    siteId: Optional[str] = Field(None, alias="siteId", description="Optional site ID to associate with the audit")

    class Config:
        populate_by_name = True


class AuditFinding(BaseModel):
    category: Literal['seo', 'accessibility', 'performance', 'copy', 'trust']
    severity: Literal['critical', 'high', 'medium', 'low']
    title: str = Field(..., max_length=60)
    description: str = Field(..., max_length=200)
    suggestedFix: str = Field(..., max_length=300, alias="suggestedFix")

    class Config:
        populate_by_name = True


class AuditResultResponse(BaseModel):
    id: str = ""
    targetUrl: str = Field(..., alias="targetUrl")
    siteId: Optional[str] = Field(None, alias="siteId")
    score: int
    status: Literal['complete', 'failed'] = 'complete'
    findings: List[AuditFinding]
    createdAt: str = Field(..., alias="createdAt")
    completedAt: str = Field(..., alias="completedAt")

    class Config:
        populate_by_name = True


# --- Helper Functions ---

def fetch_html_sync(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        raise ValueError("url must be a valid http(s) URL")
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ExpoProxyAI-Auditor/1.0 (+https://expoproxy.ai)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    with urllib.request.urlopen(req, timeout=10.0) as response:
        return response.read().decode("utf-8")


def map_agent_findings(findings: List[Any]) -> List[AuditFinding]:
    category_map = {
        "seo": "seo",
        "accessibility": "accessibility",
        "performance": "performance",
        "content": "copy",
        "copy": "copy",
        "trust": "trust"
    }
    severity_map = {
        "critical": "critical",
        "warning": "medium",
        "info": "low",
        "high": "high",
        "medium": "medium",
        "low": "low"
    }

    mapped = []
    for f in findings:
        if hasattr(f, "model_dump"):
            f_dict = f.model_dump()
        elif isinstance(f, dict):
            f_dict = f
        else:
            try:
                f_dict = dict(f)
            except Exception:
                continue

        raw_cat = str(f_dict.get("category", "seo")).lower()
        cat = category_map.get(raw_cat, "seo")

        raw_sev = str(f_dict.get("severity", "low")).lower()
        sev = severity_map.get(raw_sev, "low")

        title = str(f_dict.get("title", "Audit Finding"))[:60]
        description = str(f_dict.get("description", ""))[:200]
        suggested_fix = str(
            f_dict.get("suggestedFix") or 
            f_dict.get("suggested_fix") or 
            f_dict.get("description") or 
            f"Improve {cat} quality"
        )[:300]

        mapped.append(
            AuditFinding(
                category=cat,
                severity=sev,
                title=title,
                description=description,
                suggestedFix=suggested_fix
            )
        )
    return mapped


# --- Endpoints ---

@router.post("/audit", response_model=AuditResultResponse)
async def run_website_audit(
    payload: AuditRequest,
    user: Optional[Dict[str, Any]] = Depends(get_optional_user),
    db: Client = Depends(get_request_scoped_db),
    service_db: Client = Depends(get_db)
):
    """
    Fetches the HTML of the provided URL and runs a structured quality audit.
    Requires user authentication.
    """
    # 0. If siteId is provided, verify site ownership first.
    if payload.siteId:
        try:
            UUID(payload.siteId)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid UUID format for siteId."
            )
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required to associate audit with a site."
            )
        user_id = user["sub"]
        try:
            site_query = db.table("sites").select("owner_id").eq("id", payload.siteId).execute()
            if not site_query.data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Site not found."
                )
            site_owner_id = site_query.data[0]["owner_id"]
            
            roles_res = db.table("user_roles").select("role").eq("user_id", user_id).execute()
            is_admin = any(r.get("role") == "admin" for r in roles_res.data)
            
            if site_owner_id != user_id and not is_admin:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied to site."
                )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Database query failed during site validation: {str(e)}"
            )

    created_at = datetime.utcnow().isoformat() + "Z"

    # 1. Fetch HTML from URL asynchronously
    try:
        html_content = await asyncio.to_thread(fetch_html_sync, payload.url)
    except urllib.error.HTTPError as e:
        raise HTTPException(
            status_code=422,
            detail=f"HTTP Error {e.code}: {e.reason}"
        )
    except urllib.error.URLError as e:
        raise HTTPException(
            status_code=422,
            detail=f"URL Error: {str(e.reason)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=422,
            detail=f"Could not fetch URL: {str(e)}"
        )

    # 2. Call Sequential Agent Pipeline
    try:
        pipeline = create_audit_pipeline()
        async with pipeline as active_pipeline:
            response = await active_pipeline.chat(html_content)
            structured_data = await response.structured_output()

            if not structured_data:
                # Fallback to parsing raw text response as JSON
                raw_text = await response.text()
                try:
                    structured_data = json.loads(raw_text)
                except Exception:
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail="AI agent failed to generate a valid structured audit schema."
                    )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI audit pipeline execution failed: {str(e)}"
        )

    completed_at = datetime.utcnow().isoformat() + "Z"

    # 3. Reconcile and Map Schema Differences
    if hasattr(structured_data, "model_dump"):
        structured_data_dict = structured_data.model_dump()
    elif isinstance(structured_data, dict):
        structured_data_dict = structured_data
    else:
        structured_data_dict = dict(structured_data)

    raw_findings = structured_data_dict.get("findings", [])
    mapped_findings = map_agent_findings(raw_findings)

    # Calculate overall score based on the mapped finding severities
    score = 100
    for finding in mapped_findings:
        if finding.severity == "critical":
            score -= 25
        elif finding.severity == "high":
            score -= 15
        elif finding.severity == "medium":
            score -= 8
        elif finding.severity == "low":
            score -= 3
    score = max(0, score)

    # Save to database if user is authenticated
    audit_id = ""
    if user:
        findings_db = [f.model_dump() for f in mapped_findings]
        audit_row = {
            "site_id": payload.siteId if payload.siteId else None,
            "target_url": payload.url,
            "score": score,
            "status": "complete",
            "findings": findings_db,
            "created_at": created_at,
            "completed_at": completed_at
        }
        try:
            db_res = service_db.table("audit_results").insert(audit_row).execute()
            if db_res.data:
                audit_id = str(db_res.data[0]["id"])
        except Exception as db_err:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to persist audit results: {str(db_err)}"
            )

    return AuditResultResponse(
        id=audit_id,
        targetUrl=payload.url,
        siteId=payload.siteId,
        score=score,
        status="complete",
        findings=mapped_findings,
        createdAt=created_at,
        completedAt=completed_at
    )


# --- Endpoints for Audits list/retrieve ---

@router.get("/{site_id}/audits", response_model=List[AuditResultResponse])
async def list_site_audits(
    site_id: UUID,
    db: Client = Depends(get_request_scoped_db),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """
    List audits for a site.
    Requires site ownership or admin permissions.
    """
    user_id = user["sub"]
    try:
        site_query = db.table("sites").select("owner_id").eq("id", str(site_id)).execute()
        if not site_query.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Site not found."
            )
        site_owner_id = site_query.data[0]["owner_id"]
        
        roles_res = db.table("user_roles").select("role").eq("user_id", user_id).execute()
        is_admin = any(r.get("role") == "admin" for r in roles_res.data)
        
        if site_owner_id != user_id and not is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to site."
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed during site validation: {str(e)}"
        )

    try:
        res = db.table("audit_results").select("*").eq("site_id", str(site_id)).order("created_at", desc=True).execute()
        mapped_results = []
        for row in (res.data or []):
            mapped_results.append(AuditResultResponse(
                id=row["id"],
                targetUrl=row["target_url"],
                siteId=row["site_id"],
                score=row["score"],
                status=row["status"],
                findings=row["findings"],
                createdAt=row["created_at"],
                completedAt=row["completed_at"]
            ))
        return mapped_results
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve site audits: {str(e)}"
        )


@router.get("/audits/{audit_id}", response_model=AuditResultResponse)
async def get_audit_details(
    audit_id: UUID,
    db: Client = Depends(get_request_scoped_db),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Retrieve details of a specific audit result.
    Requires site ownership (if associated with a site) or admin permissions.
    """
    user_id = user["sub"]
    try:
        res = db.table("audit_results").select("*").eq("id", str(audit_id)).execute()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to query audit: {str(e)}"
        )

    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audit result not found."
        )

    row = res.data[0]
    site_id = row.get("site_id")

    # If associated with a site, verify ownership or admin status
    if site_id:
        try:
            site_query = db.table("sites").select("owner_id").eq("id", site_id).execute()
            if not site_query.data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Site associated with the audit not found."
                )
            site_owner_id = site_query.data[0]["owner_id"]
            
            roles_res = db.table("user_roles").select("role").eq("user_id", user_id).execute()
            is_admin = any(r.get("role") == "admin" for r in roles_res.data)
            
            if site_owner_id != user_id and not is_admin:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied to site audits."
                )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Database query failed during site validation: {str(e)}"
            )

    return AuditResultResponse(
        id=row["id"],
        targetUrl=row["target_url"],
        siteId=row["site_id"],
        score=row["score"],
        status=row["status"],
        findings=row["findings"],
        createdAt=row["created_at"],
        completedAt=row["completed_at"]
    )
