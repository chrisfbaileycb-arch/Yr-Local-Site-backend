"""Audits website HTML and returns a structured quality report using google.antigravity."""
from typing import List, Literal
from pydantic import BaseModel, Field
from .site_generator import LlmAgent

# --- Schema Definitions ---
class AuditFindingSchema(BaseModel):
    severity: Literal["critical", "warning", "info"] = Field(description="Severity levels: 'critical', 'warning', or 'info'")
    category: Literal["seo", "accessibility", "performance", "content"] = Field(description="Audit categories: 'seo', 'accessibility', 'performance', or 'content'")
    title: str = Field(description="Short summary of the finding")
    description: str = Field(description="Detailed explanation of the issue and how to resolve it")

class AuditSchema(BaseModel):
    score: int = Field(ge=0, le=100, description="Overall website health score from 0 to 100")
    findings: List[AuditFindingSchema] = Field(description="List of audit findings discovered")

# --- Prompt / System Instructions ---
AUDIT_INSTRUCTION = """
You are a website quality auditor. Analyze the provided HTML and return a structured JSON audit.

You must check for:
- SEO: title, meta descriptions, h1 headings, link descriptive texts
- Accessibility: alt tags on images, ARIA labels, semantic landmark usage
- Performance hints: image sizes, render-blocking scripts
- Content quality: spelling, coherence, appropriate text lengths

Conform strictly to the response schema.
"""

def create_site_auditor_agent() -> LlmAgent:
    """Creates a site auditor LlmAgent with structured audit report schema."""
    return LlmAgent(
        system_instructions=AUDIT_INSTRUCTION,
        response_schema=AuditSchema,
    )
