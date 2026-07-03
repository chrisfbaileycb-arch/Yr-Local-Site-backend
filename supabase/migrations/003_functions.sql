-- ============================================================
--  003_functions.sql
--  Expo Proxy AI — Database Functions, Triggers & Auth Hooks
-- ============================================================

-- ─── has_role() — SECURITY DEFINER helper ────────────────────
-- Called from RLS policies; search_path locked to prevent injection.
CREATE OR REPLACE FUNCTION public.has_role(
  _user_id uuid,
  _role    public.app_role
)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT EXISTS (
    SELECT 1
    FROM   public.user_roles
    WHERE  user_id = _user_id
    AND    role    = _role
  );
$$;

-- ─── Trigger: auto-provision profile + default role ──────────
-- SECURITY DEFINER so it can write to public schema from auth trigger context.
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  INSERT INTO public.profiles (id, display_name)
  VALUES (
    NEW.id,
    COALESCE(
      NEW.raw_user_meta_data ->> 'full_name',
      NEW.raw_user_meta_data ->> 'name',
      NEW.email
    )
  )
  ON CONFLICT (id) DO NOTHING;

  INSERT INTO public.user_roles (user_id, role)
  VALUES (NEW.id, 'user')
  ON CONFLICT (user_id, role) DO NOTHING;

  RETURN NEW;
END;
$$;

-- Bind the trigger function to the auth.users table
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;

CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW
  EXECUTE FUNCTION public.handle_new_user();

-- ─── Trigger: prune revisions to keep max 20 per site ──────────
CREATE OR REPLACE FUNCTION public.prune_revisions()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  DELETE FROM public.revisions
  WHERE site_id = NEW.site_id
    AND id NOT IN (
      SELECT id
      FROM public.revisions
      WHERE site_id = NEW.site_id
      ORDER BY revision_number DESC
      LIMIT 20
    );
  RETURN NEW;
END;
$$;

-- Bind the trigger function to public.revisions
DROP TRIGGER IF EXISTS on_revision_inserted ON public.revisions;

CREATE TRIGGER on_revision_inserted
  AFTER INSERT ON public.revisions
  FOR EACH ROW
  EXECUTE FUNCTION public.prune_revisions();
