import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from jose import jwt
import main
from api.ssr_renderer import (
    validate_ga_measurement_id,
    validate_plausible_domain,
    esc,
    render_analytics_and_consent,
    render_site_blocks,
    render_site_html
)

client = TestClient(main.app)

# Helper to setup mock database chain
def setup_mock_db(mock_get_db):
    mock_db = MagicMock()
    mock_get_db.return_value = mock_db
    mock_query = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value = mock_query
    mock_query.order.return_value = mock_query
    return mock_db, mock_query

# 1. Test validate_ga_measurement_id with non-string types
def test_ga_measurement_id_non_string():
    # Int type
    try:
        res = validate_ga_measurement_id(12345)
        assert res is False
    except TypeError as e:
        pytest.fail(f"validate_ga_measurement_id raised TypeError on integer input: {e}")
        
    # List type
    try:
        res = validate_ga_measurement_id(["G-ABCDE12345"])
        assert res is False
    except TypeError as e:
        pytest.fail(f"validate_ga_measurement_id raised TypeError on list input: {e}")

# 2. Test validate_plausible_domain with non-string types
def test_plausible_domain_non_string():
    # Int type
    try:
        res = validate_plausible_domain(123)
        assert res is False
    except TypeError as e:
        pytest.fail(f"validate_plausible_domain raised TypeError on integer input: {e}")

    # List type
    try:
        res = validate_plausible_domain(["example.com"])
        assert res is False
    except TypeError as e:
        pytest.fail(f"validate_plausible_domain raised TypeError on list input: {e}")

# 3. Test render_site_blocks with non-string body in services section
def test_render_services_non_string_body():
    site = {
        "sections": [
            {
                "kind": "services",
                "headline": "Our Services",
                "body": 123  # Int instead of string
            }
        ]
    }
    try:
        html_out = render_site_blocks(site)
        assert "123" in html_out
    except TypeError as e:
        pytest.fail(f"render_site_blocks raised TypeError on non-string services body: {e}")

# 4. Test Stored XSS vulnerability in invoice_url (via javascript: protocol)
def test_invoice_url_xss_injection():
    site = {
        "id": "site-id-123",
        "name": "XSS Test Store",
        "slug": "xss-store",
        "status": "published",
        "owner_id": "owner-uuid-123",
        "invoice_url": "javascript:alert(document.cookie)",
        "white_label": False
    }
    
    # Render with is_client_view=True
    html_out = render_site_html(site, is_client_view=True)
    
    # The javascript protocol should be sanitized/blocked
    assert 'href="javascript:alert(document.cookie)"' not in html_out, "XSS Payload is successfully injected!"

# 5. Test draft site preview auth when sub claim is missing in the valid JWT token
@patch("api.auth.get_supabase_jwt_secret", return_value="test_jwt_secret_key")
@patch("api.ssr_renderer.get_db")
def test_render_ssr_draft_site_missing_sub_claim(mock_get_db, mock_secret):
    setup_mock_db(mock_get_db)
    
    # Valid signature but NO 'sub' claim
    payload = {
        "aud": "authenticated",
        "email": "user@example.com"
    }
    token = jwt.encode(payload, "test_jwt_secret_key", algorithm="HS256")
    
    # Request draft site
    response = client.get("/ssr/draft-site", headers={"Authorization": f"Bearer {token}"})
    # Since 'sub' is missing, it should not grant access and should return 401 Unauthorized
    assert response.status_code == 401

# 6. Test draft site preview auth when sub is not a valid UUID format
@patch("api.auth.get_supabase_jwt_secret", return_value="test_jwt_secret_key")
@patch("api.ssr_renderer.get_db")
def test_render_ssr_draft_site_invalid_uuid_format(mock_get_db, mock_secret):
    setup_mock_db(mock_get_db)
    
    # JWT with non-uuid sub claim
    payload = {
        "sub": "not-a-uuid",
        "aud": "authenticated",
        "email": "user@example.com"
    }
    token = jwt.encode(payload, "test_jwt_secret_key", algorithm="HS256")
    
    response = client.get("/ssr/draft-site", headers={"Authorization": f"Bearer {token}"})
    # Since sub is not a valid UUID format, it fails JWT verification with 401 Unauthorized
    assert response.status_code == 401
