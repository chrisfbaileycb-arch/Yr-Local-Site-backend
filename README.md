# Expo Proxy AI — Backend & Database

This is the FastAPI backend service and Supabase database schema definition for the Expo Proxy AI platform.

## Directory Structure

- `db/`: Database client files (caching connection logic).
- `supabase/migrations/`: Database schema, RLS policies, and function definitions.
- `tests/`: Automated verification and schema integrity tests.

## Setup Instructions

1. Clone or copy files to the backend directory.
2. Copy `.env.example` to `.env` and fill in the required variables:
   ```bash
   cp .env.example .env
   ```
3. Initialize virtual environment and install dependencies:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

## Running the Application

To run the development server locally:
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Alternatively, run via Docker Compose:
```bash
docker-compose up --build
```

## Migration Execution

Migrations are applied sequentially:
1. `supabase/migrations/001_initial_schema.sql`
2. `supabase/migrations/002_rls_policies.sql`
3. `supabase/migrations/003_functions.sql`

For local testing, migrations can be run directly using PostgreSQL clients (e.g., `psql` or our test runner `pytest`).

## Client Thread-Safety and Request-Scoped Auth

The Supabase client instance returned by `get_db()` is a cached shared singleton. While highly efficient for shared, system-level, or anonymous operations, **this cached client is not thread-safe for request-scoped headers, token headers, or auth state modifications** (such as calling `client.postgrest.auth(...)` or mutating default headers). Doing so can mutate the shared instance and cause authentication context leaks across concurrent requests or different threads.

### Best Practice

For operations requiring user-specific authentication or request-scoped auth tokens (e.g., executing queries on behalf of an authenticated user using their JWT), developers should instantiate a fresh, un-cached Supabase client.

Here is an example of creating a request-scoped client:

```python
import os
from supabase import create_client, Client

def get_request_scoped_db(user_jwt: str = None) -> Client:
    """
    Instantiates and returns a fresh, request-specific Supabase client.
    Use this when modifying client auth state or headers to avoid cross-request contamination.
    """
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "placeholder-key"
    
    # Instantiate a fresh, un-cached client instance
    client = create_client(supabase_url, supabase_key)
    
    if user_jwt:
        # Securely scope this instance to the user's JWT
        client.postgrest.auth(user_jwt)
        
    return client
```
