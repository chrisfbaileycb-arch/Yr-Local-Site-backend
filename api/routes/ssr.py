from fastapi import APIRouter
from api.ssr_renderer import render_ssr_page

router = APIRouter(tags=["ssr"])

router.add_api_route("/ssr/{slug}", render_ssr_page, methods=["GET"])
