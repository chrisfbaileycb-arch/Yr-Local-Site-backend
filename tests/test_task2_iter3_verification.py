import json
import pytest
from uuid import UUID, uuid4
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from jose import jwt

import main
from api.auth import get_request_scoped_db

client = TestClient(main.app)
TEST_SECRET = "test_jwt_secret_key"


def create_token(user_id="b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11", email="user@example.com", include_sub=True):
    payload = {
        "aud": "authenticated",
        "email": email
    }
    if include_sub:
        payload["sub"] = user_id
    return jwt.encode(payload, TEST_SECRET, algorithm="HS256")


@pytest.fixture
def mock_db_setup():
    mock_db = MagicMock()

    def make_query_mock():
        m = MagicMock()
        m.select.return_value = m
        m.eq.return_value = m
        m.order.return_value = m
        m.insert.return_value = m
        m.update.return_value = m
        m.delete.return_value = m
        return m

    mock_user_roles = make_query_mock()
    mock_sites = make_query_mock()
    mock_site_sections = make_query_mock()

    # Default: user is not an admin
    mock_user_roles.execute.return_value = MagicMock(data=[])

    def table_router(table_name):
        if table_name == "user_roles":
            return mock_user_roles
        elif table_name == "sites":
            return mock_sites
        elif table_name == "site_sections":
            return mock_site_sections
        return MagicMock()

    mock_db.table.side_effect = table_router

    # Register dependency override in FastAPI
    main.app.dependency_overrides[get_request_scoped_db] = lambda: mock_db
    yield mock_db, mock_user_roles, mock_sites, mock_site_sections
    # Clean up dependency override
    main.app.dependency_overrides.pop(get_request_scoped_db, None)


# --- VERIFICATION TEST CASES FOR TASK 2 ITERATION 3 ---

# 1. Missing JWT subject claim 'sub' is rejected with HTTP 401.
@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_missing_jwt_sub_rejected(mock_secret):
    """
    Verify that a JWT lacking the 'sub' claim is rejected with HTTP 401.
    """
    # Create token with NO 'sub' claim
    token = create_token(include_sub=False)
    
    response = client.get("/api/sites", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert "missing subject" in response.json()["detail"].lower()


# 2. Prompts/descriptions > 2000 characters in generate endpoint are rejected with HTTP 422.
@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
@patch("api.routes.generate.create_generation_pipeline")
def test_generate_endpoint_length_limits(mock_create_pipeline, mock_secret, mock_db_setup):
    """
    Verify that prompts or descriptions exceeding 2000 characters are rejected with HTTP 422.
    """
    mock_db, mock_user_roles, mock_sites, mock_site_sections = mock_db_setup
    token = create_token()

    # Case A: Prompt > 2000 characters
    long_prompt = "a" * 2001
    payload_long_prompt = {"prompt": long_prompt}
    response = client.post(
        "/api/sites/generate",
        json=payload_long_prompt,
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 422
    assert "string_too_long" in str(response.json()) or "2000" in str(response.json())

    # Case B: Description > 2000 characters
    long_description = "b" * 2001
    payload_long_desc = {"description": long_description}
    response = client.post(
        "/api/sites/generate",
        json=payload_long_desc,
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 422
    assert "string_too_long" in str(response.json()) or "2000" in str(response.json())

    # Case C: Valid length (<= 2000 characters) - should pass validation
    # Mock pipeline so it doesn't actually run AI generation
    class MockPipeline:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        async def chat(self, prompt: str):
            mock_resp = MagicMock()
            async def mock_structured_output():
                return {
                    "name": "Generated Site",
                    "slug": "generated-site",
                    "seoTitle": "Generated Site Title",
                    "seoDescription": "Generated Site Description",
                    "brandColor": "oklch(0.65 0.18 45)",
                    "sections": []
                }
            mock_resp.structured_output = mock_structured_output
            return mock_resp

    mock_sites.execute.side_effect = [
        MagicMock(data=[]), # select check
        MagicMock(data=[{
            "id": "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
            "owner_id": "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
            "name": "Generated Site",
            "slug": "generated-site",
            "status": "draft",
            "seo_title": "Generated Site Title",
            "seo_description": "Generated Site Description",
            "brand_color": "oklch(0.65 0.18 45)",
            "og_image_url": None,
            "created_at": "2026-07-03T00:00:00Z",
            "updated_at": "2026-07-03T00:00:00Z"
        }])
    ]
    mock_site_sections.execute.return_value = MagicMock(data=[])

    mock_create_pipeline.return_value = MockPipeline()
    valid_prompt = "a" * 2000
    payload_valid = {"prompt": valid_prompt}
    response = client.post(
        "/api/sites/generate",
        json=payload_valid,
        headers={"Authorization": f"Bearer {token}"}
    )
    # Pydantic validation should succeed (we might get 200, or pipeline result)
    assert response.status_code == 200


# 3. Metadata is reverted properly on section insertion failure.
@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_update_site_metadata_reverted_on_section_failure(mock_secret, mock_db_setup):
    """
    Verify that if the site section insertion fails during update,
    the metadata is reverted to its original state and backup sections are restored.
    """
    _, _, mock_sites, mock_site_sections = mock_db_setup

    site_id = str(uuid4())
    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=user_id)

    original_metadata = {
        "id": site_id,
        "owner_id": user_id,
        "name": "Original Name",
        "slug": "original-slug",
        "status": "draft",
        "seo_title": "Original SEO Title",
        "seo_description": "Original SEO Description",
        "og_image_url": "http://example.com/original.png",
        "brand_color": "#000000"
    }

    updated_metadata = {
        "id": site_id,
        "owner_id": user_id,
        "name": "New Name",
        "slug": "new-slug",
        "status": "published",
        "seo_title": "New SEO Title",
        "seo_description": "New SEO Description",
        "og_image_url": "http://example.com/new.png",
        "brand_color": "#ffffff"
    }

    backup_sections = [
        {
            "id": str(uuid4()),
            "site_id": site_id,
            "kind": "hero",
            "position": 0,
            "content": {"title": "Welcome"}
        }
    ]

    # Setup execute side effects:
    # 1. Sites table:
    #    - first call: select/existence check -> returns original_metadata
    #    - second call: update to new metadata -> returns updated_metadata
    #    - third call (rollback): update back to original -> returns original_metadata
    mock_sites.execute.side_effect = [
        MagicMock(data=[original_metadata]),
        MagicMock(data=[updated_metadata]),
        MagicMock(data=[original_metadata])
    ]

    # 2. Site sections table:
    #    - first call: select/backup existing sections -> returns backup_sections
    #    - second call: delete existing sections -> returns empty list (success)
    #    - third call: insert new sections -> raises exception (fails!)
    #    - fourth call (rollback delete): delete any partial sections -> returns empty list (success)
    #    - fifth call (rollback restore): insert backup sections -> returns backup_sections (success)
    mock_site_sections.execute.side_effect = [
        MagicMock(data=backup_sections),
        MagicMock(data=[]),
        Exception("DB Error: Unique constraint violation on position"),
        MagicMock(data=[]),
        MagicMock(data=backup_sections)
    ]

    payload = {
        "name": "New Name",
        "slug": "new-slug",
        "status": "published",
        "seo_title": "New SEO Title",
        "seo_description": "New SEO Description",
        "og_image_url": "http://example.com/new.png",
        "brand_color": "#ffffff",
        "sections": [
            {"kind": "about", "position": 0, "content": {"text": "About us"}}
        ]
    }

    response = client.put(
        f"/api/sites/{site_id}",
        json=payload,
        headers={"Authorization": f"Bearer {token}"}
    )

    # Put should fail with HTTP 400 because section insertion failed
    assert response.status_code == 400
    assert "Failed to replace sections" in response.json()["detail"]

    # Verify that metadata rollback was triggered
    # The first update is to updated_metadata values, the second update should be the revert
    assert mock_sites.update.call_count == 2
    
    # Check first update arguments
    first_update_args = mock_sites.update.call_args_list[0][0][0]
    assert first_update_args["name"] == "New Name"
    assert first_update_args["slug"] == "new-slug"
    
    # Check second update (rollback) arguments - should restore original metadata
    second_update_args = mock_sites.update.call_args_list[1][0][0]
    assert second_update_args["name"] == "Original Name"
    assert second_update_args["slug"] == "original-slug"
    assert second_update_args["status"] == "draft"

    # Verify that sections were cleaned up and restored
    assert mock_site_sections.delete.call_count == 2  # first delete, then rollback delete
    assert mock_site_sections.insert.call_count == 2  # first failed insert, then rollback insert (restore)
    
    # Check rollback insert restore arguments
    restore_args = mock_site_sections.insert.call_args_list[1][0][0]
    assert len(restore_args) == 1
    assert restore_args[0]["kind"] == "hero"
    assert restore_args[0]["content"]["title"] == "Welcome"


# 4. JWT sub claim type and format validation.
@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_jwt_sub_validation_adversarial(mock_secret):
    """
    Verify that invalid sub claims (non-string, empty, or non-UUID) are rejected with HTTP 401.
    """
    # Case A: sub is not a string (e.g., int)
    token_int = jwt.encode({"aud": "authenticated", "email": "u@ex.com", "sub": 12345}, TEST_SECRET, algorithm="HS256")
    response = client.get("/api/sites", headers={"Authorization": f"Bearer {token_int}"})
    assert response.status_code == 401
    assert "must be a non-empty string" in response.json()["detail"]

    # Case B: sub is empty string
    token_empty = jwt.encode({"aud": "authenticated", "email": "u@ex.com", "sub": ""}, TEST_SECRET, algorithm="HS256")
    response = client.get("/api/sites", headers={"Authorization": f"Bearer {token_empty}"})
    assert response.status_code == 401
    assert "must be a non-empty string" in response.json()["detail"]

    # Case C: sub is not a valid UUID format
    token_invalid_uuid = jwt.encode({"aud": "authenticated", "email": "u@ex.com", "sub": "not-a-uuid-format"}, TEST_SECRET, algorithm="HS256")
    response = client.get("/api/sites", headers={"Authorization": f"Bearer {token_invalid_uuid}"})
    assert response.status_code == 401
    assert "must be a valid UUID" in response.json()["detail"]


# 5. Payloads containing only whitespace strings fail validation with HTTP 422.
@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_generate_endpoint_whitespace_validation(mock_secret):
    """
    Verify that prompts/descriptions with only whitespace are rejected with HTTP 422.
    """
    token = create_token()

    # Case A: prompt contains only spaces
    payload_spaces = {"prompt": "          "}
    response = client.post(
        "/api/sites/generate",
        json=payload_spaces,
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 422

    # Case B: description contains only spaces/newlines
    payload_spaces_desc = {"description": " \n \t   "}
    response = client.post(
        "/api/sites/generate",
        json=payload_spaces_desc,
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 422


# 6. If sections backup query fails, raise HTTP 500 directly (no metadata updates should happen).
@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_update_site_sections_backup_failure(mock_secret, mock_db_setup):
    """
    Verify that if the backup select query fails, an HTTP 500 is raised directly,
    and no sites update query is executed.
    """
    _, _, mock_sites, mock_site_sections = mock_db_setup

    site_id = str(uuid4())
    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=user_id)

    # 1. Mock existence check to succeed
    mock_sites.execute.return_value = MagicMock(data=[{"id": site_id, "owner_id": user_id, "name": "Original Name"}])

    # 2. Mock backup select query to raise Exception
    mock_site_sections.execute.side_effect = Exception("Select backup failed")

    payload = {
        "name": "New Name",
        "sections": [
            {"kind": "hero", "position": 0, "content": {}}
        ]
    }

    response = client.put(
        f"/api/sites/{site_id}",
        json=payload,
        headers={"Authorization": f"Bearer {token}"}
    )

    # Should raise HTTP 500
    assert response.status_code == 500
    assert "Failed to backup existing sections" in response.json()["detail"]

    # Verify that sites metadata update was NEVER called because we exited early on backup select failure
    assert mock_sites.update.call_count == 0
