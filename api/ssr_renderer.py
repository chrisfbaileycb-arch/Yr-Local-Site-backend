"""
SSR Renderer for Expo Proxy AI website pages.
Renders static HTML with conditionally injected analytics scripts and cookie consent logic.
"""

import html
import re
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from db.client import get_db
from api.auth import get_optional_user

router = APIRouter()

def esc(v: Any) -> str:
    """Escapes string value for safe insertion into HTML/attributes."""
    if v is None:
        return ""
    return html.escape(str(v), quote=True)

def validate_ga_measurement_id(ga_id: str) -> bool:
    """
    Validates Google Analytics Measurement ID.
    Returns True if the string matches /^G-[A-Z0-9]{6,12}$/
    """
    if not isinstance(ga_id, str) or not ga_id:
        return False
    return bool(re.match(r"^G-[A-Z0-9]{6,12}$", ga_id))

def validate_plausible_domain(domain: str) -> bool:
    """
    Validates Plausible Domain.
    Returns True if the domain is a valid hostname (no ://, no /, contains at least one .)
    """
    if not domain or not isinstance(domain, str):
        return False
    if "://" in domain or "/" in domain:
        return False
    if "." not in domain:
        return False
    # Check if domain consists solely of dots and spaces
    if not domain.replace(".", "").replace(" ", ""):
        return False
    return True

def validate_brand_color(color: str) -> bool:
    if not color:
        return False
    hex_pattern = r"^#([A-Fa-f0-9]{3}|[A-Fa-f0-9]{6})$"
    oklch_pattern = r"^oklch\([0-9.]+(?:\s+|,\s*)[0-9.]+(?:\s+|,\s*)[0-9.]+(?:\s*/\s*[0-9.%]+)?\)$"
    other_pattern = r"^(rgb|rgba|hsl|hsla)\([0-9.,\s%/]+\)$"
    import re
    return bool(re.match(hex_pattern, color) or re.match(oklch_pattern, color) or re.match(other_pattern, color))

def render_analytics_and_consent(site: Dict[str, Any]) -> tuple[str, str]:
    """Generates analytics tracking scripts and cookie consent banner HTML."""
    provider = site.get("analytics_provider") or site.get("analyticsProvider")
    ga_id = site.get("ga_measurement_id") or site.get("gaMeasurementId")
    plausible_domain = site.get("plausible_domain") or site.get("plausibleDomain")
    is_consent_enabled = bool(site.get("cookie_consent_enabled") or site.get("cookieConsentEnabled"))
    
    head_script = ""
    body_banner = ""
    
    # 1. Google Analytics Script Injection
    if provider == "ga" and ga_id and validate_ga_measurement_id(ga_id):
        escaped_ga_id = esc(ga_id)
        if is_consent_enabled:
            head_script = f"""
  <script>
    window.epa_load_analytics = function() {{
      if (window.epa_analytics_loaded) return;
      window.epa_analytics_loaded = true;
      var s = document.createElement('script');
      s.async = true;
      s.src = 'https://www.googletagmanager.com/gtag/js?id={escaped_ga_id}';
      document.head.appendChild(s);
      window.dataLayer = window.dataLayer || [];
      function gtag() {{ dataLayer.push(arguments); }}
      window.gtag = gtag;
      gtag('js', new Date());
      gtag('config', '{escaped_ga_id}');
    }};
    if (localStorage.getItem('epa_consent') === '1') {{
      window.epa_load_analytics();
    }}
  </script>"""
        else:
            head_script = f"""
  <script async src="https://www.googletagmanager.com/gtag/js?id={escaped_ga_id}"></script>
  <script>
    window.dataLayer = window.dataLayer || [];
    function gtag() {{ dataLayer.push(arguments); }}
    gtag('js', new Date());
    gtag('config', '{escaped_ga_id}');
  </script>"""

    # 2. Plausible Analytics Script Injection
    elif provider == "plausible" and plausible_domain and validate_plausible_domain(plausible_domain):
        escaped_domain = esc(plausible_domain)
        if is_consent_enabled:
            head_script = f"""
  <script>
    window.epa_load_analytics = function() {{
      if (window.epa_analytics_loaded) return;
      window.epa_analytics_loaded = true;
      var s = document.createElement('script');
      s.defer = true;
      s.setAttribute('data-domain', '{escaped_domain}');
      s.src = 'https://plausible.io/js/script.js';
      document.head.appendChild(s);
    }};
    if (localStorage.getItem('epa_consent') === '1') {{
      window.epa_load_analytics();
    }}
  </script>"""
        else:
            head_script = f"""
  <script defer data-domain="{escaped_domain}" src="https://plausible.io/js/script.js"></script>"""

    # 3. Cookie Consent Banner
    if is_consent_enabled:
        body_banner = """
  <div id="epa-consent-banner" style="position: fixed; bottom: 0; left: 0; right: 0; background: hsl(222 47% 11%); border-top: 1px solid hsl(217 33% 17%); padding: 1rem; display: none; justify-content: space-between; align-items: center; z-index: 9999; font-family: system-ui, sans-serif; gap: 1rem; box-shadow: 0 -4px 10px rgba(0,0,0,0.3);">
    <div style="color: hsl(210 40% 96%); font-size: 0.875rem;">This site uses cookies for analytics.</div>
    <div style="display: flex; gap: 0.5rem; flex-shrink: 0;">
      <button id="epa-consent-decline" style="background: transparent; border: 1px solid hsl(217 33% 17%); color: hsl(215 20% 65%); padding: 0.5rem 1rem; border-radius: 0.375rem; cursor: pointer; font-size: 0.875rem; font-weight: 500;">Decline</button>
      <button id="epa-consent-accept" style="background: hsl(38 92% 50%); border: none; color: hsl(220 49% 8%); padding: 0.5rem 1rem; border-radius: 0.375rem; cursor: pointer; font-size: 0.875rem; font-weight: 600;">Accept</button>
    </div>
  </div>
  <script>
    (function() {
      var banner = document.getElementById('epa-consent-banner');
      var acceptBtn = document.getElementById('epa-consent-accept');
      var declineBtn = document.getElementById('epa-consent-decline');
      if (!localStorage.getItem('epa_consent')) {
        banner.style.display = 'flex';
      }
      acceptBtn.onclick = function() {
        localStorage.setItem('epa_consent', '1');
        banner.style.display = 'none';
        if (typeof window.epa_load_analytics === 'function') {
          window.epa_load_analytics();
        }
      };
      declineBtn.onclick = function() {
        localStorage.setItem('epa_consent', '0');
        banner.style.display = 'none';
      };
    })();
  </script>"""

    return head_script, body_banner

def render_site_blocks(site: Dict[str, Any]) -> str:
    """Renders site layout sections into plain HTML matching React's SiteBlockRendererSSR."""
    sections = site.get("sections") or []
    raw_color = site.get("brand_color") or site.get("brandColor")
    if not raw_color or not validate_brand_color(str(raw_color)):
        raw_color = "hsl(38 92% 50%)"
    brand_color = esc(raw_color)
    
    html_parts = []
    
    for section in sections:
        kind = section.get("kind")
        headline = esc(section.get("headline", ""))
        subheadline = section.get("subheadline")
        body = section.get("body")
        cta_label = section.get("ctaLabel") or section.get("cta_label")
        
        if kind == "hero":
            sub_html = f'<p class="epa-sub">{esc(subheadline)}</p>' if subheadline else ''
            body_html = f'<p class="epa-body" style="margin: 0 auto 1.5rem;">{esc(body)}</p>' if body else ''
            cta_html = f'<a href="#contact" class="epa-cta" style="background-color: {brand_color};">{esc(cta_label)}</a>' if cta_label else ''
            html_parts.append(f"""<section class="epa-hero">
  <div class="epa-container">
    <h1 class="epa-h1">{headline}</h1>
    {sub_html}
    {body_html}
    {cta_html}
  </div>
</section>""")
            
        elif kind == "about":
            sub_html = f'<p class="epa-about-sub">{esc(subheadline)}</p>' if subheadline else ''
            body_html = f'<p class="epa-body">{esc(body)}</p>' if body else ''
            html_parts.append(f"""<section class="epa-section">
  <div class="epa-container">
    <h2 class="epa-h2">{headline}</h2>
    {sub_html}
    {body_html}
  </div>
</section>""")
            
        elif kind == "services":
            sub_html = f'<p class="epa-sub" style="text-align: center;">{esc(subheadline)}</p>' if subheadline else ''
            grid_or_body_html = ''
            if body:
                if isinstance(body, str):
                    items = [item.strip() for item in re.split(r'[|·\n]', body) if item.strip()]
                    if items:
                        cards_html = "".join(f'<div class="epa-card">{esc(item)}</div>' for item in items)
                        grid_or_body_html = f'<div class="epa-grid">{cards_html}</div>'
                    else:
                        grid_or_body_html = f'<p class="epa-body" style="text-align: center;">{esc(body)}</p>'
                else:
                    grid_or_body_html = f'<p class="epa-body" style="text-align: center;">{esc(body)}</p>'
            html_parts.append(f"""<section class="epa-section epa-section-alt">
  <div class="epa-container">
    <h2 class="epa-h2" style="text-align: center;">{headline}</h2>
    {sub_html}
    {grid_or_body_html}
  </div>
</section>""")
            
        elif kind == "contact":
            sub_html = f'<p class="epa-sub" style="text-align: center;">{esc(subheadline)}</p>' if subheadline else ''
            cta_btn_label = esc(cta_label) if cta_label else "Send message"
            html_parts.append(f"""<section id="contact" class="epa-section">
  <div class="epa-container" style="max-width: 36rem; margin: 0 auto;">
    <h2 class="epa-h2" style="text-align: center;">{headline}</h2>
    {sub_html}
    <form action="#" method="post" style="margin-top: 2rem;">
      <input type="text" placeholder="Your name" class="epa-form-field" readonly />
      <input type="email" placeholder="Your email" class="epa-form-field" readonly />
      <textarea placeholder="Your message…" class="epa-form-field" rows="4" readonly></textarea>
      <button type="submit" class="epa-submit-btn" style="width: 100%; background-color: {brand_color}; padding: 0.75rem; border-radius: 0.375rem; font-weight: 600; font-size: 0.875rem; border: none; cursor: pointer;">{cta_btn_label}</button>
    </form>
  </div>
</section>""")

    return "\n".join(html_parts)

def render_site_html(site: Dict[str, Any], is_client_view: bool = False) -> str:
    """Assembles the final, complete HTML document with styles, SEO head, and script injections."""
    title = esc(site.get("seo_title") or site.get("seoTitle") or site.get("name") or "")
    desc = esc(site.get("seo_description") or site.get("seoDescription") or "")
    og_img = esc(site.get("og_image_url") or site.get("ogImageUrl") or "")
    slug = esc(site.get("slug") or "")
    
    body_html = render_site_blocks(site)
    analytics_head_html, consent_banner_html = render_analytics_and_consent(site)
    
    # Render invoice payment CTA button
    invoice_btn_html = ""
    invoice_url = site.get("invoice_url") or site.get("invoiceUrl")
    if invoice_url:
        # Sanitize invoice_url to prevent Stored XSS
        if not str(invoice_url).lower().startswith(("http://", "https://")):
            invoice_url = ""
            
    if is_client_view and invoice_url:
        raw_color = site.get("brand_color") or site.get("brandColor")
        if not raw_color or not validate_brand_color(str(raw_color)):
            raw_color = "hsl(38 92% 50%)"
        brand_color = esc(raw_color)
        invoice_btn_html = f"""
  <div class="epa-container" style="text-align: center; margin-top: 2rem; margin-bottom: 2rem;">
    <a href="{esc(invoice_url)}" target="_blank" rel="noopener noreferrer" class="epa-cta" style="background-color: {brand_color}; color: hsl(220 49% 8%); padding: 1rem 2rem; font-size: 1rem; border-radius: 0.5rem; display: inline-flex; align-items: center; gap: 0.5rem; text-decoration: none; font-weight: 600;">
      Pay Invoice &rarr;
    </a>
  </div>"""

    # Suppress brand attribution on white-labeled sites
    white_label = site.get("white_label") or site.get("whiteLabel") or False
    footer_html = ""
    if not white_label:
        footer_html = """
  <footer class="epa-footer">
    Built with <a href="https://expoproxy.ai" style="color: hsl(38 92% 50%); text-decoration: none;">Expo Proxy AI</a>
  </footer>"""

    og_image_meta = f'<meta property="og:image" content="{og_img}" />' if og_img else ''
    twitter_card_type = 'summary_large_image' if og_img else 'summary'
    twitter_image_meta = f'<meta name="twitter:image" content="{og_img}" />' if og_img else ''

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title}</title>
  <meta name="description" content="{desc}" />
  <meta property="og:title" content="{title}" />
  <meta property="og:description" content="{desc}" />
  <meta property="og:type" content="website" />
  <meta property="og:url" content="https://expoproxy.ai/s/{slug}" />
  {og_image_meta}
  <meta name="twitter:card" content="{twitter_card_type}" />
  <meta name="twitter:title" content="{title}" />
  <meta name="twitter:description" content="{desc}" />
  {twitter_image_meta}
  <link rel="canonical" href="https://expoproxy.ai/s/{slug}" />
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: system-ui, -apple-system, sans-serif; background: hsl(220 49% 8%); color: hsl(210 40% 96%); line-height: 1.6; }}
    .epa-container {{ max-width: 56rem; margin: 0 auto; padding: 0 2rem; }}
    .epa-hero {{ padding: 5rem 2rem; text-align: center; border-bottom: 1px solid hsl(217 33% 17%); }}
    .epa-h1 {{ font-size: clamp(2rem, 5vw, 3.5rem); font-weight: 700; margin-bottom: 1rem; }}
    .epa-h2 {{ font-size: clamp(1.5rem, 3vw, 2rem); font-weight: 700; margin-bottom: 0.75rem; }}
    .epa-sub {{ font-size: 1.125rem; color: hsl(215 20% 65%); max-width: 36rem; margin: 0 auto 1.5rem; }}
    .epa-body {{ color: hsl(215 20% 65%); max-width: 42rem; line-height: 1.75; }}
    .epa-cta {{ display: inline-block; padding: 0.75rem 1.5rem; border-radius: 0.375rem; font-weight: 600; font-size: 0.875rem; text-decoration: none; margin-top: 1rem; color: hsl(220 49% 8%) !important; }}
    .epa-about-sub {{ font-weight: 500; margin-bottom: 1rem; color: hsl(210 40% 98%); }}
    .epa-submit-btn {{ color: hsl(220 49% 8%) !important; }}
    .epa-section {{ padding: 4rem 2rem; border-bottom: 1px solid hsl(217 33% 17%); }}
    .epa-section-alt {{ background: hsl(222 47% 11%); }}
    .epa-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-top: 2rem; }}
    .epa-card {{ background: hsl(220 49% 8%); border: 1px solid hsl(217 33% 17%); border-radius: 0.5rem; padding: 1.25rem; font-size: 0.875rem; }}
    .epa-form-field {{ width: 100%; background: hsl(220 49% 8%); border: 1px solid hsl(217 33% 17%); border-radius: 0.375rem; padding: 0.5rem 0.75rem; color: hsl(215 20% 65%); margin-bottom: 0.75rem; font-size: 0.875rem; display: block; }}
    .epa-footer {{ padding: 2rem; text-align: center; color: hsl(215 19% 35%); font-size: 0.75rem; border-top: 1px solid hsl(217 33% 17%); margin-top: 2rem; }}
  </style>
  {analytics_head_html}
</head>
<body>
  <div id="site-root">{body_html}</div>
  {invoice_btn_html}
  {footer_html}
  {consent_banner_html}
</body>
</html>"""

@router.get("/ssr/{slug}", response_class=HTMLResponse)
async def render_ssr_page(
    slug: str,
    ga_id: Optional[str] = Query(None, alias="ga_id"),
    plausible_domain: Optional[str] = Query(None, alias="plausible_domain"),
    db = Depends(get_db),
    user = Depends(get_optional_user)
):
    """
    SSR Endpoint to fetch site data from Supabase and render static HTML.
    Publicly accessible but checks draft visibility auth permissions.
    """
    # 1. Fetch site
    site_res = db.table("sites").select("*").eq("slug", slug).execute()
    if not site_res.data:
        raise HTTPException(status_code=404, detail="Site not found")
    site = site_res.data[0]
    
    # 2. Enforce draft permissions (owner or admin only)
    if site.get("status") != "published":
        if not user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Draft sites can only be previewed by the owner or an admin."
            )
        user_id = user.get("sub")
        owner_id = site.get("owner_id")
        is_owner = owner_id is not None and str(owner_id) == user_id
        
        # Check admin role in user_roles
        import uuid
        is_admin = False
        if user_id and isinstance(user_id, str):
            try:
                uuid.UUID(user_id)
                roles_res = db.table("user_roles").select("role").eq("user_id", user_id).execute()
                is_admin = any(r.get("role") == "admin" for r in roles_res.data)
            except ValueError:
                pass
            
        if not (is_owner or is_admin):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Draft sites can only be previewed by the owner or an admin."
            )

    # 3. Fetch layout sections ordered by position
    sections_res = db.table("site_sections").select("*").eq("site_id", site.get("id")).order("position").execute()
    sections = sections_res.data
    
    # 4. Map sections to PublicSiteSection contract
    site["sections"] = [
        (sec.get("content") or {}) | {"kind": sec.get("kind")}
        for sec in sections
    ]
    
    # 5. Overrides
    if ga_id is not None:
        site["ga_measurement_id"] = ga_id
    if plausible_domain is not None:
        site["plausible_domain"] = plausible_domain
        
    return HTMLResponse(content=render_site_html(site), status_code=200)
