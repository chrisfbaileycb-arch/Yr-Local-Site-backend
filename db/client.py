import os
from functools import lru_cache
from supabase import create_client, Client
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

@lru_cache()
def _get_client(url: str, key: str) -> Client:
    return create_client(url, key)

def get_db() -> Client:
    """
    Returns a cached Supabase client instance using parameterized caching.
    Uses _get_client to cache based on url and key, fetching env dynamically.

    WARNING:
    This client is a shared cached singleton and is NOT thread-safe for request-scoped
    header or authentication mutation (e.g. client.postgrest.auth(...) or modifying
    headers directly). Doing so could leak authentication context across requests/threads.
    For user-scoped or request-specific authentication, developers must instantiate
    fresh client instances (e.g., using `create_client(url, key)`) instead of using
    this cached database singleton.
    """
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    
    # Fallback for development/testing if variables are missing
    if not supabase_url:
        supabase_url = "https://placeholder.supabase.co"
    if not supabase_key:
        supabase_key = "placeholder-key"
        
    return _get_client(supabase_url, supabase_key)

# Bind the cache_clear method of _get_client to get_db
get_db.cache_clear = _get_client.cache_clear
