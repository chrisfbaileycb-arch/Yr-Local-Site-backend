-- ============================================================
--  002_rls_policies.sql
--  Expo Proxy AI — Row Level Security (RLS) & Policies
-- ============================================================

-- ─── ENABLE RLS ──────────────────────────────────────────────
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sites ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.site_sections ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.leads ENABLE ROW LEVEL SECURITY;

-- ─── profiles POLICIES ───────────────────────────────────────
-- Owner may read + update their own full row
CREATE POLICY "profiles_self_crud" ON public.profiles
  FOR ALL
  TO authenticated
  USING  (auth.uid() = id)
  WITH CHECK (auth.uid() = id);

-- Every authenticated user may read any profile (needed for display_name lookups)
CREATE POLICY "profiles_authenticated_read" ON public.profiles
  FOR SELECT
  TO authenticated
  USING (true);

-- ─── user_roles POLICIES ─────────────────────────────────────
-- Users may only read their own role rows
CREATE POLICY "user_roles_self_read" ON public.user_roles
  FOR SELECT
  TO authenticated
  USING (auth.uid() = user_id);

-- Admins can do everything on user_roles
CREATE POLICY "user_roles_admin_all" ON public.user_roles
  FOR ALL
  TO authenticated
  USING (public.has_role(auth.uid(), 'admin'))
  WITH CHECK (public.has_role(auth.uid(), 'admin'));

-- ─── sites POLICIES ──────────────────────────────────────────
-- Owner can do everything on their own sites
CREATE POLICY "sites_owner_crud" ON public.sites
  FOR ALL
  TO authenticated
  USING  (auth.uid() = owner_id)
  WITH CHECK (auth.uid() = owner_id);

-- Admins can do everything on all sites
CREATE POLICY "sites_admin_all" ON public.sites
  FOR ALL
  TO authenticated
  USING (public.has_role(auth.uid(), 'admin'))
  WITH CHECK (public.has_role(auth.uid(), 'admin'));

-- Anyone can read published sites
CREATE POLICY "sites_public_read" ON public.sites
  FOR SELECT
  TO anon, authenticated
  USING (status = 'published');

-- ─── site_sections POLICIES ──────────────────────────────────
-- Owner and Admins can CRUD sections for their own sites.
-- Fixed the admin check bug in the WITH CHECK clause.
CREATE POLICY "site_sections_owner_crud" ON public.site_sections
  FOR ALL
  TO authenticated
  USING (
    EXISTS (
      SELECT 1 FROM public.sites
      WHERE  id       = site_sections.site_id
      AND    (owner_id = auth.uid() OR public.has_role(auth.uid(), 'admin'))
    )
  )
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM public.sites
      WHERE  id       = site_sections.site_id
      AND    (owner_id = auth.uid() OR public.has_role(auth.uid(), 'admin'))
    )
  );

-- Anyone can read sections belonging to published sites
CREATE POLICY "site_sections_public_read" ON public.site_sections
  FOR SELECT
  TO anon, authenticated
  USING (
    EXISTS (
      SELECT 1 FROM public.sites
      WHERE id = site_sections.site_id AND status = 'published'
    )
  );

-- ─── leads POLICIES ──────────────────────────────────────────
-- Anyone (including anon) may insert a lead
CREATE POLICY "leads_insert_open" ON public.leads
  FOR INSERT
  TO anon, authenticated
  WITH CHECK (true);

-- Only admins may read leads
CREATE POLICY "leads_admin_read" ON public.leads
  FOR SELECT
  TO authenticated
  USING (public.has_role(auth.uid(), 'admin'));

-- Only admins may update leads
CREATE POLICY "leads_admin_update" ON public.leads
  FOR UPDATE
  TO authenticated
  USING (public.has_role(auth.uid(), 'admin'))
  WITH CHECK (public.has_role(auth.uid(), 'admin'));

-- ─── ENABLE RLS ──────────────────────────────────────────────
ALTER TABLE public.products ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audit_results ENABLE ROW LEVEL SECURITY;

-- ─── products POLICIES ───────────────────────────────────────
CREATE POLICY "products_owner_crud" ON public.products
  FOR ALL
  TO authenticated
  USING (
    EXISTS (
      SELECT 1 FROM public.sites
      WHERE id = products.site_id AND owner_id = auth.uid()
    )
  )
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM public.sites
      WHERE id = products.site_id AND owner_id = auth.uid()
    )
  );

CREATE POLICY "products_admin_all" ON public.products
  FOR ALL
  TO authenticated
  USING (public.has_role(auth.uid(), 'admin'))
  WITH CHECK (public.has_role(auth.uid(), 'admin'));

CREATE POLICY "products_public_read" ON public.products
  FOR SELECT
  TO anon, authenticated
  USING (
    EXISTS (
      SELECT 1 FROM public.sites
      WHERE id = products.site_id AND status = 'published'
    )
  );

-- ─── revisions POLICIES ──────────────────────────────────────
CREATE POLICY "revisions_owner_select" ON public.revisions
  FOR SELECT
  TO authenticated
  USING (
    EXISTS (
      SELECT 1 FROM public.sites
      WHERE id = revisions.site_id AND owner_id = auth.uid()
    )
  );

CREATE POLICY "revisions_owner_insert" ON public.revisions
  FOR INSERT
  TO authenticated
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM public.sites
      WHERE id = revisions.site_id AND owner_id = auth.uid()
    )
  );

CREATE POLICY "revisions_admin_all" ON public.revisions
  FOR ALL
  TO authenticated
  USING (public.has_role(auth.uid(), 'admin'))
  WITH CHECK (public.has_role(auth.uid(), 'admin'));

-- ─── audit_results POLICIES ──────────────────────────────────
CREATE POLICY "audit_results_owner_select" ON public.audit_results
  FOR SELECT
  TO authenticated
  USING (
    EXISTS (
      SELECT 1 FROM public.sites
      WHERE id = audit_results.site_id AND owner_id = auth.uid()
    )
  );

CREATE POLICY "audit_results_admin_all" ON public.audit_results
  FOR ALL
  TO authenticated
  USING (public.has_role(auth.uid(), 'admin'))
  WITH CHECK (public.has_role(auth.uid(), 'admin'));
