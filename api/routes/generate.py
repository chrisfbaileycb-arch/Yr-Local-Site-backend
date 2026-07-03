import json
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from supabase import Client

from api.auth import get_current_user, get_request_scoped_db
from api.routes.sites import SiteDetailResponse
from agents.orchestrator import create_generation_pipeline
from agents.site_generator import SiteGeneratorSchema

router = APIRouter(prefix="/sites", tags=["generation"])


class GenerateSiteRequest(BaseModel):
    prompt: Optional[str] = Field(None, min_length=10, max_length=2000, description="A clear description of the small business")
    description: Optional[str] = Field(None, min_length=10, max_length=2000, description="Detailed description of the business")

    @model_validator(mode="before")
    @classmethod
    def check_prompt_or_description(cls, data: Any) -> Any:
        if isinstance(data, dict):
            prompt = data.get("prompt")
            desc = data.get("description")
            if isinstance(prompt, str):
                prompt = prompt.strip()
                data["prompt"] = prompt
            if isinstance(desc, str):
                desc = desc.strip()
                data["description"] = desc
            if not prompt and not desc:
                raise ValueError("Either 'prompt' or 'description' must be provided as a non-empty string.")
            if prompt and not desc:
                data["description"] = prompt
            elif desc and not prompt:
                data["prompt"] = desc
        return data


@router.post("/generate", response_model=SiteDetailResponse)
async def generate_site(
    payload: GenerateSiteRequest,
    db: Client = Depends(get_request_scoped_db),
    user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Generate website content and structure using the sequential SiteGenerator agent pipeline.
    Requires authentication.
    """
    prompt_text = payload.prompt or payload.description
    try:
        pipeline = create_generation_pipeline()
        async with pipeline as active_pipeline:
            response = await active_pipeline.chat(prompt_text)
            structured_data = await response.structured_output()

            if not structured_data:
                # Fallback to parsing raw text response as JSON
                raw_text = await response.text()
                try:
                    structured_data = json.loads(raw_text)
                except Exception:
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail="AI agent failed to generate a valid structured site schema."
                    )

            # Extract generated data
            data_dict = {}
            if hasattr(structured_data, "model_dump"):
                data_dict = structured_data.model_dump()
            elif hasattr(structured_data, "dict"):
                data_dict = structured_data.dict()
            elif isinstance(structured_data, dict):
                data_dict = structured_data
            else:
                data_dict = dict(structured_data)

            owner_id = user["sub"]
            site_data = {
                "owner_id": owner_id,
                "name": data_dict.get("name"),
                "slug": data_dict.get("slug"),
                "status": "draft",
                "seo_title": data_dict.get("seoTitle") or data_dict.get("seo_title"),
                "seo_description": data_dict.get("seoDescription") or data_dict.get("seo_description"),
                "brand_color": data_dict.get("brandColor") or data_dict.get("brand_color"),
            }

            try:
                site_res = db.table("sites").insert(site_data).execute()
            except Exception as e:
                err_msg = str(e).lower()
                if "duplicate" in err_msg or "23505" in err_msg or "unique" in err_msg:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Slug '{site_data['slug']}' is already in use."
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

            # Process and insert sections
            sections_list = data_dict.get("sections") or []
            sections_data = []
            for i, sec in enumerate(sections_list):
                if hasattr(sec, "model_dump"):
                    sec_dict = sec.model_dump()
                elif hasattr(sec, "dict"):
                    sec_dict = sec.dict()
                elif isinstance(sec, dict):
                    sec_dict = sec
                else:
                    sec_dict = dict(sec)

                kind = sec_dict.get("kind")
                content = {k: v for k, v in sec_dict.items() if k != "kind"}
                sections_data.append({
                    "site_id": site_id,
                    "kind": kind,
                    "position": i,
                    "content": content
                })

            inserted_sections = []
            if sections_data:
                try:
                    sec_res = db.table("site_sections").insert(sections_data).execute()
                    inserted_sections = sec_res.data or []
                    inserted_sections.sort(key=lambda s: s.get("position", 0))
                except Exception as e:
                    # rollback
                    try:
                        db.table("sites").delete().eq("id", site_id).execute()
                    except Exception:
                        pass
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Failed to insert site sections: {str(e)}"
                    )

            return {**created_site, "sections": inserted_sections}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI generation pipeline error: {str(e)}"
        )
