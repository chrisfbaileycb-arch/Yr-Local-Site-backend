-- ============================================================
--  004_client_and_custom_block.sql
--  Yr Local — Client management fields, custom_html block,
--             and billing classification on revisions
-- ============================================================
--
--  HOW TO RUN:
--  Paste this entire file into Supabase Dashboard → SQL Editor → Run
--
--  BOOTSTRAP (run separately after first sign-in):
--  UPDATE public.user_roles
--  SET    role = 'admin'
--  WHERE  user_id = 'YOUR-SUPABASE-USER-UUID';
-- ============================================================

-- ─── Extend section_kind enum ────────────────────────────────
-- Adds 'custom_html' as a new section type — the escape hatch
-- for bespoke $3,000+ work that the standard blocks can't handle.
-- ADD VALUE is safe to run multiple times (IF NOT EXISTS guard).
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_enum
    WHERE enumlabel = 'custom_html'
      AND enumtypid = 'public.section_kind'::regtype
  ) THEN
    ALTER TYPE public.section_kind ADD VALUE 'custom_html';
  END IF;
END $$;

-- ─── Client management fields on sites ───────────────────────
-- Tracks the real-world client relationship alongside the site data.
ALTER TABLE public.sites
  ADD COLUMN IF NOT EXISTS client_name        text,
  ADD COLUMN IF NOT EXISTS client_email       text,
  ADD COLUMN IF NOT EXISTS monthly_rate       numeric(8,2),
  ADD COLUMN IF NOT EXISTS client_notes       text,
  ADD COLUMN IF NOT EXISTS netlify_site_id    text,
  ADD COLUMN IF NOT EXISTS deployment_url     text,
  ADD COLUMN IF NOT EXISTS deployment_status  text NOT NULL DEFAULT 'not_deployed'
    CONSTRAINT sites_deployment_status_check
    CHECK (deployment_status IN ('not_deployed', 'deploying', 'live', 'failed')),
  ADD COLUMN IF NOT EXISTS last_deployed_at   timestamptz;

-- Note: custom_domain already exists on sites from the SiteData interface.
-- If it doesn't exist in your DB yet, uncomment the line below:
-- ALTER TABLE public.sites ADD COLUMN IF NOT EXISTS custom_domain text;

-- ─── Billing classification on revisions ─────────────────────
-- change_type distinguishes maintenance updates (covered by $65/mo)
-- from structural revisions (billable project work).
-- structural = added/removed/reordered sections (component tree change)
-- maintenance = content edits within existing sections (text, images)
ALTER TABLE public.revisions
  ADD COLUMN IF NOT EXISTS change_type text NOT NULL DEFAULT 'structural'
    CONSTRAINT revisions_change_type_check
    CHECK (change_type IN ('structural', 'maintenance')),
  ADD COLUMN IF NOT EXISTS change_summary text;

-- ─── Indexes ─────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_sites_deployment_status
  ON public.sites(deployment_status);

CREATE INDEX IF NOT EXISTS idx_sites_netlify_site_id
  ON public.sites(netlify_site_id)
  WHERE netlify_site_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_revisions_change_type
  ON public.revisions(site_id, change_type);

-- ─── Column comments ─────────────────────────────────────────
COMMENT ON COLUMN public.sites.client_name        IS 'Client business or personal name';
COMMENT ON COLUMN public.sites.client_email       IS 'Client primary contact email';
COMMENT ON COLUMN public.sites.monthly_rate       IS 'Monthly maintenance fee charged to this client (USD)';
COMMENT ON COLUMN public.sites.client_notes       IS 'Internal notes about this client — never shown to client';
COMMENT ON COLUMN public.sites.netlify_site_id    IS 'Netlify site ID used for programmatic deploys';
COMMENT ON COLUMN public.sites.deployment_url     IS 'Live public URL of the deployed site';
COMMENT ON COLUMN public.sites.deployment_status  IS 'Current state: not_deployed | deploying | live | failed';
COMMENT ON COLUMN public.sites.last_deployed_at   IS 'Timestamp of last successful Netlify deploy';
COMMENT ON COLUMN public.revisions.change_type    IS 'structural = billable project work; maintenance = covered by monthly fee';
COMMENT ON COLUMN public.revisions.change_summary IS 'Short human-readable description of what changed';
