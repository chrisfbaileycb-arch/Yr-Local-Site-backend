import html
import logging
import os
from datetime import datetime
from typing import Dict, Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, status
from pydantic import BaseModel, Field
import httpx
from supabase import Client

from api.auth import get_request_scoped_db

router = APIRouter(prefix="/leads", tags=["leads"])
logger = logging.getLogger(__name__)

# --- Pydantic Schemas ---

class LeadCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    project_type: str = Field(..., alias="projectType", min_length=1)
    budget: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)

    class Config:
        populate_by_name = True


class LeadDetailResponse(BaseModel):
    id: UUID
    name: str
    email: str
    project_type: Optional[str] = Field(None, alias="projectType")
    budget: Optional[str] = None
    message: str
    created_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True


# --- Background Tasks ---

async def send_lead_email_notification(
    name: str,
    email: str,
    project_type: str,
    budget: str,
    message: str
):
    """
    Sends an email notification via Resend.
    Any errors are caught and logged silently so they do not disrupt the client request.
    """
    resend_api_key = os.getenv("RESEND_API_KEY")
    resend_from = os.getenv("RESEND_FROM_EMAIL")
    resend_to = os.getenv("RESEND_NOTIFY_EMAIL")

    if not resend_api_key or not resend_from or not resend_to:
        logger.warning("Resend configurations (RESEND_API_KEY, RESEND_FROM_EMAIL, or RESEND_NOTIFY_EMAIL) missing. Skipping email dispatch.")
        return

    # Escaping logic to prevent HTML injection
    escaped_name = html.escape(name)
    escaped_email = html.escape(email)
    escaped_project = html.escape(project_type)
    escaped_budget = html.escape(budget)
    escaped_msg = html.escape(message).replace("\n", "<br>")

    email_body = {
        "from": resend_from,
        "to": [resend_to],
        "subject": f"New Lead: {escaped_name} — Yr Local",
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

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.resend.com/emails",
                json=email_body,
                headers=email_headers,
                timeout=10.0
            )
            if not response.is_success:
                logger.error(f"Resend returned HTTP status {response.status_code}: {response.text}")
            else:
                logger.info("Lead email notification sent successfully via Resend.")
    except Exception as e:
        logger.error(f"Failed to dispatch lead notification email: {str(e)}")


# --- Endpoints ---

@router.post("", response_model=LeadDetailResponse, status_code=status.HTTP_201_CREATED)
async def submit_lead(
    payload: LeadCreate,
    background_tasks: BackgroundTasks,
    db: Client = Depends(get_request_scoped_db)
):
    """
    Public endpoint to submit a lead.
    Saves lead to database and sends email notification via Resend in the background.
    """
    lead_data = {
        "name": payload.name,
        "email": payload.email,
        "project_type": payload.project_type,
        "budget": payload.budget,
        "message": payload.message
    }

    try:
        res = db.table("leads").insert(lead_data).execute()
        if not res.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to submit lead: no record returned from database."
            )
        inserted_lead = res.data[0]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database insertion failed: {str(e)}"
        )

    # Dispatch email notification in background
    background_tasks.add_task(
        send_lead_email_notification,
        payload.name,
        payload.email,
        payload.project_type,
        payload.budget,
        payload.message
    )

    return LeadDetailResponse(
        id=inserted_lead["id"],
        name=inserted_lead["name"],
        email=inserted_lead["email"],
        projectType=inserted_lead.get("project_type"),
        budget=inserted_lead.get("budget"),
        message=inserted_lead["message"],
        created_at=inserted_lead["created_at"]
    )
