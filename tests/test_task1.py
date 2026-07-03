import os
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
from jose import jwt
import main
from api.ssr_renderer import validate_ga_measurement_id, validate_plausible_domain, esc, validate_brand_color
from api.auth import get_request_scoped_db
from fastapi.security import HTTPAuthorizationCredentials

client = TestClient(main.app)

# 1. HTML escaping test
def test_escape_html():
    assert esc("hello") == "hello"
    assert esc("<div>test</div>") == "&lt;div&gt;test&lt;/div&gt;"
    assert esc(None) == ""
    assert esc(123) == "123"

# 2. Analytics regex validation tests
def test_validate_ga_measurement_id():
    assert validate_ga_measurement_id("G-ABCDE12345") is True
    assert validate_ga_measurement_id("G-1234567") is True
    # UA- format is not supported by our current GA regex
    assert validate_ga_measurement_id("UA-12345-1") is False
    assert validate_ga_measurement_id("G-INVALID!") is False
    assert validate_ga_measurement_id("") is False
    assert validate_ga_measurement_id(None) is False

def test_validate_plausible_domain():
    assert validate_plausible_domain("example.com") is True
    assert validate_plausible_domain("sub.example.co.uk") is True
    assert validate_plausible_domain("invalid_domain") is False
    assert validate_plausible_domain("https://example.com") is False
    assert validate_plausible_domain("example.com/path") is False
    assert validate_plausible_domain(None) is False

# 3. JWT validation tests
@patch("api.auth.get_supabase_jwt_secret", return_value="test_jwt_secret_key")
def test_auth_valid_token(mock_secret):
    payload = {
        "sub": "b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
        "aud": "authenticated",
        "email": "user@example.com"
    }
    token = jwt.encode(payload, "test_jwt_secret_key", algorithm="HS256")
    
    response = client.get("/api/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["user"]["sub"] == payload["sub"]

@patch("api.auth.get_supabase_jwt_secret", return_value="test_jwt_secret_key")
def test_auth_invalid_token(mock_secret):
    response = client.get("/api/protected", headers={"Authorization": "Bearer invalid_token_value"})
    assert response.status_code == 401

def test_auth_missing_token():
    response = client.get("/api/protected")
    assert response.status_code == 401

@patch("main.get_db")
def test_public_paths_exempt(mock_get_db):
    mock_db = MagicMock()
    mock_get_db.return_value = mock_db
    mock_query = MagicMock()
    mock_db.table.return_value = mock_query
    mock_query.select.return_value = mock_query
    mock_query.limit.return_value = mock_query
    
    response_health = client.get("/health")
    assert response_health.status_code == 200
    
    response_root = client.get("/")
    assert response_root.status_code == 200

# Helper to setup mock database chain
def setup_mock_db(mock_get_db):
    mock_db = MagicMock()
    mock_get_db.return_value = mock_db
    
    mock_query = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value = mock_query
    mock_query.order.return_value = mock_query
    
    return mock_db, mock_query

# 5. SSR page rendering permissions & script injection
@patch("api.auth.get_supabase_jwt_secret", return_value="test_jwt_secret_key")
@patch("api.ssr_renderer.get_db")
def test_render_ssr_published_site(mock_get_db, mock_secret):
    mock_db, mock_query = setup_mock_db(mock_get_db)
    
    mock_site = MagicMock()
    mock_site.data = [{
        "id": "site-id-123",
        "name": "Pizza Store",
        "slug": "pizza-store",
        "status": "published",
        "owner_id": "owner-uuid-123",
        "brand_color": "#ff5733",
        "analytics_provider": "ga",
        "ga_measurement_id": "G-ABCDE12345",
        "cookie_consent_enabled": True,
        "invoice_url": "https://stripe.com/invoice/123",
        "white_label": False
    }]
    
    mock_sections = MagicMock()
    mock_sections.data = [
        {"kind": "hero", "content": {"headline": "Best Pizza", "body": "Tasty Italian slices"}},
        {"kind": "about", "content": {"headline": "About Us", "subheadline": "We have been cooking since 1990", "body": "Great heritage"}},
        {"kind": "services", "content": {"headline": "Our Services", "subheadline": "Quality pizza", "body": "Delivery | Catering | Dine-In"}},
        {"kind": "contact", "content": {"headline": "Order Now"}}
    ]
    
    mock_query.execute.side_effect = [
        mock_site,
        mock_sections
    ]
    
    response = client.get("/ssr/pizza-store")
    assert response.status_code == 200
    assert "Pizza Store" in response.text
    assert "Best Pizza" in response.text
    assert "About Us" in response.text
    assert "We have been cooking since 1990" in response.text
    assert "Our Services" in response.text
    assert "Delivery" in response.text
    assert "Catering" in response.text
    assert "Dine-In" in response.text
    assert "epa-consent-banner" in response.text
    assert "https://www.googletagmanager.com/gtag/js?id=G-ABCDE12345" in response.text

def test_render_site_html_helper():
    from api.ssr_renderer import render_site_html
    site = {
        "id": "site-id-123",
        "name": "Pizza Store",
        "slug": "pizza-store",
        "status": "published",
        "owner_id": "owner-uuid-123",
        "brand_color": "#ff5733",
        "analytics_provider": "plausible",
        "plausible_domain": "pizza.store",
        "cookie_consent_enabled": False,
        "invoice_url": "https://stripe.com/invoice/123",
        "white_label": True
    }
    # 1. White label true, is_client_view false -> no invoice btn, no footer brand, Plausible normal script
    html_out = render_site_html(site, is_client_view=False)
    assert "Pay Invoice" not in html_out
    assert "Built with" not in html_out
    assert 'src="https://plausible.io/js/script.js"' in html_out
    assert 'data-domain="pizza.store"' in html_out
    
    # 2. White label false, is_client_view true -> has invoice btn, has footer brand
    site["white_label"] = False
    html_out_2 = render_site_html(site, is_client_view=True)
    assert "Pay Invoice" in html_out_2
    assert "Built with" in html_out_2

# 6. Draft visibility tests
@patch("api.auth.get_supabase_jwt_secret", return_value="test_jwt_secret_key")
@patch("api.ssr_renderer.get_db")
def test_render_ssr_draft_site_owner(mock_get_db, mock_secret):
    mock_db, mock_query = setup_mock_db(mock_get_db)
    
    mock_site = MagicMock()
    mock_site.data = [{
        "id": "site-id-123",
        "name": "Draft Pizza",
        "slug": "draft-pizza",
        "status": "draft",
        "owner_id": "11111111-1111-1111-1111-111111111111"
    }]
    mock_sections = MagicMock()
    mock_sections.data = []
    
    mock_query.execute.side_effect = [
        mock_site,
        mock_sections
    ]
    
    # Owner requests the page
    payload = {
        "sub": "11111111-1111-1111-1111-111111111111",
        "aud": "authenticated",
        "email": "owner@example.com"
    }
    token = jwt.encode(payload, "test_jwt_secret_key", algorithm="HS256")
    
    response = client.get("/ssr/draft-pizza", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert "Draft Pizza" in response.text

@patch("api.auth.get_supabase_jwt_secret", return_value="test_jwt_secret_key")
@patch("api.ssr_renderer.get_db")
def test_render_ssr_draft_site_admin(mock_get_db, mock_secret):
    mock_db, mock_query = setup_mock_db(mock_get_db)
    
    mock_site = MagicMock()
    mock_site.data = [{
        "id": "site-id-123",
        "name": "Draft Pizza",
        "slug": "draft-pizza",
        "status": "draft",
        "owner_id": "11111111-1111-1111-1111-111111111111"
    }]
    mock_sections = MagicMock()
    mock_sections.data = []
    
    mock_roles = MagicMock()
    mock_roles.data = [{"role": "admin"}]
    
    mock_query.execute.side_effect = [
        mock_site,
        mock_roles,
        mock_sections
    ]
    
    # Admin requests the page
    payload = {
        "sub": "22222222-2222-2222-2222-222222222222",
        "aud": "authenticated",
        "email": "admin@example.com"
    }
    token = jwt.encode(payload, "test_jwt_secret_key", algorithm="HS256")
    
    response = client.get("/ssr/draft-pizza", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert "Draft Pizza" in response.text

@patch("api.auth.get_supabase_jwt_secret", return_value="test_jwt_secret_key")
@patch("api.ssr_renderer.get_db")
def test_render_ssr_draft_site_forbidden(mock_get_db, mock_secret):
    mock_db, mock_query = setup_mock_db(mock_get_db)
    
    mock_site = MagicMock()
    mock_site.data = [{
        "id": "site-id-123",
        "name": "Draft Pizza",
        "slug": "draft-pizza",
        "status": "draft",
        "owner_id": "11111111-1111-1111-1111-111111111111"
    }]
    mock_roles = MagicMock()
    mock_roles.data = [{"role": "user"}]
    
    mock_query.execute.side_effect = [
        mock_site,
        mock_roles
    ]
    
    # Wrong user requests the page
    payload = {
        "sub": "33333333-3333-3333-3333-333333333333",
        "aud": "authenticated",
        "email": "wrong@example.com"
    }
    token = jwt.encode(payload, "test_jwt_secret_key", algorithm="HS256")
    
    response = client.get("/ssr/draft-pizza", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403

# 7. Request-scoped database client tests
@patch.dict(os.environ, {"SUPABASE_URL": "https://test.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "test-key"})
@patch("api.auth.create_client")
def test_request_scoped_db_unauthenticated(mock_create_client):
    mock_client = MagicMock()
    mock_create_client.return_value = mock_client
    
    db = get_request_scoped_db(credentials=None)
    assert db is mock_client
    mock_create_client.assert_called_once_with("https://test.supabase.co", "test-key")
    mock_client.postgrest.auth.assert_not_called()

@patch.dict(os.environ, {"SUPABASE_URL": "https://test.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "test-key"})
@patch("api.auth.create_client")
def test_request_scoped_db_authenticated(mock_create_client):
    mock_client = MagicMock()
    mock_create_client.return_value = mock_client
    
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="dummy-jwt-token")
    db = get_request_scoped_db(credentials=credentials)
    assert db is mock_client
    mock_create_client.assert_called_once_with("https://test.supabase.co", "test-key")
    mock_client.postgrest.auth.assert_called_once_with("dummy-jwt-token")

def test_validate_brand_color():
    assert validate_brand_color("#fff") is True
    assert validate_brand_color("#ABCDEF") is True
    assert validate_brand_color("oklch(0.65 0.18 45)") is True
    assert validate_brand_color("oklch(0.65, 0.18, 45)") is True
    assert validate_brand_color("oklch(0.65, 0.18, 45 / 0.5)") is True
    assert validate_brand_color("oklch(0.65 0.18 45 / 50%)") is True
    assert validate_brand_color("rgb(255, 0, 0)") is True
    assert validate_brand_color("rgba(255, 0, 0, 0.5)") is True
    assert validate_brand_color("hsl(120, 100%, 50%)") is True
    assert validate_brand_color("hsla(120, 100%, 50%, 0.3)") is True
    
    assert validate_brand_color("invalid") is False
    assert validate_brand_color("#12") is False
    assert validate_brand_color("#12345") is False
    assert validate_brand_color("") is False
    assert validate_brand_color(None) is False
    assert validate_brand_color("oklch(0.65 0.18 45; injection)") is False
