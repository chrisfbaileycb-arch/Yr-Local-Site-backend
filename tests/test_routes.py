import json
import urllib.error
import pytest
from uuid import UUID, uuid4
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient
from jose import jwt

import main
from api.auth import get_request_scoped_db
from db.client import get_db

client = TestClient(main.app)
TEST_SECRET = "test_jwt_secret_key"


def create_token(user_id="b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11", email="user@example.com"):
    payload = {
        "sub": user_id,
        "aud": "authenticated",
        "email": email
    }
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
        m.in_.return_value = m
        m.limit.return_value = m
        m.execute.return_value = MagicMock(data=[])
        return m

    mock_user_roles = make_query_mock()
    mock_sites = make_query_mock()
    mock_site_sections = make_query_mock()
    mock_products = make_query_mock()
    mock_revisions = make_query_mock()
    mock_audit_results = make_query_mock()

    # Default: user is not an admin
    mock_user_roles.execute.return_value = MagicMock(data=[])

    def table_router(table_name):
        if table_name == "user_roles":
            return mock_user_roles
        elif table_name == "sites":
            return mock_sites
        elif table_name == "site_sections":
            return mock_site_sections
        elif table_name == "products":
            return mock_products
        elif table_name == "revisions":
            return mock_revisions
        elif table_name == "audit_results":
            return mock_audit_results
        return MagicMock()

    mock_db.table.side_effect = table_router

    # Register dependency override in FastAPI
    main.app.dependency_overrides[get_request_scoped_db] = lambda: mock_db
    main.app.dependency_overrides[get_db] = lambda: mock_db
    yield mock_db, mock_user_roles, mock_sites, mock_site_sections, mock_products, mock_revisions, mock_audit_results
    # Clean up dependency override
    main.app.dependency_overrides.pop(get_request_scoped_db, None)
    main.app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def mock_db_setup_task3():
    mock_db = MagicMock()

    def make_query_mock():
        m = MagicMock()
        m.select.return_value = m
        m.eq.return_value = m
        m.order.return_value = m
        m.insert.return_value = m
        m.update.return_value = m
        m.delete.return_value = m
        m.in_.return_value = m
        m.limit.return_value = m
        m.execute.return_value = MagicMock(data=[])
        return m

    mock_user_roles = make_query_mock()
    mock_sites = make_query_mock()
    mock_site_sections = make_query_mock()
    mock_leads = make_query_mock()
    mock_products = make_query_mock()
    mock_revisions = make_query_mock()
    mock_audit_results = make_query_mock()

    # Default role check returns empty list (non-admin)
    mock_user_roles.execute.return_value = MagicMock(data=[])

    def table_router(table_name):
        if table_name == "user_roles":
            return mock_user_roles
        elif table_name == "sites":
            return mock_sites
        elif table_name == "site_sections":
            return mock_site_sections
        elif table_name == "leads":
            return mock_leads
        elif table_name == "products":
            return mock_products
        elif table_name == "revisions":
            return mock_revisions
        elif table_name == "audit_results":
            return mock_audit_results
        return MagicMock()

    mock_db.table.side_effect = table_router

    main.app.dependency_overrides[get_request_scoped_db] = lambda: mock_db
    main.app.dependency_overrides[get_db] = lambda: mock_db
    yield mock_db, mock_user_roles, mock_sites, mock_site_sections, mock_leads, mock_products, mock_revisions, mock_audit_results
    main.app.dependency_overrides.pop(get_request_scoped_db, None)
    main.app.dependency_overrides.pop(get_db, None)


# --- 1. Authentication Tests ---

@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_unauthorized_calls_missing_token(mock_secret):
    response = client.get("/api/sites")
    assert response.status_code == 401
    assert "Authorization header" in response.json()["detail"]


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_unauthorized_calls_invalid_token(mock_secret):
    response = client.get("/api/sites", headers={"Authorization": "Bearer invalid_token"})
    assert response.status_code == 401
    assert "Invalid or expired token" in response.json()["detail"]


# --- 2. CRUD Operations & Validation Tests ---

@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_list_sites_regular_user(mock_secret, mock_db_setup):
    _, mock_user_roles, mock_sites, _ = mock_db_setup

    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=user_id)

    mock_sites.execute.return_value = MagicMock(data=[
        {"id": str(uuid4()), "owner_id": user_id, "name": "Site 1", "slug": "site-1", "status": "draft"}
    ])

    response = client.get("/api/sites", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["name"] == "Site 1"

    # Verify RLS query check was applied: owner_id should be scoped
    mock_sites.eq.assert_called_with("owner_id", user_id)


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_list_sites_admin_user(mock_secret, mock_db_setup):
    _, mock_user_roles, mock_sites, _ = mock_db_setup

    # Make user an admin
    mock_user_roles.execute.return_value = MagicMock(data=[{"role": "admin"}])

    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=user_id)

    mock_sites.execute.return_value = MagicMock(data=[
        {"id": str(uuid4()), "owner_id": str(uuid4()), "name": "Site 1", "slug": "site-1", "status": "draft"},
        {"id": str(uuid4()), "owner_id": user_id, "name": "Site 2", "slug": "site-2", "status": "published"}
    ])

    response = client.get("/api/sites", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert len(response.json()) == 2

    # Verify admin check bypassed the owner_id filter
    # So eq("owner_id", ...) should NOT be in the mock calls
    calls = [call[0] for call in mock_sites.eq.call_args_list]
    assert ("owner_id", user_id) not in calls


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_get_site_by_id_happy_path(mock_secret, mock_db_setup):
    _, _, mock_sites, mock_site_sections = mock_db_setup

    site_id = str(uuid4())
    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=user_id)

    mock_sites.execute.return_value = MagicMock(data=[
        {"id": site_id, "owner_id": user_id, "name": "Site Detail", "slug": "site-detail", "status": "draft"}
    ])

    mock_site_sections.execute.return_value = MagicMock(data=[
        {"id": str(uuid4()), "site_id": site_id, "kind": "about", "position": 1, "content": {}, "updated_at": "2026-07-03T02:15:54Z"},
        {"id": str(uuid4()), "site_id": site_id, "kind": "hero", "position": 0, "content": {}, "updated_at": "2026-07-03T02:15:54Z"}
    ])

    response = client.get(f"/api/sites/{site_id}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200

    data = response.json()
    assert data["name"] == "Site Detail"
    assert len(data["sections"]) == 2
    # Verify ordered by position: hero (0) then about (1)
    assert data["sections"][0]["kind"] == "hero"
    assert data["sections"][1]["kind"] == "about"


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_get_site_by_id_not_found(mock_secret, mock_db_setup):
    _, _, mock_sites, _ = mock_db_setup

    site_id = str(uuid4())
    token = create_token()

    # Empty data list means not found or access denied
    mock_sites.execute.return_value = MagicMock(data=[])

    response = client.get(f"/api/sites/{site_id}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404
    assert "Site not found" in response.json()["detail"]


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_create_site_without_sections(mock_secret, mock_db_setup):
    _, _, mock_sites, _ = mock_db_setup

    site_id = str(uuid4())
    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=user_id)

    payload = {
        "name": "New Site",
        "slug": "new-site",
        "status": "draft",
        "brand_color": "oklch(0.65 0.18 45)"
    }

    mock_sites.execute.return_value = MagicMock(data=[
        {**payload, "id": site_id, "owner_id": user_id, "created_at": "2026-07-03T02:15:54Z", "updated_at": "2026-07-03T02:15:54Z"}
    ])

    response = client.post("/api/sites", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 201
    assert response.json()["name"] == "New Site"
    assert response.json()["sections"] == []


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_create_site_with_sections(mock_secret, mock_db_setup):
    _, _, mock_sites, mock_site_sections = mock_db_setup

    site_id = str(uuid4())
    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=user_id)

    payload = {
        "name": "New Site",
        "slug": "new-site",
        "status": "draft",
        "sections": [
            {"kind": "hero", "position": 0, "content": {"headline": "Hello"}}
        ]
    }

    mock_sites.execute.return_value = MagicMock(data=[
        {
            "id": site_id,
            "owner_id": user_id,
            "name": payload["name"],
            "slug": payload["slug"],
            "status": payload["status"],
            "brand_color": None,
            "seo_title": None,
            "seo_description": None,
            "og_image_url": None,
            "created_at": "2026-07-03T02:15:54Z",
            "updated_at": "2026-07-03T02:15:54Z"
        }
    ])

    mock_site_sections.execute.return_value = MagicMock(data=[
        {
            "id": str(uuid4()),
            "site_id": site_id,
            "kind": "hero",
            "position": 0,
            "content": {"headline": "Hello"},
            "updated_at": "2026-07-03T02:15:54Z"
        }
    ])

    response = client.post("/api/sites", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 201
    assert response.json()["name"] == "New Site"
    assert len(response.json()["sections"]) == 1
    assert response.json()["sections"][0]["kind"] == "hero"


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_create_site_slug_conflict(mock_secret, mock_db_setup):
    _, _, mock_sites, _ = mock_db_setup

    token = create_token()
    payload = {
        "name": "New Site",
        "slug": "existing-slug"
    }

    # Simulate unique constraint violation exception from DB
    mock_sites.execute.side_effect = Exception("duplicate key value violates unique constraint 'sites_slug_key'")

    response = client.post("/api/sites", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 409
    assert "already in use" in response.json()["detail"]


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_create_site_invalid_brand_color(mock_secret, mock_db_setup):
    token = create_token()
    payload = {
        "name": "New Site",
        "slug": "new-site",
        "brand_color": "invalid_color_format"
    }

    response = client.post("/api/sites", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 422


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_update_site_metadata_only(mock_secret, mock_db_setup):
    _, _, mock_sites, mock_site_sections = mock_db_setup

    site_id = str(uuid4())
    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=user_id)

    # 1. Existence check return value
    mock_sites.execute.side_effect = [
        MagicMock(data=[{"id": site_id, "owner_id": user_id, "name": "Old Name", "slug": "old-slug", "status": "draft"}]),
        MagicMock(data=[{"id": site_id, "owner_id": user_id, "name": "New Name", "slug": "old-slug", "status": "draft"}])
    ]

    mock_site_sections.execute.return_value = MagicMock(data=[])

    payload = {
        "name": "New Name"
    }

    response = client.put(f"/api/sites/{site_id}", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["name"] == "New Name"
    assert response.json()["sections"] == []


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_update_site_with_sections(mock_secret, mock_db_setup):
    _, _, mock_sites, mock_site_sections = mock_db_setup

    site_id = str(uuid4())
    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=user_id)

    # Mock DB executions:
    # 1. Check existence query
    # 2. Update metadata query
    mock_sites.execute.side_effect = [
        MagicMock(data=[{"id": site_id, "owner_id": user_id, "name": "Old", "slug": "slug", "status": "draft"}]),
        MagicMock(data=[{"id": site_id, "owner_id": user_id, "name": "Updated", "slug": "slug", "status": "draft"}])
    ]

    # Mock sections backup select, delete, and insert
    mock_site_sections.execute.side_effect = [
        MagicMock(data=[]), # backup select
        MagicMock(data=[]), # delete sections
        MagicMock(data=[ # insert sections
            {
                "id": str(uuid4()),
                "site_id": site_id,
                "kind": "about",
                "position": 0,
                "content": {},
                "updated_at": "2026-07-03T02:15:54Z"
            }
        ])
    ]

    payload = {
        "name": "Updated",
        "sections": [
            {"kind": "about", "position": 0, "content": {}}
        ]
    }

    response = client.put(f"/api/sites/{site_id}", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["name"] == "Updated"
    assert len(response.json()["sections"]) == 1
    assert response.json()["sections"][0]["kind"] == "about"


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_update_site_slug_conflict(mock_secret, mock_db_setup):
    _, _, mock_sites, _ = mock_db_setup

    site_id = str(uuid4())
    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=user_id)

    # 1. Existence check succeeds
    # 2. Update metadata raises duplicate key error
    mock_sites.execute.side_effect = [
        MagicMock(data=[{"id": site_id, "owner_id": user_id, "name": "Old", "slug": "slug", "status": "draft"}]),
        Exception("duplicate key value violates unique constraint 'sites_slug_key'")
    ]

    payload = {
        "slug": "taken-slug"
    }

    response = client.put(f"/api/sites/{site_id}", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 409
    assert "already in use" in response.json()["detail"]


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_delete_site_happy_path(mock_secret, mock_db_setup):
    _, _, mock_sites, _ = mock_db_setup

    site_id = str(uuid4())
    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=user_id)

    # 1. Existence check return value
    mock_sites.execute.side_effect = [
        MagicMock(data=[{"id": site_id}]), # select check
        MagicMock(data=[])                 # delete execution
    ]

    response = client.delete(f"/api/sites/{site_id}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["status"] == "deleted"
    assert response.json()["site_id"] == site_id


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_delete_site_not_found(mock_secret, mock_db_setup):
    _, _, mock_sites, _ = mock_db_setup

    site_id = str(uuid4())
    token = create_token()

    mock_sites.execute.return_value = MagicMock(data=[])

    response = client.delete(f"/api/sites/{site_id}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404
    assert "Site not found" in response.json()["detail"]


def test_invalid_uuid_parameter_format():
    # Calling endpoint with invalid UUID format should return 422 immediately (validated by FastAPI)
    token = create_token()
    response = client.get("/api/sites/not-a-valid-uuid", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 422


# --- 3. AI Generation Endpoint Tests ---

class MockResponse:
    async def structured_output(self):
        return {
            "name": "Mock AI Pizza",
            "slug": "mock-ai-pizza",
            "seoTitle": "Mock AI Pizza Store",
            "seoDescription": "The finest mock pizzas in town",
            "brandColor": "oklch(0.65 0.18 45)",
            "sections": [
                {
                    "kind": "hero",
                    "headline": "Finest Mock Pizzas",
                    "subheadline": "Directly from the AI",
                    "body": "Made with quality synthetic cheese.",
                    "ctaLabel": "Order Now"
                }
            ]
        }

    async def text(self):
        return json.dumps(await self.structured_output())


class MockPipeline:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def chat(self, prompt: str):
        return MockResponse()


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
@patch("api.routes.generate.create_generation_pipeline")
def test_ai_generation_endpoint_happy_path(mock_create_pipeline, mock_secret, mock_db_setup):
    mock_db, mock_user_roles, mock_sites, mock_site_sections, mock_products, mock_revisions, mock_audit_results = mock_db_setup
    mock_create_pipeline.return_value = MockPipeline()

    token = create_token()
    payload = {
        "prompt": "Create a nice pizza store with synthetic cheese"
    }

    # Mock database insert response
    mock_sites.execute.return_value = MagicMock(data=[{
        "id": "c0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
        "owner_id": "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
        "name": "Mock AI Pizza",
        "slug": "mock-ai-pizza",
        "status": "draft",
        "seo_title": "Mock AI Pizza Store",
        "seo_description": "The finest mock pizzas in town",
        "brand_color": "oklch(0.65 0.18 45)",
        "created_at": "2026-07-03T11:55:00Z",
        "updated_at": "2026-07-03T11:55:00Z"
    }])
    mock_site_sections.execute.return_value = MagicMock(data=[{
        "id": "d0eebc99-9c0b-4ef8-bb6d-6bb9bd380a12",
        "site_id": "c0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
        "kind": "hero",
        "position": 0,
        "content": {
            "headline": "Finest Mock Pizzas",
            "subheadline": "Directly from the AI",
            "body": "Made with quality synthetic cheese.",
            "ctaLabel": "Order Now"
        },
        "updated_at": "2026-07-03T11:55:00Z"
    }])

    response = client.post("/api/sites/generate", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200

    data = response.json()
    assert data["name"] == "Mock AI Pizza"
    assert data["slug"] == "mock-ai-pizza"
    assert data["seo_title"] == "Mock AI Pizza Store"
    assert data["brand_color"] == "oklch(0.65 0.18 45)"
    assert len(data["sections"]) == 1
    assert data["sections"][0]["kind"] == "hero"


# --- 4. Adversarial & Negative Test Cases (Task 2) ---

@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_update_site_not_found(mock_secret, mock_db_setup):
    _, _, mock_sites, _ = mock_db_setup
    site_id = str(uuid4())
    token = create_token()
    
    # Simulate site not found (database returns empty list)
    mock_sites.execute.return_value = MagicMock(data=[])
    
    payload = {"name": "New Name"}
    response = client.put(f"/api/sites/{site_id}", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404
    assert "Site not found" in response.json()["detail"]


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_delete_site_not_owned(mock_secret, mock_db_setup):
    _, _, mock_sites, _ = mock_db_setup
    site_id = str(uuid4())
    token = create_token(user_id="e0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11")
    
    # Simulate site not owned (empty list returned when querying with owner_id filter)
    mock_sites.execute.return_value = MagicMock(data=[])
    
    response = client.delete(f"/api/sites/{site_id}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404
    assert "Site not found" in response.json()["detail"]


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_list_sites_db_failure(mock_secret, mock_db_setup):
    _, _, mock_sites, _ = mock_db_setup
    token = create_token()
    
    # Simulate query execution error
    mock_sites.execute.side_effect = Exception("Connection lost")
    
    response = client.get("/api/sites", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 500
    assert "Database query failed" in response.json()["detail"]


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_get_site_db_failure(mock_secret, mock_db_setup):
    _, _, mock_sites, _ = mock_db_setup
    site_id = str(uuid4())
    token = create_token()
    
    # Simulate query execution error
    mock_sites.execute.side_effect = Exception("Read timeout")
    
    response = client.get(f"/api/sites/{site_id}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 500
    assert "Database query failed" in response.json()["detail"]


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_update_site_clear_sections(mock_secret, mock_db_setup):
    _, _, mock_sites, mock_site_sections = mock_db_setup
    site_id = str(uuid4())
    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=user_id)
    
    # Mock existence check and metadata update
    mock_sites.execute.side_effect = [
        MagicMock(data=[{"id": site_id, "owner_id": user_id, "name": "Old", "slug": "slug", "status": "draft"}]),
        MagicMock(data=[{"id": site_id, "owner_id": user_id, "name": "Old", "slug": "slug", "status": "draft"}])
    ]
    
    # Mock delete sections
    mock_site_sections.execute.return_value = MagicMock(data=[])
    
    payload = {
        "sections": []
    }
    
    response = client.put(f"/api/sites/{site_id}", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["sections"] == []


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_generate_site_validation_error(mock_secret):
    token = create_token()
    
    # 1. Missing both prompt and description
    response = client.post("/api/sites/generate", json={}, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 422
    
    # 2. Prompt too short (< 10 chars)
    response = client.post("/api/sites/generate", json={"prompt": "short"}, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 422


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
@patch("api.routes.generate.create_generation_pipeline")
def test_generate_site_pipeline_failure(mock_create_pipeline, mock_secret):
    token = create_token()
    
    # Mock pipeline creation throwing exception
    mock_create_pipeline.side_effect = Exception("API Quota exceeded")
    
    payload = {"prompt": "Create a nice pizza store with synthetic cheese"}
    response = client.post("/api/sites/generate", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 500
    assert "AI generation pipeline error" in response.json()["detail"]


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
@patch("api.routes.generate.create_generation_pipeline")
def test_generate_site_invalid_json_fallback(mock_create_pipeline, mock_secret):
    token = create_token()
    
    class BadResponse:
        async def structured_output(self):
            return None # Fallback triggers
        async def text(self):
            return "This is not valid json text at all"
            
    class BadPipeline:
        async def __aenter__(self): return self
        async def __aexit__(self, exc_type, exc_val, exc_tb): pass
        async def chat(self, prompt: str): return BadResponse()
        
    mock_create_pipeline.return_value = BadPipeline()
    
    payload = {"prompt": "Create a nice pizza store with synthetic cheese"}
    response = client.post("/api/sites/generate", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 502
    assert "AI agent failed to generate a valid structured site schema" in response.json()["detail"]


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_update_site_atomic_rollback_on_insert_failure(mock_secret, mock_db_setup):
    _, _, mock_sites, mock_site_sections = mock_db_setup

    site_id = str(uuid4())
    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=user_id)

    # 1. Check existence query: returns old site
    # 2. Update metadata query: returns updated site
    mock_sites.execute.side_effect = [
        MagicMock(data=[{"id": site_id, "owner_id": user_id, "name": "Old", "slug": "slug", "status": "draft"}]),
        MagicMock(data=[{"id": site_id, "owner_id": user_id, "name": "Old", "slug": "slug", "status": "draft"}])
    ]

    # Mock sections backup select, delete, insert, clean-up delete, restore insert
    backup_data = [
        {
            "id": str(uuid4()),
            "site_id": site_id,
            "kind": "hero",
            "position": 0,
            "content": {"title": "Backup"}
        }
    ]
    mock_site_sections.execute.side_effect = [
        MagicMock(data=backup_data), # backup select
        MagicMock(data=[]), # delete old
        Exception("DB insert error"), # insert new fails!
        MagicMock(data=[]), # cleanup delete
        MagicMock(data=backup_data) # restore backup insert
    ]

    payload = {
        "sections": [
            {"kind": "about", "position": 0, "content": {}}
        ]
    }

    response = client.put(f"/api/sites/{site_id}", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 400
    assert "Failed to replace sections" in response.json()["detail"]


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_update_site_atomic_rollback_critical_restore_failure(mock_secret, mock_db_setup):
    _, _, mock_sites, mock_site_sections = mock_db_setup

    site_id = str(uuid4())
    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=user_id)

    # 1. Check existence query: returns old site
    # 2. Update metadata query: returns updated site
    mock_sites.execute.side_effect = [
        MagicMock(data=[{"id": site_id, "owner_id": user_id, "name": "Old", "slug": "slug", "status": "draft"}]),
        MagicMock(data=[{"id": site_id, "owner_id": user_id, "name": "Old", "slug": "slug", "status": "draft"}])
    ]

    # Mock sections backup select, delete, insert, clean-up delete, restore insert (fails)
    backup_data = [
        {
            "id": str(uuid4()),
            "site_id": site_id,
            "kind": "hero",
            "position": 0,
            "content": {"title": "Backup"}
        }
    ]
    mock_site_sections.execute.side_effect = [
        MagicMock(data=backup_data), # backup select
        MagicMock(data=[]), # delete old
        Exception("DB insert error"), # insert new fails!
        MagicMock(data=[]), # cleanup delete
        Exception("DB restore error") # restore backup insert fails!
    ]

    payload = {
        "sections": [
            {"kind": "about", "position": 0, "content": {}}
        ]
    }

    response = client.put(f"/api/sites/{site_id}", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 500
    assert "CRITICAL: Failed to restore backup sections" in response.json()["detail"]


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_update_site_updated_at_changed_when_sections_replaced(mock_secret, mock_db_setup):
    _, _, mock_sites, mock_site_sections = mock_db_setup

    site_id = str(uuid4())
    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=user_id)

    # Mock DB executions:
    # 1. Check existence query
    # 2. Update metadata query (which should be called because sections are modified)
    mock_sites.execute.side_effect = [
        MagicMock(data=[{"id": site_id, "owner_id": user_id, "name": "Old", "slug": "slug", "status": "draft"}]),
        MagicMock(data=[{"id": site_id, "owner_id": user_id, "name": "Old", "slug": "slug", "status": "draft", "updated_at": "2026-07-03T02:30:00Z"}])
    ]

    mock_site_sections.execute.side_effect = [
        MagicMock(data=[]), # backup select
        MagicMock(data=[]), # delete sections
        MagicMock(data=[])  # insert sections
    ]

    payload = {
        "sections": [
            {"kind": "about", "position": 0, "content": {}}
        ]
    }

    response = client.put(f"/api/sites/{site_id}", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    
    # Assert that metadata update was indeed triggered (which consumes second side_effect item)
    assert mock_sites.update.called
    # Check that update included "updated_at"
    called_args, called_kwargs = mock_sites.update.call_args
    assert "updated_at" in called_args[0]


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_generate_site_null_bypass_failure(mock_secret):
    token = create_token()

    # 1. Both prompt and description are null
    payload = {
        "prompt": None,
        "description": None
    }
    response = client.post("/api/sites/generate", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 422
    assert "Either 'prompt' or 'description' must be provided as a non-empty string" in response.text

    # 2. Both prompt and description are empty strings
    payload = {
        "prompt": "",
        "description": ""
    }
    response = client.post("/api/sites/generate", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 422
    assert "Either 'prompt' or 'description' must be provided as a non-empty string" in response.text


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_update_site_slug_conflict_error_fallback(mock_secret, mock_db_setup):
    _, _, mock_sites, _ = mock_db_setup

    site_id = str(uuid4())
    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=user_id)

    # 1. Existence check succeeds, returns original slug "original-slug"
    # 2. Update metadata raises duplicate key error
    mock_sites.execute.side_effect = [
        MagicMock(data=[{"id": site_id, "owner_id": user_id, "name": "Old", "slug": "original-slug", "status": "draft"}]),
        Exception("duplicate key value violates unique constraint 'sites_slug_key'")
    ]

    # Send update where slug is not in payload (or slug is None) but name is updated
    payload = {
        "name": "New Name"
    }

    response = client.put(f"/api/sites/{site_id}", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 409
    assert "Slug 'original-slug' is already in use." in response.json()["detail"]


# --- Tests for Task 2 Iteration 3 fixes ---

@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_update_site_metadata_rollback_on_section_insert_failure(mock_secret, mock_db_setup):
    """
    Assert that if a payload updates both metadata and sections,
    and sections insert fails, the metadata update is also rolled back.
    """
    _, _, mock_sites, mock_site_sections = mock_db_setup

    site_id = str(uuid4())
    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=user_id)

    original_site_data = {
        "id": site_id,
        "owner_id": user_id,
        "name": "Old Name",
        "slug": "slug",
        "status": "draft",
        "updated_at": "2026-07-03T02:00:00Z"
    }

    # Mock DB executions:
    # 1. Check existence query: returns original_site_data
    # 2. Update metadata query: returns updated site
    # 3. Rollback metadata query: returns success (mocked)
    mock_sites.execute.side_effect = [
        MagicMock(data=[original_site_data]),
        MagicMock(data=[{**original_site_data, "name": "New Name", "updated_at": "2026-07-03T02:10:00Z"}]),
        MagicMock(data=[original_site_data])
    ]

    # Mock sections backup select, delete, and insert (fails)
    backup_data = [
        {
            "id": str(uuid4()),
            "site_id": site_id,
            "kind": "hero",
            "position": 0,
            "content": {"title": "Backup"}
        }
    ]
    mock_site_sections.execute.side_effect = [
        MagicMock(data=backup_data), # backup select
        MagicMock(data=[]), # delete old
        Exception("DB insert error"), # insert new fails!
        MagicMock(data=[]), # cleanup delete
        MagicMock(data=backup_data) # restore backup insert
    ]

    payload = {
        "name": "New Name",
        "sections": [
            {"kind": "about", "position": 0, "content": {}}
        ]
    }

    response = client.put(f"/api/sites/{site_id}", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 400
    assert "Failed to replace sections" in response.json()["detail"]

    # Verify that mock_sites.update was called twice:
    # First: to apply payload metadata ("New Name")
    # Second: to roll back to original metadata ("Old Name", original updated_at)
    assert mock_sites.update.call_count == 2
    
    first_update_args = mock_sites.update.call_args_list[0][0][0]
    assert first_update_args["name"] == "New Name"
    
    second_update_args = mock_sites.update.call_args_list[1][0][0]
    assert second_update_args["name"] == "Old Name"
    assert second_update_args["updated_at"] == "2026-07-03T02:00:00Z"


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_update_site_metadata_rollback_failure(mock_secret, mock_db_setup):
    """
    Assert that if metadata rollback itself fails during a section insert failure,
    a 500 error is returned with critical detail.
    """
    _, _, mock_sites, mock_site_sections = mock_db_setup

    site_id = str(uuid4())
    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=user_id)

    original_site_data = {
        "id": site_id,
        "owner_id": user_id,
        "name": "Old Name",
        "slug": "slug",
        "status": "draft",
        "updated_at": "2026-07-03T02:00:00Z"
    }

    # Mock DB executions:
    # 1. Check existence query: returns original_site_data
    # 2. Update metadata query: returns updated site
    # 3. Rollback metadata query: raises exception!
    mock_sites.execute.side_effect = [
        MagicMock(data=[original_site_data]),
        MagicMock(data=[{**original_site_data, "name": "New Name", "updated_at": "2026-07-03T02:10:00Z"}]),
        Exception("Metadata rollback DB failure")
    ]

    # Mock sections backup select, delete, and insert (fails)
    backup_data = []
    mock_site_sections.execute.side_effect = [
        MagicMock(data=backup_data), # backup select
        MagicMock(data=[]), # delete old
        Exception("DB insert error"), # insert new fails!
        MagicMock(data=[]), # cleanup delete
    ]

    payload = {
        "name": "New Name",
        "sections": [
            {"kind": "about", "position": 0, "content": {}}
        ]
    }

    response = client.put(f"/api/sites/{site_id}", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 500
    assert "Failed to roll back metadata" in response.json()["detail"] or "double rollback failure" in response.json()["detail"]


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_missing_sub_claim_unauthorized(mock_secret):
    """
    Assert that a JWT missing the 'sub' claim returns a 401 Unauthorized status.
    """
    payload = {
        "aud": "authenticated",
        "email": "user@example.com"
        # 'sub' is missing
    }
    token = jwt.encode(payload, TEST_SECRET, algorithm="HS256")
    
    response = client.get("/api/sites", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert "missing subject" in response.json()["detail"].lower()


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_generate_site_max_length_validation_error(mock_secret):
    """
    Assert that prompts or descriptions exceeding 2000 characters fail validation with 422.
    """
    token = create_token()
    
    # 1. Prompt too long (> 2000 chars)
    long_prompt = "a" * 2001
    response = client.post("/api/sites/generate", json={"prompt": long_prompt}, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 422
    
    # 2. Description too long (> 2000 chars)
    long_desc = "a" * 2001
    response = client.post("/api/sites/generate", json={"description": long_desc}, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 422


# =====================================================================
# 3. Task 3 Ported Tests
# =====================================================================

@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
@patch("api.routes.audit.create_audit_pipeline")
@patch("api.routes.audit.asyncio.to_thread")
def test_audit_route_happy_path(mock_to_thread, mock_create_pipeline, mock_secret, mock_db_setup):
    """
    Test audit endpoint fetches HTML, runs agent pipeline, and calculates score.
    """
    mock_db, mock_user_roles, mock_sites, mock_site_sections, mock_products, mock_revisions, mock_audit_results = mock_db_setup
    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    
    # Mock site ownership check and audit insertion
    mock_sites.execute.return_value = MagicMock(data=[{"owner_id": user_id}])
    mock_audit_results.execute.return_value = MagicMock(data=[{"id": "test-audit-id"}])

    token = create_token(user_id=user_id)
    mock_to_thread.return_value = "<html><head><title>Test</title></head><body>Hello</body></html>"

    # Setup agent response mock
    mock_pipeline = MagicMock()
    mock_response = MagicMock()
    
    mock_findings = [
        {"category": "seo", "severity": "critical", "title": "Missing meta description", "description": "No description"},
        {"category": "content", "severity": "warning", "title": "Bad content readability", "description": "Text is too hard to read"},
        {"category": "performance", "severity": "info", "title": "Unoptimized images", "description": "Optimize images"}
    ]
    
    mock_response.structured_output = AsyncMock(return_value={
        "score": 85,
        "findings": mock_findings
    })
    mock_response.text = AsyncMock(return_value=json.dumps({"score": 85, "findings": mock_findings}))
    mock_pipeline.chat = AsyncMock(return_value=mock_response)
    
    # Context manager mock
    mock_pipeline.__aenter__ = AsyncMock(return_value=mock_pipeline)
    mock_pipeline.__aexit__ = AsyncMock(return_value=None)
    mock_create_pipeline.return_value = mock_pipeline

    payload = {
        "url": "https://example.com/test-audit",
        "siteId": str(uuid4())
    }

    response = client.post(
        "/api/sites/audit",
        json=payload,
        headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["targetUrl"] == payload["url"]
    assert data["siteId"] == payload["siteId"]
    assert data["status"] == "complete"
    assert len(data["findings"]) == 3

    # Deductions: critical (25) + warning->medium (8) + info->low (3) = 36 deduction
    # score = 100 - 36 = 64
    assert data["score"] == 64

    # Assert categories were mapped: 'content' -> 'copy'
    categories = [f["category"] for f in data["findings"]]
    assert "copy" in categories
    assert "seo" in categories
    assert "performance" in categories

    # Assert severities were mapped: 'warning' -> 'medium', 'info' -> 'low'
    severities = [f["severity"] for f in data["findings"]]
    assert "critical" in severities
    assert "medium" in severities
    assert "low" in severities


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
@patch("api.routes.audit.asyncio.to_thread")
def test_audit_route_fetch_error_http(mock_to_thread, mock_secret):
    """
    HTTPError during HTML fetch should raise 422.
    """
    token = create_token()
    mock_to_thread.side_effect = urllib.error.HTTPError(
        url="https://example.com", code=404, msg="Not Found", hdrs=None, fp=None
    )

    payload = {"url": "https://example.com/not-found"}
    response = client.post(
        "/api/sites/audit",
        json=payload,
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 422
    assert "HTTP Error" in response.json()["detail"]


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
@patch("api.routes.audit.asyncio.to_thread")
def test_audit_route_fetch_error_url(mock_to_thread, mock_secret):
    """
    URLError during HTML fetch should raise 422.
    """
    token = create_token()
    mock_to_thread.side_effect = urllib.error.URLError(reason="Connection refused")

    payload = {"url": "https://example.com/refused"}
    response = client.post(
        "/api/sites/audit",
        json=payload,
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 422
    assert "URL Error" in response.json()["detail"]


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_audit_route_unauthorized(mock_secret):
    """
    Missing authorization header should return 401.
    """
    response = client.post("/api/sites/audit", json={"url": "https://example.com"})
    assert response.status_code == 401


@patch("api.routes.leads.httpx.AsyncClient.post")
def test_submit_lead_happy_path(mock_httpx_post, mock_db_setup_task3):
    """
    Submitting a valid lead saves to DB and triggers background Resend email dispatch.
    """
    _, _, _, _, mock_leads = mock_db_setup_task3

    lead_id = str(uuid4())
    lead_created_at = "2026-07-03T03:04:05.123456Z"
    
    # Mock database return value
    mock_leads.execute.return_value = MagicMock(data=[{
        "id": lead_id,
        "name": "Jane Doe",
        "email": "jane@example.com",
        "project_type": "web-app",
        "budget": "$10,000",
        "message": "We need a new dashboard built.",
        "created_at": lead_created_at
    }])

    # Mock Resend API response
    mock_email_response = MagicMock()
    mock_email_response.is_success = True
    mock_httpx_post.return_value = mock_email_response

    payload = {
        "name": "Jane Doe",
        "email": "jane@example.com",
        "projectType": "web-app",
        "budget": "$10,000",
        "message": "We need a new dashboard built."
    }

    # Set Resend environment variables to trigger the client call
    with patch.dict("os.environ", {
        "RESEND_API_KEY": "re_123456",
        "RESEND_FROM_EMAIL": "alerts@expoproxy.ai",
        "RESEND_NOTIFY_EMAIL": "admin@expoproxy.ai"
    }):
        response = client.post("/api/leads", json=payload)

    assert response.status_code == 201
    data = response.json()
    assert data["id"] == lead_id
    assert data["name"] == payload["name"]
    assert data["email"] == payload["email"]
    assert data["projectType"] == payload["projectType"]
    assert data["budget"] == payload["budget"]
    assert data["message"] == payload["message"]

    # Verify Supabase database insert call
    mock_leads.insert.assert_called_once_with({
        "name": "Jane Doe",
        "email": "jane@example.com",
        "project_type": "web-app",
        "budget": "$10,000",
        "message": "We need a new dashboard built."
    })


def test_submit_lead_invalid_email(mock_db_setup_task3):
    """
    Submitting lead with invalid email format should fail schema validation.
    """
    payload = {
        "name": "Jane Doe",
        "email": "not-an-email",
        "projectType": "web-app",
        "budget": "$10,000",
        "message": "We need a new dashboard built."
    }
    response = client.post("/api/leads", json=payload)
    assert response.status_code == 422
    assert "email" in response.json()["detail"][0]["loc"]


@patch("api.routes.leads.httpx.AsyncClient.post")
def test_submit_lead_resend_error_handled_silently(mock_httpx_post, mock_db_setup_task3):
    """
    If Resend fails or throws an exception, the lead endpoint should still return 201.
    """
    _, _, _, _, mock_leads = mock_db_setup_task3
    lead_id = str(uuid4())
    
    mock_leads.execute.return_value = MagicMock(data=[{
        "id": lead_id,
        "name": "Jane Doe",
        "email": "jane@example.com",
        "project_type": "web-app",
        "budget": "$10,000",
        "message": "We need a new dashboard built.",
        "created_at": "2026-07-03T03:04:05.123456Z"
    }])

    # Mock Resend API throwing exception
    mock_httpx_post.side_effect = Exception("Resend connection timed out")

    payload = {
        "name": "Jane Doe",
        "email": "jane@example.com",
        "projectType": "web-app",
        "budget": "$10,000",
        "message": "We need a new dashboard built."
    }

    with patch.dict("os.environ", {
        "RESEND_API_KEY": "re_123456",
        "RESEND_FROM_EMAIL": "alerts@expoproxy.ai",
        "RESEND_NOTIFY_EMAIL": "admin@expoproxy.ai"
    }):
        response = client.post("/api/leads", json=payload)

    # Must succeed despite email notification error
    assert response.status_code == 201
    assert response.json()["id"] == lead_id


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_publish_site_happy_path(mock_secret, mock_db_setup_task3):
    """
    User publishes their own site.
    """
    _, mock_user_roles, mock_sites, _, _ = mock_db_setup_task3
    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=user_id)
    site_id = str(uuid4())

    # Mock database check (site exists and owned by user)
    mock_sites.execute.side_effect = [
        MagicMock(data=[{"id": site_id, "owner_id": user_id, "name": "My Site", "slug": "my-site", "status": "draft"}]),  # auth check select
        MagicMock(data=[{"id": site_id, "owner_id": user_id, "name": "My Site", "slug": "my-site", "status": "published", "created_at": "2026-07-03T01:00:00Z", "updated_at": "2026-07-03T03:00:00Z"}])  # update execution
    ]

    response = client.post(
        f"/api/sites/{site_id}/publish",
        headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "published"


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_publish_site_not_owner_returns_404(mock_secret, mock_db_setup_task3):
    """
    User tries to publish another user's site, returns 404.
    """
    _, mock_user_roles, mock_sites, _, _ = mock_db_setup_task3
    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=user_id)
    site_id = str(uuid4())

    # Return empty list representing site not found or access denied
    mock_sites.execute.return_value = MagicMock(data=[])

    response = client.post(
        f"/api/sites/{site_id}/publish",
        headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 404
    assert "Site not found" in response.json()["detail"]


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_publish_site_admin_bypass(mock_secret, mock_db_setup_task3):
    """
    Admin can publish another user's site.
    """
    _, mock_user_roles, mock_sites, _, _ = mock_db_setup_task3
    admin_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=admin_id)
    site_id = str(uuid4())

    # Make user an admin
    mock_user_roles.execute.return_value = MagicMock(data=[{"role": "admin"}])

    owner_id = str(uuid4())
    mock_sites.execute.side_effect = [
        MagicMock(data=[{"id": site_id, "owner_id": owner_id, "name": "Other Site", "slug": "other-site", "status": "draft"}]),  # auth check select
        MagicMock(data=[{"id": site_id, "owner_id": owner_id, "name": "Other Site", "slug": "other-site", "status": "published", "created_at": "2026-07-03T01:00:00Z", "updated_at": "2026-07-03T03:00:00Z"}])  # update execution
    ]

    response = client.post(
        f"/api/sites/{site_id}/publish",
        headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "published"


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_unpublish_site_happy_path(mock_secret, mock_db_setup_task3):
    """
    User unpublishes their own site (updates status to 'draft').
    """
    _, mock_user_roles, mock_sites, _, _ = mock_db_setup_task3
    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=user_id)
    site_id = str(uuid4())

    # Mock database check
    mock_sites.execute.side_effect = [
        MagicMock(data=[{"id": site_id, "owner_id": user_id, "name": "My Site", "slug": "my-site", "status": "published"}]),  # auth check select
        MagicMock(data=[{"id": site_id, "owner_id": user_id, "name": "My Site", "slug": "my-site", "status": "draft", "created_at": "2026-07-03T01:00:00Z", "updated_at": "2026-07-03T03:00:00Z"}])  # update execution
    ]

    response = client.post(
        f"/api/sites/{site_id}/unpublish",
        headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "draft"


# =====================================================================
# 4. CORS Middleware Tests
# =====================================================================

def test_cors_preflight_valid_origin():
    """
    OPTIONS request with Origin header, checks Access-Control-Allow-Origin
    is returned and contains appropriate methods/headers.
    """
    headers = {
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "Content-Type",
    }
    response = client.options("/", headers=headers)
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "*"
    assert "GET" in response.headers.get("access-control-allow-methods", "")
    assert "Content-Type" in response.headers.get("access-control-allow-headers", "")


def test_cors_actual_request_valid_origin():
    """
    GET / with Origin header, checks Access-Control-Allow-Origin is returned.
    """
    headers = {
        "Origin": "http://localhost:3000",
    }
    response = client.get("/", headers=headers)
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "*"


def test_cors_middleware_starlette_behavior():
    """
    Programmatically creates test apps to verify Starlette's CORSMiddleware
    behaves as expected for both wildcard and credentialed configurations.
    """
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.testclient import TestClient

    # 1. Wildcard origin config
    app_wildcard = FastAPI()
    app_wildcard.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    @app_wildcard.get("/")
    def read_root_wc():
        return {"ok": True}

    client_wildcard = TestClient(app_wildcard)
    resp = client_wildcard.get("/", headers={"Origin": "http://example.com"})
    assert resp.headers.get("access-control-allow-origin") == "*"
    assert resp.headers.get("access-control-allow-credentials") is None

    # 2. Specific origin config (credentialed)
    app_cred = FastAPI()
    app_cred.add_middleware(
        CORSMiddleware,
        allow_origins=["http://example.com"],
        allow_credentials=True,
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    @app_cred.get("/")
    def read_root_cr():
        return {"ok": True}

    client_cred = TestClient(app_cred)
    resp = client_cred.get("/", headers={"Origin": "http://example.com"})
    assert resp.headers.get("access-control-allow-origin") == "http://example.com"
    assert resp.headers.get("access-control-allow-credentials") == "true"

    # Non-matching origin
    resp_bad = client_cred.get("/", headers={"Origin": "http://another.com"})
    assert resp_bad.headers.get("access-control-allow-origin") is None


