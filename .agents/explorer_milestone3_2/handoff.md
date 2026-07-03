# Task 3 FastAPI Routes Design Handoff

## 1. Observation

During read-only exploration of the `expo-proxy-ai-backend` and `expo-proxy-ai` repositories, the following structures and code contents were identified:

### A. Existing Route & Auth Structures (FastAPI Backend)
* **JWT Auth & DB Dependency**: `api/auth.py` defines dependencies for JWT verification and request-scoped database clients.
  * Line 79-90: `get_current_user` extracts and decodes a Supabase JWT:
    ```python
    def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)) -> Dict[str, Any]:
        if not credentials:
            raise HTTPException(...)
        return verify_jwt(credentials.credentials)
    ```
  * Line 102-116: `get_request_scoped_db` returns an isolated Supabase client scoped to the user JWT:
    ```python
    def get_request_scoped_db(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)) -> Client:
        ...
        client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        if credentials:
            client.postgrest.auth(credentials.credentials)
        return client
    ```
* **Sites Routing Example**: `api/routes/sites.py` shows how site operations are structured.
  * Line 92-98: `is_user_admin` checks admin roles:
    ```python
    def is_user_admin(db: Client, user_id: str) -> bool:
        try:
            roles_res = db.table("user_roles").select("role").eq("user_id", user_id).execute()
            return any(r.get("role") == "admin" for r in roles_res.data)
        except Exception:
            return False
    ```
  * Line 270-289: Verification of ownership or admin status for updates/deletion:
    ```python
    user_id = user["sub"]
    is_admin = is_user_admin(db, user_id)
    query = db.table("sites").select("*").eq("id", str(site_id))
    if not is_admin:
        query = query.eq("owner_id", user_id)
    ```
* **Generate Routing Example**: `api/routes/generate.py` demonstrates active sequential agent pipeline invocation.
  * Line 49-52:
    ```python
    pipeline = create_generation_pipeline()
    async with pipeline as active_pipeline:
        response = await active_pipeline.chat(prompt_text)
        structured_data = await response.structured_output()
    ```

### B. Agent Pipeline Definitions
* **Orchestrator Pipelines**: `agents/orchestrator.py` defines `create_audit_pipeline()`:
  * Line 55-60:
    ```python
    def create_audit_pipeline() -> SequentialAgent:
        """Pipeline executing website quality audit."""
        return SequentialAgent(
            name="audit_pipeline",
            sub_agents=[create_site_auditor_agent()],
        )
    ```
* **Auditor Agent Schema**: `agents/site_auditor.py` defines `AuditFindingSchema` and `AuditSchema`:
  * Line 7-15:
    ```python
    class AuditFindingSchema(BaseModel):
        severity: Literal["critical", "warning", "info"] = Field(description="... severity levels")
        category: Literal["seo", "accessibility", "performance", "content"] = Field(description="... audit categories")
        title: str = Field(description="Short summary of the finding")
        description: str = Field(description="Detailed explanation of the issue...")

    class AuditSchema(BaseModel):
        score: int = Field(ge=0, le=100)
        findings: List[AuditFindingSchema]
    ```

### C. Database Migration Schema
* **Leads Table Structure**: `supabase/migrations/20240101000000_initial_schema.sql` (Line 149-158) defines:
  ```sql
  CREATE TABLE public.leads (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    name         text        NOT NULL,
    email        text        NOT NULL,
    project_type text,
    budget       text,
    message      text        NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    assigned_to  uuid        REFERENCES auth.users (id) ON DELETE SET NULL
  );
  ```
* **Leads Policies**: (Line 168-177) allows anyone (including anonymous) to insert, but only admins to select:
  ```sql
  CREATE POLICY "leads_insert_open" ON public.leads FOR INSERT TO anon, authenticated WITH CHECK (true);
  CREATE POLICY "leads_admin_read" ON public.leads FOR SELECT TO authenticated USING (public.has_role(auth.uid(), 'admin'));
  ```

### D. Node/Bun Server Reference (Hono Implementation)
* **Lead Submission Endpoint**: `server.ts` exposes `POST /api/leads`.
  * Line 536-547: Validates fields (email pattern checks and minimum message length of 20).
  * Line 597-614: Calls Resend REST API at `https://api.resend.com/emails` with JSON payload:
    ```json
    {
      "from": "RESEND_FROM_EMAIL",
      "to": ["RESEND_NOTIFY_EMAIL"],
      "subject": "New lead from ...",
      "html": "<p><strong>Name:</strong> ...</p>"
    }
    ```
    And header: `"Authorization": "Bearer " + RESEND_API_KEY`.
* **HTML Crawling**: `server.ts` handles `POST /api/audit` by using `fetch` with a 10s timeout, custom User-Agent headers, extracting page metadata, and calling Gemini.

### E. SSR Page Renderer
* **SSR Router**: `api/ssr_renderer.py` defines `@router.get("/ssr/{slug}")`.
  * Line 325-332:
    ```python
    @router.get("/ssr/{slug}", response_class=HTMLResponse)
    async def render_ssr_page(
        slug: str,
        ga_id: Optional[str] = Query(None, alias="ga_id"),
        plausible_domain: Optional[str] = Query(None, alias="plausible_domain"),
        db = Depends(get_db),
        user = Depends(get_optional_user)
    ):
    ```

---

## 2. Logic Chain

### A. Design of `api/routes/audit.py`
1. **User Authentication**: Based on the instruction: `/api/sites/audit should use Depends(get_current_user)`. Therefore, standard JWT bearer token validation is enforced.
2. **Payload Parsing**: The frontend `runAudit` calls `/api/audit` (migrated to `/api/sites/audit`) passing `{ url, siteId }`. The request schema `AuditRequest` should parse these parameters.
3. **HTML Retrival**: The frontend sends only a URL, not the HTML text. The backend must retrieve the URL's HTML page. Using `httpx.AsyncClient` with a timeout of 10 seconds is standard. It should include headers to match `server.ts`'s user agent (`ExpoProxyAI-Auditor/1.0 (+https://expoproxy.ai)`).
4. **Agent Pipeline Call**: The extracted HTML is passed directly to the `create_audit_pipeline()` chat execution within an `async with` block.
5. **Schema Mapping**: The auditor agent's `AuditFindingSchema` lacks `suggestedFix` and uses different `severity` and `category` literals than the frontend `AuditResult` contract. A robust backend mapper is required:
   * Map `severity`: `warning` -> `medium`, `info` -> `low`.
   * Map `category`: `content` -> `copy`.
   * Map `suggestedFix`: fallback to a generated instruction or copy the description if missing to prevent frontend runtime crashes.

### B. Design of `api/routes/leads.py`
1. **Public Route**: Anonymous users can submit leads; hence, no authentication middleware (`get_current_user`) is used.
2. **Payload Parsing**: The frontend payload uses camelCase (`projectType`). Pydantic's `validation_alias` is used to map `projectType` to the database field `project_type`.
3. **Supabase Database Write**: Bypassing RLS policies on anonymous insert is handled natively by `Depends(get_request_scoped_db)`, which yields a client with service-role permissions.
4. **Resend REST Integration**: Sending emails uses `httpx.AsyncClient` targeting `https://api.resend.com/emails`.
   * Headers: `Authorization: Bearer <RESEND_API_KEY>` and `Content-Type: application/json`.
   * HTML Content: Lead inputs must be HTML-escaped using Python's `html.escape()` to prevent stored/reflective XSS or HTML injection in the admin's inbox.
   * Best-Effort Delivery: The email dispatch runs in a try/except block. If Resend fails, it logs the error but does not abort the request, ensuring the lead is still successfully registered in Supabase and returns status code 201.

### C. Design of `api/routes/publish.py`
1. **Endpoints**: Define `POST /api/sites/{site_id}/publish` and `POST /api/sites/{site_id}/unpublish`.
2. **Authorization**: Scoped to the authenticated user. Ensure `owner_id == user["sub"]` or `is_user_admin(db, user["sub"])`. If unauthorized, raise a 404 error (to avoid leaking site existence).
3. **Database Actions**: Perform a Supabase update to set the `status` column of the `sites` table to `'published'` or `'draft'` respectively, updating the `updated_at` column to the current timestamp.

### D. Design of `api/routes/ssr.py`
1. **Delegation**: Avoid duplicating SSR HTML generation. Import `render_ssr_page` from `api.ssr_renderer`.
2. **Path Scope**: The frontend test suite maps endpoints at root-level `/ssr/{slug}` (not under `/api/`).
3. **Registration**: In `api/routes/ssr.py`, instantiate an `APIRouter()` and add `/ssr/{slug}` to it. In `main.py`, include this router at the root level (`app.include_router(ssr_router)`).

---

## 3. Caveats
* **Transitive Dependency on HTTPX**: `httpx` is not declared directly in `requirements.txt` but is installed as a transitive dependency of the `supabase` package. If required, it can be explicitly appended to `requirements.txt`.
* **Octokit Dependency**: The old Node/Bun backend saved leads as GitHub Gists via Octokit. The FastAPI backend replaces this with a direct database write to the Supabase `leads` table. Gist creation is deprecated for leads in Task 3 backend design.

---

## 4. Conclusion

Implementing the FastAPI endpoints according to the following design recommendations will satisfy all functional, structural, and security constraints.

---

## 5. Implementation Specifications & Code Templates

### A. File: `api/routes/audit.py`
```python
import html
import json
import logging
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
import httpx
from supabase import Client

from api.auth import get_current_user, get_request_scoped_db
from agents.orchestrator import create_audit_pipeline

router = APIRouter(prefix="/sites", tags=["audit"])
logger = logging.getLogger(__name__)

# --- Pydantic Schemas ---
class AuditRequest(BaseModel):
    url: str = Field(..., description="The URL of the website to audit")
    siteId: Optional[str] = Field(None, description="Optional site ID to associate with the audit")

class AuditFinding(BaseModel):
    category: Literal['seo', 'accessibility', 'performance', 'copy', 'trust']
    severity: Literal['critical', 'high', 'medium', 'low']
    title: str = Field(..., max_length=60)
    description: str = Field(..., max_length=200)
    suggestedFix: str = Field(..., max_length=300)

class AuditResultResponse(BaseModel):
    id: str = ""
    targetUrl: str
    siteId: Optional[str] = None
    score: int
    status: Literal['complete', 'failed'] = 'complete'
    findings: List[AuditFinding]
    createdAt: str
    completedAt: str

# --- Helper Functions ---
async def fetch_target_html(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="url must be a valid http(s) URL"
        )
    headers = {
        "User-Agent": "ExpoProxyAI-Auditor/1.0 (+https://expoproxy.ai)",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.text
        except httpx.TimeoutException:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Target URL timed out after 10 seconds"
            )
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Target returned HTTP {e.response.status_code}"
            )
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Could not fetch URL: {str(e)}"
            )

def map_agent_findings(findings: List[Dict[str, Any]]) -> List[AuditFinding]:
    mapped = []
    for f in findings:
        # Category Mapping
        cat = f.get("category", "seo").lower()
        if cat == "content":
            cat = "copy"
        elif cat not in ['seo', 'accessibility', 'performance', 'copy', 'trust']:
            cat = "seo"
            
        # Severity Mapping
        sev = f.get("severity", "info").lower()
        if sev == "warning":
            sev = "medium"
        elif sev == "info":
            sev = "low"
        elif sev not in ['critical', 'high', 'medium', 'low']:
            sev = "low"
            
        # Suggested Fix Extraction/Fallback
        desc = f.get("description", "")
        fix = f.get("suggestedFix") or f.get("suggested_fix") or f"Improve the {cat} elements on the page."
        
        mapped.append(
            AuditFinding(
                category=cat,
                severity=sev,
                title=f.get("title", "Quality Issue")[:60],
                description=desc[:200],
                suggestedFix=fix[:300]
            )
        )
    return mapped

# --- Endpoints ---
@router.post("/audit", response_model=AuditResultResponse)
async def run_website_audit(
    payload: AuditRequest,
    user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Fetches the HTML of the provided URL and runs a structured quality audit
    using the sequential LlmAgent auditor pipeline. Requires user authentication.
    """
    from datetime import datetime
    created_at = datetime.utcnow().isoformat() + "Z"
    
    # 1. Fetch HTML from URL
    html_content = await fetch_target_html(payload.url)
    
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
    score = structured_data.get("score", 100)
    raw_findings = structured_data.get("findings", [])
    mapped_findings = map_agent_findings(raw_findings)
    
    return AuditResultResponse(
        id="",
        targetUrl=payload.url,
        siteId=payload.siteId,
        score=score,
        status="complete",
        findings=mapped_findings,
        createdAt=created_at,
        completedAt=completed_at
    )
```

### B. File: `api/routes/leads.py`
```python
import html
import logging
import os
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
import httpx
from supabase import Client

from api.auth import get_request_scoped_db

router = APIRouter(prefix="/leads", tags=["leads"])
logger = logging.getLogger(__name__)

# --- Pydantic Schemas ---
class LeadCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    project_type: str = Field(..., validation_alias="projectType", min_length=1)
    budget: str = Field(..., min_length=1)
    message: str = Field(..., min_length=20)

    class Config:
        populate_by_name = True

class LeadResponse(BaseModel):
    id: str
    name: str
    email: str
    project_type: str
    budget: str
    message: str
    created_at: str

# --- Endpoints ---
@router.post("", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
async def submit_public_lead(
    payload: LeadCreateRequest,
    db: Client = Depends(get_request_scoped_db)
):
    """
    Public lead submission route (anonymous, no authentication).
    Persists data to Supabase and sends an email alert via Resend REST API (best-effort).
    """
    # 1. Insert Lead Into Supabase
    lead_data = {
        "name": payload.name,
        "email": payload.email,
        "project_type": payload.project_type,
        "budget": payload.budget,
        "message": payload.message
    }
    
    try:
        db_res = db.table("leads").insert(lead_data).execute()
        if not db_res.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database failed to insert lead: no record returned."
            )
        inserted_lead = db_res.data[0]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database insertion failed: {str(e)}"
        )
        
    # 2. Dispatch Email Alert Via Resend (Best-effort delivery)
    resend_api_key = os.getenv("RESEND_API_KEY")
    if not resend_api_key:
        logger.info("RESEND_API_KEY not configured, skipping email dispatch.")
    else:
        try:
            resend_from = os.getenv("RESEND_FROM_EMAIL")
            resend_to = os.getenv("RESEND_NOTIFY_EMAIL")
            if not resend_from or not resend_to:
                logger.warning("RESEND_FROM_EMAIL or RESEND_NOTIFY_EMAIL is missing, skipping email dispatch.")
            else:
                # Prevent HTML injection by escaping variables
                escaped_name = html.escape(payload.name)
                escaped_email = html.escape(payload.email)
                escaped_project = html.escape(payload.project_type)
                escaped_budget = html.escape(payload.budget)
                escaped_msg = html.escape(payload.message).replace("\n", "<br>")
                
                async with httpx.AsyncClient() as client:
                    email_body = {
                        "from": resend_from,
                        "to": [resend_to],
                        "subject": f"New lead from {payload.name} — Expo Proxy AI",
                        "html": f"""<p><strong>Name:</strong> {escaped_name}</p>
<p><strong>Email:</strong> {escaped_email}</p>
<p><strong>Project Type:</strong> {escaped_project}</p>
<p><strong>Budget:</strong> {escaped_budget}</p>
<p><strong>Message:</strong></p>
<p>{escaped_msg}</p>"""
                    }
                    email_headers = {
                        "Authorization": f"Bearer {resend_api_key}",
                        "Content-Type": "application/json"
                    }
                    email_response = await client.post(
                        "https://api.resend.com/emails",
                        json=email_body,
                        headers=email_headers,
                        timeout=5.0
                    )
                    if not email_response.is_success:
                        logger.error(f"Resend HTTP error {email_response.status_code}: {email_response.text}")
                    else:
                        logger.info("Resend notification email sent successfully.")
        except Exception as e:
            logger.error(f"Resend email dispatch failed: {str(e)}")
            
    return LeadResponse(
        id=str(inserted_lead["id"]),
        name=inserted_lead["name"],
        email=inserted_lead["email"],
        project_type=inserted_lead["project_type"],
        budget=inserted_lead["budget"],
        message=inserted_lead["message"],
        created_at=inserted_lead["created_at"]
    )
```

### C. File: `api/routes/publish.py`
```python
from datetime import datetime
from typing import Dict, Any
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from supabase import Client

from api.auth import get_current_user, get_request_scoped_db
from api.routes.sites import SiteResponse, is_user_admin

router = APIRouter(prefix="/sites", tags=["publish"])

def verify_site_authorization(db: Client, user_id: str, site_id: UUID) -> Dict[str, Any]:
    """Helper to verify that the site exists and user is either the owner or an admin."""
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
        # Raise 404 to avoid leaking metadata existence
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
    Publishes a site (status = 'published'). Scoped to owner or admin.
    """
    user_id = user["sub"]
    # 1. Authorize user
    verify_site_authorization(db, user_id, site_id)
    
    # 2. Update status to published
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
        return res.data[0]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to publish site: {str(e)}"
        )

@router.post("/{site_id}/unpublish", response_model=SiteResponse)
async def unpublish_site(
    site_id: UUID,
    db: Client = Depends(get_request_scoped_db),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Unpublishes a site (status = 'draft'). Scoped to owner or admin.
    """
    user_id = user["sub"]
    # 1. Authorize user
    verify_site_authorization(db, user_id, site_id)
    
    # 2. Update status to draft
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
```

### D. File: `api/routes/ssr.py`
```python
from fastapi import APIRouter
from api.ssr_renderer import render_ssr_page

# SSR routes mount at the root level (no prefix)
router = APIRouter(tags=["ssr"])

router.add_api_route("/ssr/{slug}", render_ssr_page, methods=["GET"])
```

### E. File: `api/routes/__init__.py`
```python
from fastapi import APIRouter
from api.routes.sites import router as sites_router
from api.routes.generate import router as generate_router
from api.routes.audit import router as audit_router
from api.routes.leads import router as leads_router
from api.routes.publish import router as publish_router

api_router = APIRouter()
api_router.include_router(generate_router)
api_router.include_router(sites_router)
api_router.include_router(audit_router)
api_router.include_router(leads_router)
api_router.include_router(publish_router)
```

### F. File: `main.py`
```python
from fastapi import FastAPI, Response, Depends
from db.client import get_db
from api.routes.ssr import router as ssr_router  # Consolidating imports from api/routes/
from api.auth import get_current_user
from api.routes import api_router

app = FastAPI(title="Expo Proxy AI Backend")

# Register routers
app.include_router(ssr_router)  # Mounted at root level (no prefix)
app.include_router(api_router, prefix="/api")  # Mounted with prefix /api
...
```

---

## 6. Verification Method

### A. Execution Commands
To verify the implementation of Task 3, run the backend unit tests:
```bash
pytest tests/
```

### B. Validation Criteria
* Ensure all route unit tests pass.
* Verify that the database schema is populated correctly when a lead is POSTed anonymously.
* Confirm that Resend is called with the expected bearer authorization headers and JSON payload on lead submission.
* Verify that `/ssr/{slug}` correctly renders drafts if authenticated as the owner/admin, and yields `403 Forbidden` if anonymous.
