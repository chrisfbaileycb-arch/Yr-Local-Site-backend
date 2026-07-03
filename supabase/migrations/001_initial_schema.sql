-- ============================================================
--  001_initial_schema.sql
--  Expo Proxy AI — Initial Schema (Types, Tables, Indexes, Grants & STUB has_role)
-- ============================================================

-- ─── ENUMs ───────────────────────────────────────────────────
CREATE TYPE public.app_role AS ENUM ('admin', 'user');

CREATE TYPE public.site_status AS ENUM ('draft', 'published');

CREATE TYPE public.section_kind AS ENUM (
  'hero', 'about', 'services', 'gallery',
  'testimonials', 'contact', 'cta', 'footer'
);

-- ─── profiles ────────────────────────────────────────────────
CREATE TABLE public.profiles (
  id          uuid        PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
  display_name text,
  avatar_url  text,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- ─── user_roles ──────────────────────────────────────────────
CREATE TABLE public.user_roles (
  id      bigint    GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id uuid      NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
  role    public.app_role NOT NULL DEFAULT 'user',
  UNIQUE (user_id, role)
);

-- ─── STUB has_role() ──────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.has_role(
  _user_id uuid,
  _role    public.app_role
)
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
  SELECT false;
$$;

-- ─── sites ───────────────────────────────────────────────────
CREATE TABLE public.sites (
  id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id        uuid        NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
  name            text        NOT NULL,
  slug            text        NOT NULL UNIQUE,
  status          public.site_status NOT NULL DEFAULT 'draft',
  seo_title       text,
  seo_description text,
  og_image_url    text,
  brand_color     text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

-- ─── site_sections ───────────────────────────────────────────
CREATE TABLE public.site_sections (
  id         uuid              PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id    uuid              NOT NULL REFERENCES public.sites (id) ON DELETE CASCADE,
  kind       public.section_kind NOT NULL,
  position   integer           NOT NULL DEFAULT 0,
  content    jsonb             NOT NULL DEFAULT '{}',
  updated_at timestamptz       NOT NULL DEFAULT now()
);

-- ─── leads ───────────────────────────────────────────────────
CREATE TABLE public.leads (
  id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  name         text        NOT NULL,
  email        text        NOT NULL,
  project_type text,
  budget       text,
  message      text        NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  assigned_to  uuid        REFERENCES auth.users (id) ON DELETE SET NULL
);

-- ─── INDEXES for performance optimization ────────────────────
CREATE INDEX idx_profiles_created_at ON public.profiles(created_at);
CREATE INDEX idx_user_roles_user_id ON public.user_roles(user_id);
CREATE INDEX idx_sites_owner_id ON public.sites(owner_id);
CREATE INDEX idx_site_sections_site_id ON public.site_sections(site_id);
CREATE INDEX idx_site_sections_position ON public.site_sections(position);
CREATE INDEX idx_leads_created_at ON public.leads(created_at);
CREATE INDEX idx_leads_assigned_to ON public.leads(assigned_to);

-- ─── GRANTS ──────────────────────────────────────────────────
-- Profiles
GRANT SELECT, UPDATE ON public.profiles TO authenticated;
GRANT INSERT ON public.profiles TO service_role;

-- User Roles
-- SQL grants for INSERT/DELETE on public.user_roles are granted to the authenticated role
-- because admins connect under the authenticated role and RLS gates access strictly to admins.
-- Postgres checks SQL permissions before executing RLS policies.
GRANT SELECT, INSERT, DELETE ON public.user_roles TO authenticated;
GRANT INSERT, DELETE ON public.user_roles TO service_role;

-- Sites
GRANT SELECT, INSERT, UPDATE, DELETE ON public.sites TO authenticated;
GRANT SELECT ON public.sites TO anon;

-- Site Sections
GRANT SELECT, INSERT, UPDATE, DELETE ON public.site_sections TO authenticated;
GRANT SELECT ON public.site_sections TO anon;

-- Leads
GRANT INSERT ON public.leads TO anon;
GRANT INSERT ON public.leads TO authenticated;
GRANT SELECT, UPDATE ON public.leads TO authenticated;

-- ─── products ────────────────────────────────────────────────
CREATE TABLE public.products (
  id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id          uuid        NOT NULL REFERENCES public.sites (id) ON DELETE CASCADE,
  name             text        NOT NULL,
  description      text        NOT NULL,
  price_label      text        NOT NULL,
  payment_link_url text        NOT NULL,
  active           boolean     NOT NULL DEFAULT true,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT name_not_empty CHECK (char_length(trim(name)) > 0),
  CONSTRAINT price_label_not_empty CHECK (char_length(trim(price_label)) > 0),
  CONSTRAINT payment_link_valid_url CHECK (payment_link_url ~* '^https?://[^\s/$.?#].[^\s]*$')
);

-- ─── revisions ───────────────────────────────────────────────
CREATE TABLE public.revisions (
  id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id           uuid        NOT NULL REFERENCES public.sites (id) ON DELETE CASCADE,
  revision_number   integer     NOT NULL,
  site_snapshot     jsonb       NOT NULL DEFAULT '{}'::jsonb,
  sections_snapshot jsonb       NOT NULL DEFAULT '[]'::jsonb,
  created_at        timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT check_revision_number CHECK (revision_number > 0),
  CONSTRAINT unique_site_revision UNIQUE (site_id, revision_number)
);

-- ─── audit_results ───────────────────────────────────────────
CREATE TABLE public.audit_results (
  id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  site_id      uuid        REFERENCES public.sites (id) ON DELETE CASCADE,
  target_url   text        NOT NULL,
  score        integer     NOT NULL CHECK (score >= 0 AND score <= 100),
  status       text        NOT NULL DEFAULT 'complete' CHECK (status IN ('complete', 'failed')),
  findings     jsonb       NOT NULL DEFAULT '[]'::jsonb,
  created_at   timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT target_url_valid CHECK (target_url ~* '^https?://[^\s/$.?#].[^\s]*$')
);

-- ─── INDEXES ─────────────────────────────────────────────────
CREATE INDEX idx_products_site_id ON public.products(site_id);
CREATE INDEX idx_products_active_site_id ON public.products(site_id) WHERE active = true;
CREATE INDEX idx_revisions_site_id ON public.revisions(site_id);
CREATE INDEX idx_revisions_number ON public.revisions(site_id, revision_number DESC);
CREATE INDEX idx_audit_results_site_id ON public.audit_results(site_id);
CREATE INDEX idx_audit_results_created_at ON public.audit_results(created_at DESC);

-- ─── GRANTS ──────────────────────────────────────────────────
GRANT SELECT, INSERT, UPDATE, DELETE ON public.products TO authenticated;
GRANT SELECT ON public.products TO anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.products TO service_role;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.revisions TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.revisions TO service_role;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.audit_results TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.audit_results TO service_role;
