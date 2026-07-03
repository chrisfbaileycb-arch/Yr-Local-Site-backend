"""
Authentication utilities and FastAPI dependencies for Supabase JWT validation.
Utilizes python-jose to decode and verify JWTs against SUPABASE_JWT_SECRET.
"""

import os
import uuid
from typing import Dict, Any, Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables
load_dotenv()

SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

# Bearer scheme helper (auto_error=False allows optional JWT extraction on public routes)
bearer_scheme = HTTPBearer(auto_error=False)

def get_supabase_jwt_secret() -> str:
    """Retrieves the Supabase JWT secret from environment variables."""
    if not SUPABASE_JWT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SUPABASE_JWT_SECRET is not configured on the server."
        )
    return SUPABASE_JWT_SECRET

def verify_jwt(token: str) -> Dict[str, Any]:
    """Decodes and validates a Supabase JWT using HS256 and checks claims."""
    secret = get_supabase_jwt_secret()
    try:
        # Supabase JWTs are signed with the project JWT Secret using HS256
        # and contain the claim aud: 'authenticated'
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience="authenticated"
        )
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is missing subject claim ('sub').",
            headers={"WWW-Authenticate": "Bearer"},
        )

    sub_val = payload["sub"]
    if not isinstance(sub_val, str) or not sub_val:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token subject claim ('sub') must be a non-empty string.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        uuid.UUID(sub_val)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token subject claim ('sub') must be a valid UUID.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload

def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)) -> Dict[str, Any]:
    """
    Dependency to strictly enforce JWT validation on protected endpoints.
    Raises 401 if token is missing, expired, or invalid.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header with Bearer token is missing.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return verify_jwt(credentials.credentials)

def get_optional_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)) -> Optional[Dict[str, Any]]:
    """
    Dependency to optionally parse a JWT.
    Returns the decoded payload if a valid token is provided, or None if no token is sent.
    Raises 401 if a token is sent but fails validation.
    """
    if not credentials:
        return None
    return verify_jwt(credentials.credentials)

def get_request_scoped_db(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)) -> Client:
    """
    Dependency to yield a fresh, un-cached Supabase client.
    Scopes the client with the user's JWT if authenticated, ensuring RLS enforcement.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Supabase credentials (SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY) are not configured."
        )
    
    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    if credentials:
        client.postgrest.auth(credentials.credentials)
    return client
