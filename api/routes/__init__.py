from fastapi import APIRouter
from api.routes.sites import router as sites_router
from api.routes.generate import router as generate_router
from api.routes.audit import router as audit_router
from api.routes.leads import router as leads_router
from api.routes.publish import router as publish_router

api_router = APIRouter()
api_router.include_router(generate_router)
api_router.include_router(sites_router)
api_router.include_router(audit_router)
api_router.include_router(leads_router)
api_router.include_router(publish_router)
