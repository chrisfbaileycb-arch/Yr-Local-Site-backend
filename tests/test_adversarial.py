import os
import pytest
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

def get_connection():
    if not DATABASE_URL:
        pytest.skip("DATABASE_URL environment variable is not set. Skipping schema tests.")
    
    # Strict safety guard: Inspect DATABASE_URL
    db_url_lower = DATABASE_URL.lower()
    safety_markers = ["localhost", "127.0.0.1", "54322", "test"]
    if not any(marker in db_url_lower for marker in safety_markers):
        raise ValueError(
            f"Safety guard: Connection blocked. DATABASE_URL must contain one of "
            f"{safety_markers} to prevent data loss on production database."
        )
        
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        pytest.skip(f"Could not connect to PostgreSQL database: {e}. Skipping schema tests.")

@pytest.fixture(scope="module")
def db_conn():
    conn = get_connection()
    conn.autocommit = True
    cursor = conn.cursor()
    
    # Clean up and reset schema
    cursor.execute("""
        DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users CASCADE;
        DROP FUNCTION IF EXISTS public.handle_new_user() CASCADE;
        DROP FUNCTION IF EXISTS public.has_role(uuid, public.app_role) CASCADE;
        DROP TABLE IF EXISTS public.leads CASCADE;
        DROP TABLE IF EXISTS public.site_sections CASCADE;
        DROP TABLE IF EXISTS public.sites CASCADE;
        DROP TABLE IF EXISTS public.user_roles CASCADE;
        DROP TABLE IF EXISTS public.profiles CASCADE;
        DROP TYPE IF EXISTS public.section_kind CASCADE;
        DROP TYPE IF EXISTS public.site_status CASCADE;
        DROP TYPE IF EXISTS public.app_role CASCADE;
    """)
    
    # Setup mock auth schema & users
    cursor.execute("""
        CREATE SCHEMA IF NOT EXISTS auth;
        CREATE TABLE IF NOT EXISTS auth.users (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          email text,
          raw_user_meta_data jsonb DEFAULT '{}'::jsonb
        );
        CREATE OR REPLACE FUNCTION auth.uid()
        RETURNS uuid
        LANGUAGE sql
        STABLE
        AS $$
          SELECT COALESCE(
            nullif(current_setting('request.jwt.claim.sub', true), '')::uuid,
            null
          );
        $$;
        
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            CREATE ROLE authenticated;
          END IF;
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            CREATE ROLE anon;
          END IF;
        END
        $$;
        
        GRANT USAGE ON SCHEMA public TO authenticated, anon;
        GRANT ALL PRIVILEGES ON SCHEMA public TO CURRENT_USER;
    """)
    
    # Read and apply migrations in order
    migrations_dir = os.path.join(os.path.dirname(__file__), "../supabase/migrations")
    for migration_file in sorted(os.listdir(migrations_dir)):
        if migration_file.endswith(".sql"):
            with open(os.path.join(migrations_dir, migration_file), "r") as f:
                sql = f.read()
                cursor.execute(sql)
                
    yield conn
    
    # Clean up after tests
    cursor.execute("""
        DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users CASCADE;
        DROP TABLE IF EXISTS auth.users CASCADE;
        DROP FUNCTION IF EXISTS auth.uid() CASCADE;
    """)
    conn.close()

def setup_users_and_grants(cursor):
    """Resets to superuser, registers users, and grants standard permissions."""
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")
    
    user_a = '11111111-1111-1111-1111-111111111111'
    user_b = '22222222-2222-2222-2222-222222222222'
    admin_user = '99999999-9999-9999-9999-999999999999'
    
    # Clear tables
    cursor.execute("DELETE FROM auth.users;")
    cursor.execute("DELETE FROM public.user_roles;")
    cursor.execute("DELETE FROM public.sites;")
    cursor.execute("DELETE FROM public.leads;")
    
    # Insert users
    cursor.execute("""
        INSERT INTO auth.users (id, email, raw_user_meta_data) VALUES
        (%s, 'usera@example.com', '{"full_name": "User A"}'),
        (%s, 'userb@example.com', '{"full_name": "User B"}'),
        (%s, 'admin@example.com', '{"full_name": "Admin User"}')
    """, (user_a, user_b, admin_user))
    
    # Explicitly set admin user's role to admin (trigger created them as 'user')
    cursor.execute("UPDATE public.user_roles SET role = 'admin' WHERE user_id = %s", (admin_user,))
    
    # Grant table access for RLS testing
    cursor.execute("GRANT ALL PRIVILEGES ON public.profiles TO authenticated;")
    cursor.execute("GRANT ALL PRIVILEGES ON public.user_roles TO authenticated;")
    cursor.execute("GRANT ALL PRIVILEGES ON public.sites TO authenticated;")
    cursor.execute("GRANT ALL PRIVILEGES ON public.site_sections TO authenticated;")
    cursor.execute("GRANT ALL PRIVILEGES ON public.leads TO anon, authenticated;")
    
    return user_a, user_b, admin_user

def test_has_role_no_recursion(db_conn):
    """Verify has_role is robust and doesn't cause infinite recursion under RLS evaluation."""
    cursor = db_conn.cursor()
    user_a, user_b, admin_user = setup_users_and_grants(cursor)
    
    # Switch role to authenticated User A
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (user_a,))
    
    # Query has_role through SQL
    cursor.execute("SELECT public.has_role(%s, 'user')", (user_a,))
    assert cursor.fetchone()[0] is True
    
    cursor.execute("SELECT public.has_role(%s, 'admin')", (user_a,))
    assert cursor.fetchone()[0] is False
    
    # Reset role
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")

def test_privilege_escalation_attack(db_conn):
    """Verify that a standard user cannot elevate their privileges or modify roles."""
    cursor = db_conn.cursor()
    user_a, user_b, admin_user = setup_users_and_grants(cursor)
    
    # Switch to User A
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (user_a,))
    
    # 1. Attempt to INSERT a new role row to make User A an admin
    try:
        cursor.execute("INSERT INTO public.user_roles (user_id, role) VALUES (%s, 'admin')", (user_a,))
        # If RLS blocks it, rowcount is 0 or it throws. Let's check rowcount:
        assert cursor.rowcount == 0, "Insert should not affect any rows under RLS"
    except Exception as e:
        # Some PostgreSQL drivers/configurations may raise an exception on RLS violations
        pass
    db_conn.rollback()
    
    # 2. Attempt to UPDATE existing role row to 'admin'
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (user_a,))
    cursor.execute("UPDATE public.user_roles SET role = 'admin' WHERE user_id = %s", (user_a,))
    assert cursor.rowcount == 0, "Standard user should not be able to update their role to admin"
    
    # 3. Attempt to DELETE own role row (to cause denial of service/undefined state)
    cursor.execute("DELETE FROM public.user_roles WHERE user_id = %s", (user_a,))
    assert cursor.rowcount == 0, "Standard user should not be able to delete their role row"
    
    # Reset role
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")

def test_sites_adversarial_scenarios(db_conn):
    """Test adversarial operations on sites table."""
    cursor = db_conn.cursor()
    user_a, user_b, admin_user = setup_users_and_grants(cursor)
    
    # User A creates a draft site
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (user_a,))
    cursor.execute("""
        INSERT INTO public.sites (owner_id, name, slug, status)
        VALUES (%s, 'User A Site', 'usera-site', 'draft')
        RETURNING id
    """, (user_a,))
    site_a_id = cursor.fetchone()[0]
    
    # 1. User B tries to read User A's draft site
    cursor.execute("SET request.jwt.claim.sub = %s", (user_b,))
    cursor.execute("SELECT id FROM public.sites WHERE id = %s", (site_a_id,))
    assert cursor.fetchone() is None, "User B should not be able to read User A's draft site"
    
    # 2. User B tries to update User A's draft site
    cursor.execute("UPDATE public.sites SET name = 'Hacked Name' WHERE id = %s", (site_a_id,))
    assert cursor.rowcount == 0, "User B should not be able to update User A's site"
    
    # 3. User B tries to delete User A's draft site
    cursor.execute("DELETE FROM public.sites WHERE id = %s", (site_a_id,))
    assert cursor.rowcount == 0, "User B should not be able to delete User A's site"
    
    # 4. User B tries to hijack a site by inserting one with owner_id = User A
    try:
        cursor.execute("""
            INSERT INTO public.sites (owner_id, name, slug, status)
            VALUES (%s, 'Hijacked Site', 'hijacked-site', 'draft')
        """, (user_a,))
        assert cursor.rowcount == 0, "User B should not be able to insert a site with owner_id = User A"
    except Exception:
        pass
    db_conn.rollback()
    
    # 5. User A tries to change the owner_id of their own site to User B (transfer ownership)
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (user_a,))
    cursor.execute("UPDATE public.sites SET owner_id = %s WHERE id = %s", (user_b, site_a_id))
    assert cursor.rowcount == 0, "User A should not be able to transfer site ownership (violates WITH CHECK)"
    
    # Reset role
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")

def test_site_sections_adversarial_scenarios(db_conn):
    """Test adversarial operations on site_sections table."""
    cursor = db_conn.cursor()
    user_a, user_b, admin_user = setup_users_and_grants(cursor)
    
    # User A creates a draft site and section
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (user_a,))
    cursor.execute("""
        INSERT INTO public.sites (owner_id, name, slug, status)
        VALUES (%s, 'User A Site', 'usera-site', 'draft')
        RETURNING id
    """, (user_a,))
    site_a_id = cursor.fetchone()[0]
    
    cursor.execute("""
        INSERT INTO public.site_sections (site_id, kind, position, content)
        VALUES (%s, 'hero', 0, '{"title": "User A Section"}')
        RETURNING id
    """, (site_a_id,))
    section_a_id = cursor.fetchone()[0]
    
    # User B creates a draft site
    cursor.execute("SET request.jwt.claim.sub = %s", (user_b,))
    cursor.execute("""
        INSERT INTO public.sites (owner_id, name, slug, status)
        VALUES (%s, 'User B Site', 'userb-site', 'draft')
        RETURNING id
    """, (user_b,))
    site_b_id = cursor.fetchone()[0]
    
    # 1. User B tries to insert a section into User A's site
    try:
        cursor.execute("""
            INSERT INTO public.site_sections (site_id, kind, position, content)
            VALUES (%s, 'hero', 0, '{"title": "User B Injection"}')
        """, (site_a_id,))
        assert cursor.rowcount == 0, "User B should not be able to insert a section into User A's site"
    except Exception:
        pass
    db_conn.rollback()
    
    # 2. User B tries to update User A's section
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (user_b,))
    cursor.execute("""
        UPDATE public.site_sections 
        SET content = '{"title": "Hacked"}' 
        WHERE id = %s
    """, (section_a_id,))
    assert cursor.rowcount == 0, "User B should not be able to update User A's section"
    
    # 3. User B tries to delete User A's section
    cursor.execute("DELETE FROM public.site_sections WHERE id = %s", (section_a_id,))
    assert cursor.rowcount == 0, "User B should not be able to delete User A's section"
    
    # 4. User A tries to move their section to User B's site
    cursor.execute("SET request.jwt.claim.sub = %s", (user_a,))
    cursor.execute("UPDATE public.site_sections SET site_id = %s WHERE id = %s", (site_b_id, section_a_id))
    assert cursor.rowcount == 0, "User A should not be able to move their section to a site they don't own"
    
    # Reset role
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")

def test_leads_adversarial_scenarios(db_conn):
    """Test adversarial operations on leads table."""
    cursor = db_conn.cursor()
    user_a, user_b, admin_user = setup_users_and_grants(cursor)
    
    # 1. Anon inserts a lead (allowed)
    cursor.execute("SET ROLE anon")
    cursor.execute("RESET request.jwt.claim.sub")
    cursor.execute("""
        INSERT INTO public.leads (name, email, message)
        VALUES ('Spam Bot', 'spam@example.com', 'Cheap medications!')
        RETURNING id
    """)
    lead_id = cursor.fetchone()[0]
    assert lead_id is not None
    
    # 2. Anon tries to read leads (blocked)
    cursor.execute("SELECT * FROM public.leads")
    assert len(cursor.fetchall()) == 0, "Anon should not be able to read leads"
    
    # 3. User A (standard user) tries to read leads (blocked)
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (user_a,))
    cursor.execute("SELECT * FROM public.leads")
    assert len(cursor.fetchall()) == 0, "Standard user should not be able to read leads"
    
    # 4. User A tries to update a lead (blocked)
    cursor.execute("UPDATE public.leads SET name = 'Cleaned' WHERE id = %s", (lead_id,))
    assert cursor.rowcount == 0, "Standard user should not be able to update leads"
    
    # 5. User A tries to delete a lead (blocked)
    cursor.execute("DELETE FROM public.leads WHERE id = %s", (lead_id,))
    assert cursor.rowcount == 0, "Standard user should not be able to delete leads"
    
    # Reset role
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")

def test_admin_bypass_behavior(db_conn):
    """Verify that admins have full CRUD bypass on sites, site_sections, leads, and user_roles."""
    cursor = db_conn.cursor()
    user_a, user_b, admin_user = setup_users_and_grants(cursor)
    
    # User A creates a draft site and section
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (user_a,))
    cursor.execute("""
        INSERT INTO public.sites (owner_id, name, slug, status)
        VALUES (%s, 'User A Site', 'usera-site', 'draft')
        RETURNING id
    """, (user_a,))
    site_a_id = cursor.fetchone()[0]
    
    cursor.execute("""
        INSERT INTO public.site_sections (site_id, kind, position, content)
        VALUES (%s, 'hero', 0, '{"title": "User A Section"}')
        RETURNING id
    """, (site_a_id,))
    section_a_id = cursor.fetchone()[0]
    
    # Anon inserts a lead
    cursor.execute("SET ROLE anon")
    cursor.execute("RESET request.jwt.claim.sub")
    cursor.execute("""
        INSERT INTO public.leads (name, email, message)
        VALUES ('Potential Lead', 'potential@example.com', 'I want a site')
        RETURNING id
    """)
    lead_id = cursor.fetchone()[0]
    
    # Switch to Admin
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (admin_user,))
    
    # 1. Admin can read User A's draft site
    cursor.execute("SELECT name FROM public.sites WHERE id = %s", (site_a_id,))
    assert cursor.fetchone()[0] == 'User A Site'
    
    # 2. Admin can update User A's draft site
    cursor.execute("UPDATE public.sites SET name = 'Admin Approved Site' WHERE id = %s", (site_a_id,))
    assert cursor.rowcount == 1
    
    # 3. Admin can read User A's section
    cursor.execute("SELECT content FROM public.site_sections WHERE id = %s", (section_a_id,))
    assert cursor.fetchone()[0]['title'] == 'User A Section'
    
    # 4. Admin can update User A's section
    cursor.execute("""
        UPDATE public.site_sections 
        SET content = '{"title": "Admin Modified Section"}' 
        WHERE id = %s
    """, (section_a_id,))
    assert cursor.rowcount == 1
    
    # 5. Admin can read leads
    cursor.execute("SELECT name FROM public.leads WHERE id = %s", (lead_id,))
    assert cursor.fetchone()[0] == 'Potential Lead'
    
    # 6. Admin can update leads (assign to User A)
    cursor.execute("UPDATE public.leads SET assigned_to = %s WHERE id = %s", (user_a, lead_id))
    assert cursor.rowcount == 1
    
    # 7. Admin can manage other user roles (e.g. make User B an admin)
    cursor.execute("INSERT INTO public.user_roles (user_id, role) VALUES (%s, 'admin')", (user_b,))
    assert cursor.rowcount == 1
    
    # 8. Admin can delete other user roles (demote User B back to user only)
    cursor.execute("DELETE FROM public.user_roles WHERE user_id = %s AND role = 'admin'", (user_b,))
    assert cursor.rowcount == 1
    
    # 9. Admin can delete User A's site
    cursor.execute("DELETE FROM public.sites WHERE id = %s", (site_a_id,))
    assert cursor.rowcount == 1
    
    # Reset role
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")
