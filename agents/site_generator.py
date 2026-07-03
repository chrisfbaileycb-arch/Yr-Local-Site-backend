"""Generates structured site JSON matching the target schema using google.antigravity."""
import os
import contextvars
from typing import Type, Any, Optional, List, Literal
from google.antigravity import Agent, LocalAgentConfig
from pydantic import BaseModel, Field

class LlmAgent:
    """Wrapper class for google.antigravity.Agent and LocalAgentConfig."""
    def __init__(
        self,
        system_instructions: Optional[str] = None,
        response_schema: Optional[Type[BaseModel]] = None,
        model: Optional[str] = None,
        **kwargs
    ):
        config_args = {
            "system_instructions": system_instructions,
            "response_schema": response_schema,
            **kwargs
        }
        if model is not None:
            config_args["model"] = model
        self.config = LocalAgentConfig(**config_args)
        self._agent_var = contextvars.ContextVar(f"active_agent_{id(self)}", default=None)

    async def __aenter__(self):
        agent = Agent(config=self.config)
        await agent.__aenter__()
        self._agent_var.set(agent)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        agent = self._agent_var.get()
        if agent:
            await agent.__aexit__(exc_type, exc_val, exc_tb)
            self._agent_var.set(None)

    async def chat(self, prompt: str):
        agent = self._agent_var.get()
        if not agent:
            raise RuntimeError("LlmAgent must be used inside an 'async with' block.")
        return await agent.chat(prompt)

# --- Schema Definitions ---
class SiteSectionSchema(BaseModel):
    kind: Literal["hero", "about", "services", "contact"] = Field(description="The category of section: e.g. hero, about, services, contact")
    headline: str = Field(description="Section heading content")
    subheadline: Optional[str] = Field(default=None, description="Subheading content (mainly for hero)")
    body: Optional[str] = Field(default=None, description="Detailed paragraph content (for about/services/contact)")
    ctaLabel: Optional[str] = Field(default=None, description="Label for the call-to-action button")

class SiteGeneratorSchema(BaseModel):
    name: str = Field(description="The small business brand name")
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", description="Lowercase URL-safe slug representation")
    seoTitle: str = Field(max_length=60, description="SEO meta-title, maximum 60 characters")
    seoDescription: str = Field(max_length=155, description="SEO meta-description, maximum 155 characters")
    brandColor: str = Field(pattern=r"^oklch\(.*\)$", description="oklch brand color declaration")
    sections: List[SiteSectionSchema] = Field(description="Ordered list of layout sections")

# --- Prompt / System Instructions ---
SITE_GEN_INSTRUCTION = """
You are an expert website content generator for small businesses.
Given a business description, generate a complete website structure.

Return valid JSON conforming to the requested schema.
Ensure oklch colors are used for brandColor (e.g. "oklch(0.65 0.18 45)").
Include at least 4 sections: hero, about, services, and contact.
"""

def create_site_generator_agent() -> LlmAgent:
    """Creates a site generator LlmAgent with structured response schema."""
    return LlmAgent(
        system_instructions=SITE_GEN_INSTRUCTION,
        response_schema=SiteGeneratorSchema,
    )

