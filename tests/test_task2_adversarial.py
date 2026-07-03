import json
import pytest
from uuid import UUID, uuid4
from unittest.mock import MagicMock, patch
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


# --- ADVERSARIAL TEST CASES FOR TASK 2 ---

@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
@patch("api.routes.generate.create_generation_pipeline")
def test_ai_generation_null_prompt_bypass(mock_create_pipeline, mock_secret):
    """
    Verify that '{"prompt": null}' is rejected with HTTP 422 by model validator.
    """
    class MockCrashPipeline:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        async def chat(self, prompt: str):
            if prompt is None:
                raise TypeError("prompt must be a string, not NoneType")
            return MagicMock()

    mock_create_pipeline.return_value = MockCrashPipeline()
    token = create_token()
    
    # Null prompt input is rejected with 422 validation error
    payload = {"prompt": None}
    response = client.post("/api/sites/generate", json=payload, headers={"Authorization": f"Bearer {token}"})
    
    assert response.status_code == 422


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_update_site_sections_data_loss_on_failure(mock_secret, mock_db_setup):
    """
    Verify that if the section insertion fails, we rollback by re-inserting the backed up sections
    and delete is called twice (once for initial delete, once during rollback delete).
    """
    _, _, mock_sites, mock_site_sections = mock_db_setup
    
    site_id = str(uuid4())
    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=user_id)
    
    # Mock existence check
    mock_sites.execute.return_value = MagicMock(data=[{"id": site_id, "owner_id": user_id, "name": "Test Site"}])
    
    # Mock backup select, initial delete, insert (raising exception), rollback delete, and rollback insert
    mock_site_sections.execute.side_effect = [
        MagicMock(data=[{"id": str(uuid4()), "site_id": site_id, "kind": "hero", "position": 0, "content": {}}]), # select backup succeeds
        MagicMock(data=[]), # delete succeeds
        Exception("DB Error: Unique constraint violation on position"), # insert fails
        MagicMock(data=[]), # rollback delete succeeds
        MagicMock(data=[])  # rollback restore insert succeeds
    ]
    
    payload = {
        "sections": [
            {"kind": "hero", "position": 0, "content": {}}
        ]
    }
    
    response = client.put(f"/api/sites/{site_id}", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 400
    assert "Failed to replace sections" in response.json()["detail"]
    
    # Assert that delete was called twice (once initially, once in rollback)
    assert mock_site_sections.delete.call_count == 2


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_update_site_updated_at_synced_on_sections_only(mock_secret, mock_db_setup):
    """
    Verify that if only sections are updated, the site's updated_at IS updated (update called once).
    """
    _, _, mock_sites, mock_site_sections = mock_db_setup
    
    site_id = str(uuid4())
    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=user_id)
    
    # Mock existence check
    mock_sites.execute.return_value = MagicMock(data=[{"id": site_id, "owner_id": user_id, "name": "Test Site"}])
    
    # Mock backup select, sections delete, and sections insert
    mock_site_sections.execute.side_effect = [
        MagicMock(data=[]), # select backup
        MagicMock(data=[]), # delete
        MagicMock(data=[{"kind": "hero", "position": 0, "content": {}}]) # insert
    ]
    
    payload = {
        "sections": [
            {"kind": "hero", "position": 0, "content": {}}
        ]
    }
    
    response = client.put(f"/api/sites/{site_id}", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    
    # Assert that sites.update was called exactly once to sync updated_at
    assert mock_sites.update.call_count == 1


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_create_site_product_validation(mock_secret, mock_db_setup):
    """
    Verify product creation validation: empty name/price label, invalid URL format are rejected.
    """
    token = create_token()
    
    # 1. Empty name (spaces)
    payload = {
        "name": "Pizza",
        "slug": "pizza",
        "products": [
            {"name": "   ", "description": "Good pizza", "price_label": "$10", "payment_link_url": "https://stripe.com/pay"}
        ]
    }
    response = client.post("/api/sites", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 422

    # 2. Empty price label
    payload = {
        "name": "Pizza",
        "slug": "pizza",
        "products": [
            {"name": "Synthetic Cheese Pizza", "description": "Good pizza", "price_label": "", "payment_link_url": "https://stripe.com/pay"}
        ]
    }
    response = client.post("/api/sites", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 422

    # 3. Invalid payment URL
    payload = {
        "name": "Pizza",
        "slug": "pizza",
        "products": [
            {"name": "Synthetic Cheese Pizza", "description": "Good pizza", "price_label": "$10", "payment_link_url": "not-a-valid-url"}
        ]
    }
    response = client.post("/api/sites", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 422


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_update_site_product_cross_site_access(mock_secret, mock_db_setup):
    """
    Verify that if updating products, trying to update a product ID that does not belong to the site raises 400 Bad Request.
    """
    mock_db, mock_user_roles, mock_sites, mock_site_sections, mock_products, mock_revisions, mock_audit_results = mock_db_setup
    
    site_id = str(uuid4())
    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=user_id)
    
    # Mock existence check
    mock_sites.execute.return_value = MagicMock(data=[{"id": site_id, "owner_id": user_id, "name": "Test Site"}])
    
    # Mock product backup returns a product ID
    other_product_id = str(uuid4())
    mock_products.execute.return_value = MagicMock(data=[{"id": str(uuid4()), "site_id": site_id, "name": "Backup Product"}])
    
    # Payload includes product ID that does not match backup product
    payload = {
        "products": [
            {"id": other_product_id, "name": "Attacking Product", "description": "Steal data", "price_label": "$100", "payment_link_url": "https://stripe.com/pay"}
        ]
    }
    
    response = client.put(f"/api/sites/{site_id}", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 400
    assert "Product ID does not belong to this site" in response.json()["detail"]


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
def test_publish_site_revision_rollback_on_failure(mock_secret, mock_db_setup):
    """
    Verify that if revision insert fails, the publisher rolls back site status/updated_at.
    """
    mock_db, mock_user_roles, mock_sites, mock_site_sections, mock_products, mock_revisions, mock_audit_results = mock_db_setup
    
    site_id = str(uuid4())
    user_id = "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    token = create_token(user_id=user_id)
    
    # Mock verification and site status update
    mock_sites.execute.side_effect = [
        MagicMock(data=[{"id": site_id, "owner_id": user_id, "status": "draft", "updated_at": "2026-07-03T01:00:00Z"}]), # auth check
        MagicMock(data=[{"id": site_id, "owner_id": user_id, "status": "published", "updated_at": "2026-07-03T02:00:00Z"}]) # update
    ]
    
    # Mock revision number query and mock revision insert fails
    mock_revisions.execute.side_effect = [
        MagicMock(data=[{"revision_number": 5}]), # query max revision succeeds
        Exception("DB Error: revision insert failure") # insert fails!
    ]
    
    response = client.post(f"/api/sites/{site_id}/publish", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 500
    assert "Failed to insert publication snapshot revision" in response.json()["detail"]
    
    # Verify that sites.update was called during rollback to restore original status and updated_at
    assert mock_sites.update.call_count == 2 # 1st for publishing, 2nd for rollback
    rollback_call_args = mock_sites.update.call_args_list[1][0][0]
    assert rollback_call_args["status"] == "draft"
    assert rollback_call_args["updated_at"] == "2026-07-03T01:00:00Z"


@patch("api.auth.get_supabase_jwt_secret", return_value=TEST_SECRET)
@patch("api.routes.audit.create_audit_pipeline")
def test_audit_endpoint_invalid_uuid_and_auth_bypass(mock_create_pipeline, mock_secret, mock_db_setup):
    """
    Verify audit site access: invalid UUID is rejected, and unauthorized site is rejected.
    """
    mock_db, mock_user_roles, mock_sites, mock_site_sections, mock_products, mock_revisions, mock_audit_results = mock_db_setup
    token = create_token()
    
    # 1. Invalid UUID format
    payload = {"url": "https://example.com", "siteId": "invalid-uuid-format"}
    response = client.post("/api/sites/audit", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 400
    assert "Invalid UUID format" in response.json()["detail"]

    # 2. Unauthorized site access (query returns no rows)
    mock_sites.execute.return_value = MagicMock(data=[])
    payload = {"url": "https://example.com", "siteId": str(uuid4())}
    response = client.post("/api/sites/audit", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404
    assert "Site not found" in response.json()["detail"]
