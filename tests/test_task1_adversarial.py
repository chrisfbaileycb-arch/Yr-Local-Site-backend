import os
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
from jose import jwt, JWTError
import main
from api.ssr_renderer import validate_ga_measurement_id, validate_plausible_domain, esc, render_site_html, validate_brand_color
from api.auth import get_supabase_jwt_secret, verify_jwt, get_current_user, get_optional_user, get_request_scoped_db
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

client = TestClient(main.app)

# ----------------- 1. Boundary & Type Checks for GA ID Validation -----------------
def test_validate_ga_measurement_id_boundaries():
    # Exact lower limit: 6 chars after G-
    assert validate_ga_measurement_id("G-123456") is True
    # Too short: 5 chars after G-
    assert validate_ga_measurement_id("G-12345") is False
    
    # Exact upper limit: 12 chars after G-
    assert validate_ga_measurement_id("G-123456789012") is True
    # Too long: 13 chars after G-
    assert validate_ga_measurement_id("G-1234567890123") is False

    # Character set boundaries (only uppercase and numbers allowed)
    assert validate_ga_measurement_id("G-ABCDEF123456") is True
    assert validate_ga_measurement_id("G-abcdef123456") is False  # Lowercase not allowed
    assert validate_ga_measurement_id("G-ABC_DEF-123") is False   # Special chars not allowed

    # Non-string types and empty values
    assert validate_ga_measurement_id("") is False
    assert validate_ga_measurement_id(None) is False
    assert validate_ga_measurement_id(123456) is False


# ----------------- 2. Boundary & Type Checks for Plausible Domain -----------------
def test_validate_plausible_domain_boundaries():
    # Standard valid domains
    assert validate_plausible_domain("example.com") is True
    assert validate_plausible_domain("sub.example.co.uk") is True

    # Loose validation vulnerability/limitation checks
    # Single dot is rejected because it consists solely of dots
    assert validate_plausible_domain(".") is False
    assert validate_plausible_domain("example.") is True
    assert validate_plausible_domain(".example") is True
    # Spaces are accepted as long as there is a dot and no slashes
    assert validate_plausible_domain("space.com ") is True

    # Invalid domains
    assert validate_plausible_domain("http://example.com") is False
    assert validate_plausible_domain("https://example.com") is False
    assert validate_plausible_domain("example.com/path") is False
    assert validate_plausible_domain("nodot") is False
    assert validate_plausible_domain("") is False
    assert validate_plausible_domain(None) is False


# ----------------- 3. SSR Renderer Block Parsing Type Robustness -----------------
def test_render_site_blocks_non_string_body():
    # If body is not a string, re.split might crash. Let's verify how render_site_html handles this.
    site = {
        "id": "site-id-123",
        "name": "Test Site",
        "slug": "test-site",
        "status": "published",
        "sections": [
            {
                "kind": "services",
                "headline": "Our Services",
                "body": 12345  # Non-string body
            }
        ]
    }
    
    # Renders successfully without TypeError because we handle non-string body gracefully
    html_out = render_site_html(site)
    assert "12345" in html_out


def test_render_site_blocks_unknown_kind():
    # Unknown section kinds should be ignored without raising errors.
    site = {
        "id": "site-id-123",
        "name": "Test Site",
        "slug": "test-site",
        "status": "published",
        "sections": [
            {
                "kind": "unknown_block_type",
                "headline": "Ignored",
                "body": "Should not render"
            }
        ]
    }
    html_out = render_site_html(site)
    assert "Ignored" not in html_out
    assert "Should not render" not in html_out


# ----------------- 4. Stored XSS Vulnerability in invoice_url -----------------
def test_stored_xss_in_invoice_url():
    # invoice_url is escaped using esc() but not sanitized for javascript: protocol
    site = {
        "id": "site-id-123",
        "name": "XSS Test",
        "slug": "xss-test",
        "status": "published",
        "invoice_url": "javascript:alert('XSS')",
        "white_label": True
    }
    
    html_out = render_site_html(site, is_client_view=True)
    # The output should NOT contain the javascript URI, as it gets sanitized/blocked
    assert 'href="javascript:alert' not in html_out


# ----------------- 5. JWT Authorization Header Parsing Edge Cases -----------------
@patch("api.auth.get_supabase_jwt_secret", return_value="test_jwt_secret_key")
def test_auth_headers_edge_cases(mock_secret):
    # 1. Bearer with no token
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="")
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(credentials)
    assert exc_info.value.status_code == 401

    # 2. Wrong scheme (Basic)
    # Note: HTTPBearer automatically parses but let's test verify_jwt with raw token or get_current_user.
    credentials_basic = HTTPAuthorizationCredentials(scheme="Basic", credentials="some_token")
    # Even if credentials.scheme is not Bearer, get_current_user still calls verify_jwt on credentials.credentials.
    # Let's verify that it processes the token but if signature fails it raises 401.
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(credentials_basic)
    assert exc_info.value.status_code == 401


# ----------------- 6. JWT Claim Verification Vulnerability / Boundary Checks -----------------
@patch("api.auth.get_supabase_jwt_secret", return_value="test_jwt_secret")
def test_jwt_claims_adversarial(mock_secret):
    # 1. Missing 'sub' claim (user ID)
    payload_no_sub = {
        "aud": "authenticated",
        "email": "user@example.com"
        # 'sub' is missing
    }
    token_no_sub = jwt.encode(payload_no_sub, "test_jwt_secret", algorithm="HS256")
    # verify_jwt must raise HTTPException 401 because 'sub' is missing
    with pytest.raises(HTTPException) as exc_info:
        verify_jwt(token_no_sub)
    assert exc_info.value.status_code == 401
    assert "missing subject" in exc_info.value.detail.lower()

    # 2. Wrong 'aud' claim
    payload_wrong_aud = {
        "sub": "user-123",
        "aud": "anon",  # Wrong audience
        "email": "user@example.com"
    }
    token_wrong_aud = jwt.encode(payload_wrong_aud, "test_jwt_secret", algorithm="HS256")
    with pytest.raises(HTTPException) as exc_info:
        verify_jwt(token_wrong_aud)
    assert exc_info.value.status_code == 401

    # 3. Signed with 'none' algorithm
    # jose.jwt.encode might not allow 'none' easily unless we construct it or use a raw payload.
    # Standard python-jose jwt.decode with algorithms=["HS256"] will reject 'none' algorithm automatically.
    # Let's verify this behavior.
    payload_none = {"sub": "user-123", "aud": "authenticated"}
    # Constructing a 'none' algorithm token manually:
    # Header: {"alg": "none", "typ": "JWT"} -> eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0
    # Payload: {"sub": "user-123", "aud": "authenticated"} -> eyJzdWIiOiJ1c2VyLTEyMyIsImF1ZCI6ImF1dGhlbnRpY2F0ZWQifQ
    token_none = "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiJ1c2VyLTEyMyIsImF1ZCI6ImF1dGhlbnRpY2F0ZWQifQ."
    with pytest.raises(HTTPException) as exc_info:
        verify_jwt(token_none)
    assert exc_info.value.status_code == 401

    # 4. Expired token
    payload_expired = {
        "sub": "user-123",
        "aud": "authenticated",
        "exp": 1000000000  # Long in the past
    }
    token_expired = jwt.encode(payload_expired, "test_jwt_secret", algorithm="HS256")
    with pytest.raises(HTTPException) as exc_info:
        verify_jwt(token_expired)
    assert exc_info.value.status_code == 401


# ----------------- 7. Draft Visibility Permissions Boundary Checks -----------------
@patch("api.auth.get_supabase_jwt_secret", return_value="test_jwt_secret")
@patch("api.ssr_renderer.get_db")
def test_draft_visibility_missing_owner_id(mock_get_db, mock_secret):
    # Site exists but owner_id is None / null in DB.
    # If owner_id is None, str(None) is 'None'. If a user has sub='None', they would be authorized as owner!
    mock_db, mock_query = setup_mock_db(mock_get_db)
    
    mock_site = MagicMock()
    mock_site.data = [{
        "id": "site-123",
        "slug": "draft-no-owner",
        "status": "draft",
        "owner_id": None
    }]
    mock_roles = MagicMock()
    mock_roles.data = []  # No admin role
    
    mock_query.execute.side_effect = [
        mock_site,
        mock_roles
    ]
    
    # 1. User with sub="None" attempts access
    payload_malicious = {
        "sub": "None",
        "aud": "authenticated"
    }
    token = jwt.encode(payload_malicious, "test_jwt_secret", algorithm="HS256")
    response = client.get("/ssr/draft-no-owner", headers={"Authorization": f"Bearer {token}"})
    # The request should be rejected with 401 Unauthorized because "None" is not a valid UUID format
    assert response.status_code == 401


def test_validate_brand_color_injection():
    # Test valid css color functions
    assert validate_brand_color("rgb(0, 0, 0)") is True
    assert validate_brand_color("rgba(0, 0, 0, 0.5)") is True
    assert validate_brand_color("hsl(120, 100%, 50%)") is True
    assert validate_brand_color("hsla(120, 100%, 50%, 0.3)") is True

    # Test CSS injection attempts in rgb/rgba/hsl/hsla color functions
    assert validate_brand_color("rgb(0, 0, 0; position: fixed; top: 0;)") is False
    assert validate_brand_color("rgba(0, 0, 0, 0.5; xss: expression(alert(1)))") is False
    assert validate_brand_color("hsl(120, 100%, 50%); background: red;") is False
    assert validate_brand_color("hsla(120, 100%, 50%, 0.3) } body { background: red; }") is False
    assert validate_brand_color("rgb(0,0,0)injection") is False


@patch("api.auth.get_supabase_jwt_secret", return_value="test_jwt_secret")
@patch("api.ssr_renderer.get_db")
def test_draft_visibility_non_string_user_id(mock_get_db, mock_secret):
    # Site is draft, user ID (sub claim) in JWT is not a string (e.g., boolean or dictionary).
    # This should not raise TypeError during UUID parsing.
    mock_db, mock_query = setup_mock_db(mock_get_db)
    
    mock_site = MagicMock()
    mock_site.data = [{
        "id": "site-123",
        "slug": "draft-non-str-user",
        "status": "draft",
        "owner_id": "some-uuid"
    }]
    mock_query.execute.side_effect = [
        mock_site
    ]
    
    # User with boolean sub claim
    payload = {
        "sub": True,
        "aud": "authenticated"
    }
    token = jwt.encode(payload, "test_jwt_secret", algorithm="HS256")
    response = client.get("/ssr/draft-non-str-user", headers={"Authorization": f"Bearer {token}"})
    # Since sub is boolean, it fails JWT verification with 401 Unauthorized
    assert response.status_code == 401


# Helper setup (copied from test_task1.py)
def setup_mock_db(mock_get_db):
    mock_db = MagicMock()
    mock_get_db.return_value = mock_db
    mock_query = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value = mock_query
    mock_query.order.return_value = mock_query
    return mock_db, mock_query
