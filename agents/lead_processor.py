"""Summarizes and prioritizes incoming lead forms using google.antigravity."""
from pydantic import BaseModel, Field
from .site_generator import LlmAgent

# --- Schema Definitions ---
class LeadProcessorSchema(BaseModel):
    summary: str = Field(description="A concise one-sentence summary of the lead request")
    priority: int = Field(ge=1, le=5, description="Priority rating from 1 (lowest) to 5 (highest)")

# --- Prompt / System Instructions ---
LEAD_INSTRUCTION = """
You are an expert sales representative. Analyze the incoming lead form submission.
Provide a concise, single-sentence summary and rate the priority from 1 (lowest) to 5 (highest).
High priority should be assigned to detailed messages, high budgets, and clear business goals.
"""

def create_lead_processor_agent() -> LlmAgent:
    """Creates a lead processor LlmAgent with structured priority schema."""
    return LlmAgent(
        system_instructions=LEAD_INSTRUCTION,
        response_schema=LeadProcessorSchema,
    )
