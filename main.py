import os
from fastapi import FastAPI, Response, Depends
from fastapi.middleware.cors import CORSMiddleware
from db.client import get_db
from api.routes.ssr import router as ssr_router
from api.auth import get_current_user
from api.routes import api_router

app = FastAPI(title="Expo Proxy AI Backend")

# Configure CORS middleware
allowed_origins_raw = os.getenv("ALLOWED_ORIGINS")
if not allowed_origins_raw:
    origins = ["*"]
else:
    origins = [o.strip() for o in allowed_origins_raw.split(",") if o.strip()]
    if not origins:
        origins = ["*"]

allow_credentials = False if "*" in origins else True

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=allow_credentials,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
)

# Register routers
app.include_router(ssr_router)
app.include_router(api_router, prefix="/api")


@app.get("/")
def read_root():
    return {"status": "ok", "message": "Expo Proxy AI Backend API"}

@app.get("/health")
def health_check(response: Response):
    try:
        db = get_db()
        # Execute a lightweight database query to verify actual database connectivity
        db.table("profiles").select("id").limit(1).execute()
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        response.status_code = 503
        return {"status": "unhealthy", "error": str(e)}

# Non-public route to test auth.py JWT validation works
@app.get("/api/protected")
def protected_route(user: dict = Depends(get_current_user)):
    return {"status": "ok", "message": "Access granted", "user": user}
