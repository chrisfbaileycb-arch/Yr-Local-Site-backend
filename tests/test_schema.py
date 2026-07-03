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
    
    # 1. Clean up existing schema/tables/types in public
    cursor.execute("""
        DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users CASCADE;
        DROP FUNCTION IF EXISTS public.handle_new_user() CASCADE;
        DROP FUNCTION IF EXISTS public.has_role(uuid, public.app_role) CASCADE;
        DROP TABLE IF EXISTS public.audit_results CASCADE;
        DROP TABLE IF EXISTS public.revisions CASCADE;
        DROP TABLE IF EXISTS public.products CASCADE;
        DROP TABLE IF EXISTS public.leads CASCADE;
        DROP TABLE IF EXISTS public.site_sections CASCADE;
        DROP TABLE IF EXISTS public.sites CASCADE;
        DROP TABLE IF EXISTS public.user_roles CASCADE;
        DROP TABLE IF EXISTS public.profiles CASCADE;
        DROP TYPE IF EXISTS public.section_kind CASCADE;
        DROP TYPE IF EXISTS public.site_status CASCADE;
        DROP TYPE IF EXISTS public.app_role CASCADE;
    """)
    
    # 2. Setup mock auth schema & users
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
        
        -- Grant schema usage to authenticated and anon
        GRANT USAGE ON SCHEMA public TO authenticated, anon;
        GRANT ALL PRIVILEGES ON SCHEMA public TO CURRENT_USER;
    """)
    
    # 3. Read and apply migrations in order
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

@pytest.fixture(autouse=True)
def clean_db(db_conn):
    cursor = db_conn.cursor()
    cursor.execute("RESET ROLE;")
    cursor.execute("RESET request.jwt.claim.sub;")
    cursor.execute("DELETE FROM auth.users;")
    cursor.execute("DELETE FROM public.leads;")
    yield

def test_tables_exist(db_conn):
    cursor = db_conn.cursor()
    cursor.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public'
    """)
    tables = [r[0] for r in cursor.fetchall()]
    assert "profiles" in tables
    assert "user_roles" in tables
    assert "sites" in tables
    assert "site_sections" in tables
    assert "leads" in tables
    assert "products" in tables
    assert "revisions" in tables
    assert "audit_results" in tables

def test_user_provisioning_trigger(db_conn):
    cursor = db_conn.cursor(cursor_factory=RealDictCursor)
    user_id = 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11'
    email = 'user@example.com'
    full_name = 'John Doe'
    
    # Insert new auth user to fire trigger
    cursor.execute("""
        INSERT INTO auth.users (id, email, raw_user_meta_data)
        VALUES (%s, %s, %s)
    """, (user_id, email, f'{{"full_name": "{full_name}"}}'))
    
    # Verify profile created automatically
    cursor.execute("SELECT * FROM public.profiles WHERE id = %s", (user_id,))
    profile = cursor.fetchone()
    assert profile is not None
    assert profile['display_name'] == full_name
    
    # Verify user role created automatically
    cursor.execute("SELECT * FROM public.user_roles WHERE user_id = %s", (user_id,))
    user_role = cursor.fetchone()
    assert user_role is not None
    assert user_role['role'] == 'user'

def test_has_role_function(db_conn):
    cursor = db_conn.cursor()
    user_id = 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11'
    
    # Insert own user
    cursor.execute("""
        INSERT INTO auth.users (id, email, raw_user_meta_data)
        VALUES (%s, 'user_role_test@example.com', '{}')
    """, (user_id,))
    
    # Test has_role for 'user' (should be True)
    cursor.execute("SELECT public.has_role(%s, 'user')", (user_id,))
    assert cursor.fetchone()[0] is True
    
    # Test has_role for 'admin' (should be False)
    cursor.execute("SELECT public.has_role(%s, 'admin')", (user_id,))
    assert cursor.fetchone()[0] is False
    
    # Upgrade user to 'admin'
    cursor.execute("INSERT INTO public.user_roles (user_id, role) VALUES (%s, 'admin')", (user_id,))
    
    # Test has_role for 'admin' (should now be True)
    cursor.execute("SELECT public.has_role(%s, 'admin')", (user_id,))
    assert cursor.fetchone()[0] is True

def test_rls_profiles(db_conn):
    cursor = db_conn.cursor()
    user_a = 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11'
    user_b = 'b0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11'
    
    # Insert user A and User B to DB
    cursor.execute("""
        INSERT INTO auth.users (id, email, raw_user_meta_data)
        VALUES (%s, %s, %s)
    """, (user_a, 'user_a@example.com', '{"full_name": "John Doe"}'))
    
    cursor.execute("""
        INSERT INTO auth.users (id, email, raw_user_meta_data)
        VALUES (%s, %s, %s)
    """, (user_b, 'user_b@example.com', '{"full_name": "User B"}'))
    
    # Now simulate User A connection: SET ROLE authenticated, SET sub to user_a
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (user_a,))
    
    # User A should be able to read their own profile
    cursor.execute("SELECT display_name FROM public.profiles WHERE id = %s", (user_a,))
    assert cursor.fetchone()[0] == 'John Doe'
    
    # User A should be able to read User B's profile (profiles_authenticated_read policy: USING true)
    cursor.execute("SELECT display_name FROM public.profiles WHERE id = %s", (user_b,))
    assert cursor.fetchone()[0] == 'User B'
    
    # User A tries to update User B's profile - should fail (affect 0 rows because of USING/WITH CHECK)
    cursor.execute("UPDATE public.profiles SET display_name = 'Hacked' WHERE id = %s", (user_b,))
    assert cursor.rowcount == 0
    
    # Reset role to superuser to prepare next test
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")

def test_admin_rls_sections_bugfix(db_conn):
    cursor = db_conn.cursor()
    user_owner = 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11'
    user_admin = 'c0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11'
    
    # Insert Owner user
    cursor.execute("""
        INSERT INTO auth.users (id, email, raw_user_meta_data)
        VALUES (%s, %s, %s)
    """, (user_owner, 'owner@example.com', '{"full_name": "Owner User"}'))
    
    # Insert Admin user
    cursor.execute("""
        INSERT INTO auth.users (id, email, raw_user_meta_data)
        VALUES (%s, %s, %s)
    """, (user_admin, 'admin@example.com', '{"full_name": "Admin User"}'))
    cursor.execute("INSERT INTO public.user_roles (user_id, role) VALUES (%s, 'admin')", (user_admin,))
    
    # Create a site owned by user_owner
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (user_owner,))
    
    cursor.execute("""
        INSERT INTO public.sites (owner_id, name, slug)
        VALUES (%s, 'Test Site', 'test-site')
        RETURNING id
    """, (user_owner,))
    site_id = cursor.fetchone()[0]
    
    # Owner creates a section
    cursor.execute("""
        INSERT INTO public.site_sections (site_id, kind, position, content)
        VALUES (%s, 'hero', 0, '{"title": "Welcome"}')
        RETURNING id
    """, (site_id,))
    section_id = cursor.fetchone()[0]
    
    # Reset to prepare for Admin operation
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")
    
    # Now simulate Admin user connection
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (user_admin,))
    
    # Admin tries to update the site section (this would fail without our WITH CHECK fix!)
    cursor.execute("""
        UPDATE public.site_sections 
        SET content = '{"title": "Updated by Admin"}' 
        WHERE id = %s
    """, (section_id,))
    
    # Assert that the update succeeded (affected 1 row)
    assert cursor.rowcount == 1
    
    # Reset connection state
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")

def test_get_connection_safety_guard():
    import tests.test_schema as ts
    orig_url = ts.DATABASE_URL
    try:
        ts.DATABASE_URL = "postgresql://postgres:password@production-db.internal:5432/production"
        with pytest.raises(ValueError) as excinfo:
            ts.get_connection()
        assert "Safety guard" in str(excinfo.value)
    finally:
        ts.DATABASE_URL = orig_url

def test_leads_policies(db_conn):
    cursor = db_conn.cursor()
    
    # Setup users
    user_anon = 'anon'
    user_auth = 'd0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11'
    user_admin = 'e0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11'
    
    # Create Auth and Admin users
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")
    cursor.execute("""
        INSERT INTO auth.users (id, email)
        VALUES (%s, 'auth_user@example.com'), (%s, 'admin_user@example.com')
        ON CONFLICT (id) DO NOTHING
    """, (user_auth, user_admin))
    cursor.execute("UPDATE public.user_roles SET role = 'admin' WHERE user_id = %s", (user_admin,))
    
    # 1. Test Anon insert (anon/auth insert open)
    cursor.execute("SET ROLE anon")
    cursor.execute("RESET request.jwt.claim.sub")
    cursor.execute("""
        INSERT INTO public.leads (name, email, message)
        VALUES ('Anon Lead', 'anon@example.com', 'Help me')
        RETURNING id
    """)
    anon_lead_id = cursor.fetchone()[0]
    assert anon_lead_id is not None
    
    # 2. Test Auth insert (anon/auth insert open)
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (user_auth,))
    cursor.execute("""
        INSERT INTO public.leads (name, email, message)
        VALUES ('Auth Lead', 'auth@example.com', 'Help me too')
        RETURNING id
    """)
    auth_lead_id = cursor.fetchone()[0]
    assert auth_lead_id is not None
    
    # 3. Test non-admin select/update blocked
    cursor.execute("SELECT id FROM public.leads")
    leads = cursor.fetchall()
    assert len(leads) == 0
    
    cursor.execute("UPDATE public.leads SET name = 'Hacked' WHERE id = %s", (anon_lead_id,))
    assert cursor.rowcount == 0
    
    # 4. Test admin select/update works
    cursor.execute("SET request.jwt.claim.sub = %s", (user_admin,))
    cursor.execute("SELECT id FROM public.leads")
    leads = [r[0] for r in cursor.fetchall()]
    assert anon_lead_id in leads
    assert auth_lead_id in leads
    
    cursor.execute("UPDATE public.leads SET name = 'Updated Admin' WHERE id = %s", (anon_lead_id,))
    assert cursor.rowcount == 1
    
    # Reset
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")

def test_sites_public_select_policies(db_conn):
    cursor = db_conn.cursor()
    
    owner = '00000000-0000-0000-0000-000000000001'
    non_owner = '00000000-0000-0000-0000-000000000002'
    admin = '00000000-0000-0000-0000-000000000003'
    
    # Setup users
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")
    cursor.execute("""
        INSERT INTO auth.users (id, email) VALUES
        (%s, 'owner@example.com'),
        (%s, 'non_owner@example.com'),
        (%s, 'admin@example.com')
        ON CONFLICT (id) DO NOTHING
    """, (owner, non_owner, admin))
    cursor.execute("UPDATE public.user_roles SET role = 'admin' WHERE user_id = %s", (admin,))
    
    # Create a published site and a draft site owned by owner
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (owner,))
    
    cursor.execute("""
        INSERT INTO public.sites (owner_id, name, slug, status)
        VALUES (%s, 'Published Site', 'published-site', 'published')
        RETURNING id
    """, (owner,))
    published_site_id = cursor.fetchone()[0]
    
    cursor.execute("""
        INSERT INTO public.sites (owner_id, name, slug, status)
        VALUES (%s, 'Draft Site', 'draft-site', 'draft')
        RETURNING id
    """, (owner,))
    draft_site_id = cursor.fetchone()[0]
    
    # Add sections to both sites
    cursor.execute("""
        INSERT INTO public.site_sections (site_id, kind, position, content)
        VALUES (%s, 'hero', 1, '{"title": "Published Section"}')
        RETURNING id
    """, (published_site_id,))
    pub_section_id = cursor.fetchone()[0]
    
    cursor.execute("""
        INSERT INTO public.site_sections (site_id, kind, position, content)
        VALUES (%s, 'hero', 1, '{"title": "Draft Section"}')
        RETURNING id
    """, (draft_site_id,))
    draft_section_id = cursor.fetchone()[0]
    
    # Reset Role and jwt claim to simulate Anon
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")
    
    # 1. Test Anon read
    cursor.execute("SET ROLE anon")
    cursor.execute("RESET request.jwt.claim.sub")
    cursor.execute("SELECT id FROM public.sites")
    anon_site_ids = [r[0] for r in cursor.fetchall()]
    assert published_site_id in anon_site_ids
    assert draft_site_id not in anon_site_ids
    
    cursor.execute("SELECT id FROM public.site_sections")
    anon_section_ids = [r[0] for r in cursor.fetchall()]
    assert pub_section_id in anon_section_ids
    assert draft_section_id not in anon_section_ids
    
    # 2. Test Non-owner (authenticated, not admin) read
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (non_owner,))
    cursor.execute("SELECT id FROM public.sites")
    non_owner_site_ids = [r[0] for r in cursor.fetchall()]
    assert published_site_id in non_owner_site_ids
    assert draft_site_id not in non_owner_site_ids
    
    cursor.execute("SELECT id FROM public.site_sections")
    non_owner_section_ids = [r[0] for r in cursor.fetchall()]
    assert pub_section_id in non_owner_section_ids
    assert draft_section_id not in non_owner_section_ids
    
    # 3. Test Owner read (sees both published and draft)
    cursor.execute("SET request.jwt.claim.sub = %s", (owner,))
    cursor.execute("SELECT id FROM public.sites")
    owner_site_ids = [r[0] for r in cursor.fetchall()]
    assert published_site_id in owner_site_ids
    assert draft_site_id in owner_site_ids
    
    cursor.execute("SELECT id FROM public.site_sections")
    owner_section_ids = [r[0] for r in cursor.fetchall()]
    assert pub_section_id in owner_section_ids
    assert draft_section_id in owner_section_ids
    
    # 4. Test Admin read (sees both published and draft)
    cursor.execute("SET request.jwt.claim.sub = %s", (admin,))
    cursor.execute("SELECT id FROM public.sites")
    admin_site_ids = [r[0] for r in cursor.fetchall()]
    assert published_site_id in admin_site_ids
    assert draft_site_id in admin_site_ids
    
    cursor.execute("SELECT id FROM public.site_sections")
    admin_section_ids = [r[0] for r in cursor.fetchall()]
    assert pub_section_id in admin_section_ids
    assert draft_section_id in admin_section_ids
    
    # Reset
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")

def test_admin_role_management_policies(db_conn):
    cursor = db_conn.cursor()
    
    user_a = '10000000-0000-0000-0000-000000000001'
    user_b = '10000000-0000-0000-0000-000000000002'
    admin = '10000000-0000-0000-0000-000000000003'
    
    # Setup users
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")
    cursor.execute("""
        INSERT INTO auth.users (id, email) VALUES
        (%s, 'usera@example.com'),
        (%s, 'userb@example.com'),
        (%s, 'admin_role@example.com')
        ON CONFLICT (id) DO NOTHING
    """, (user_a, user_b, admin))
    cursor.execute("UPDATE public.user_roles SET role = 'admin' WHERE user_id = %s", (admin,))
    
    # 1. Test User A reads own role but not others
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (user_a,))
    cursor.execute("SELECT user_id, role FROM public.user_roles")
    roles = cursor.fetchall()
    # User A should only see their own role row
    assert len(roles) == 1
    assert roles[0][0] == user_a
    
    # 2. Test User A cannot insert/update/delete roles
    try:
        cursor.execute("INSERT INTO public.user_roles (user_id, role) VALUES (%s, 'admin')", (user_b,))
        assert False, "INSERT should have been blocked by RLS"
    except Exception:
        pass
        
    db_conn.rollback()
    
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (user_a,))
    cursor.execute("UPDATE public.user_roles SET role = 'admin' WHERE user_id = %s", (user_a,))
    assert cursor.rowcount == 0
    
    cursor.execute("DELETE FROM public.user_roles WHERE user_id = %s", (user_a,))
    assert cursor.rowcount == 0
    
    # 3. Test Admin can read all roles
    cursor.execute("SET request.jwt.claim.sub = %s", (admin,))
    cursor.execute("SELECT user_id FROM public.user_roles")
    all_user_ids = [r[0] for r in cursor.fetchall()]
    assert user_a in all_user_ids
    assert user_b in all_user_ids
    assert admin in all_user_ids
    
    # 4. Test Admin can manage roles
    cursor.execute("INSERT INTO public.user_roles (user_id, role) VALUES (%s, 'admin')", (user_b,))
    assert cursor.rowcount == 1
    
    cursor.execute("DELETE FROM public.user_roles WHERE user_id = %s AND role = 'admin'", (user_b,))
    assert cursor.rowcount == 1
    
    # Reset
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")

def test_admin_sites_crud_policies(db_conn):
    cursor = db_conn.cursor()
    
    owner = '20000000-0000-0000-0000-000000000001'
    other_user = '20000000-0000-0000-0000-000000000002'
    admin = '20000000-0000-0000-0000-000000000003'
    
    # Setup users
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")
    cursor.execute("""
        INSERT INTO auth.users (id, email) VALUES
        (%s, 'owner_crud@example.com'),
        (%s, 'other_crud@example.com'),
        (%s, 'admin_crud@example.com')
        ON CONFLICT (id) DO NOTHING
    """, (owner, other_user, admin))
    cursor.execute("UPDATE public.user_roles SET role = 'admin' WHERE user_id = %s", (admin,))
    
    # Owner creates a site
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (owner,))
    cursor.execute("""
        INSERT INTO public.sites (owner_id, name, slug, status)
        VALUES (%s, 'Owner Site', 'owner-site', 'draft')
        RETURNING id
    """, (owner,))
    site_id = cursor.fetchone()[0]
    
    # 1. Other standard user tries to update the site (should be blocked / 0 rows affected)
    cursor.execute("SET request.jwt.claim.sub = %s", (other_user,))
    cursor.execute("UPDATE public.sites SET name = 'Hacked Name' WHERE id = %s", (site_id,))
    assert cursor.rowcount == 0
    
    # 2. Other standard user tries to delete the site (should be blocked / 0 rows affected)
    cursor.execute("DELETE FROM public.sites WHERE id = %s", (site_id,))
    assert cursor.rowcount == 0
    
    # 3. Admin user updates the site (should succeed / 1 row affected)
    cursor.execute("SET request.jwt.claim.sub = %s", (admin,))
    cursor.execute("UPDATE public.sites SET name = 'Admin Updated' WHERE id = %s", (site_id,))
    assert cursor.rowcount == 1
    
    # 4. Admin user deletes the site (should succeed / 1 row affected)
    cursor.execute("DELETE FROM public.sites WHERE id = %s", (site_id,))
    assert cursor.rowcount == 1
    
    # Reset
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")


def test_products_constraints_and_policies(db_conn):
    cursor = db_conn.cursor()
    
    owner = '30000000-0000-0000-0000-000000000001'
    other_user = '30000000-0000-0000-0000-000000000002'
    admin = '30000000-0000-0000-0000-000000000003'
    
    # Setup users
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")
    cursor.execute("""
        INSERT INTO auth.users (id, email) VALUES
        (%s, 'prod_owner@example.com'),
        (%s, 'prod_other@example.com'),
        (%s, 'prod_admin@example.com')
        ON CONFLICT (id) DO NOTHING
    """, (owner, other_user, admin))
    cursor.execute("UPDATE public.user_roles SET role = 'admin' WHERE user_id = %s", (admin,))
    
    # 1. Owner creates a site
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (owner,))
    cursor.execute("""
        INSERT INTO public.sites (owner_id, name, slug, status)
        VALUES (%s, 'Prod Site', 'prod-site', 'draft')
        RETURNING id
    """, (owner,))
    site_id = cursor.fetchone()[0]
    
    # 2. Owner inserts product
    cursor.execute("""
        INSERT INTO public.products (site_id, name, description, price_label, payment_link_url)
        VALUES (%s, 'Product A', 'A nice product', '$10/mo', 'https://stripe.com/pay-a')
        RETURNING id
    """, (site_id,))
    prod_id = cursor.fetchone()[0]
    assert prod_id is not None
    
    # Verify that a standard site owner can read their own products
    cursor.execute("SELECT name FROM public.products WHERE id = %s", (prod_id,))
    assert cursor.fetchone()[0] == 'Product A'
    
    # Check invalid name constraint
    with pytest.raises(Exception):
        cursor.execute("""
            INSERT INTO public.products (site_id, name, description, price_label, payment_link_url)
            VALUES (%s, '  ', 'Desc', '$10/mo', 'https://stripe.com/pay')
        """, (site_id,))
    db_conn.rollback()
    
    # Check invalid price_label constraint
    with pytest.raises(Exception):
        cursor.execute("""
            INSERT INTO public.products (site_id, name, description, price_label, payment_link_url)
            VALUES (%s, 'Product B', 'Desc', '  ', 'https://stripe.com/pay')
        """, (site_id,))
    db_conn.rollback()
    
    # Check invalid payment_link_url constraint
    with pytest.raises(Exception):
        cursor.execute("""
            INSERT INTO public.products (site_id, name, description, price_label, payment_link_url)
            VALUES (%s, 'Product B', 'Desc', '$10', 'ftp://invalid-url')
        """, (site_id,))
    db_conn.rollback()
    
    # 3. Other user tries to insert product into owner's site (should fail RLS)
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (other_user,))
    try:
        cursor.execute("""
            INSERT INTO public.products (site_id, name, description, price_label, payment_link_url)
            VALUES (%s, 'Product B', 'Desc', '$10', 'https://stripe.com/pay')
        """, (site_id,))
        assert False, "Insert should be blocked by RLS"
    except Exception:
        pass
    db_conn.rollback()
    
    # 4. Other user tries to update owner's product (should fail RLS/0 rows)
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (other_user,))
    cursor.execute("UPDATE public.products SET name = 'Hacked' WHERE id = %s", (prod_id,))
    assert cursor.rowcount == 0
    
    # 5. Admin can update owner's product
    cursor.execute("SET request.jwt.claim.sub = %s", (admin,))
    cursor.execute("UPDATE public.products SET name = 'Admin Updated' WHERE id = %s", (prod_id,))
    assert cursor.rowcount == 1
    
    # 6. Anyone can read products of published site
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")
    cursor.execute("UPDATE public.sites SET status = 'published' WHERE id = %s", (site_id,))
    
    cursor.execute("SET ROLE anon")
    cursor.execute("SELECT name FROM public.products WHERE id = %s", (prod_id,))
    assert cursor.fetchone()[0] == 'Admin Updated'
    
    # Reset
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")


def test_revisions_limit_and_policies(db_conn):
    cursor = db_conn.cursor()
    
    owner = '40000000-0000-0000-0000-000000000001'
    
    # Setup users
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")
    cursor.execute("""
        INSERT INTO auth.users (id, email) VALUES
        (%s, 'rev_owner@example.com')
        ON CONFLICT (id) DO NOTHING
    """, (owner,))
    
    # 1. Owner creates a site
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (owner,))
    cursor.execute("""
        INSERT INTO public.sites (owner_id, name, slug, status)
        VALUES (%s, 'Rev Site', 'rev-site', 'draft')
        RETURNING id
    """, (owner,))
    site_id = cursor.fetchone()[0]
    
    # 2. Owner inserts 25 revisions (1 to 25) - verifies standard site owner CAN insert revisions
    for i in range(1, 26):
        cursor.execute("""
            INSERT INTO public.revisions (site_id, revision_number, site_snapshot, sections_snapshot)
            VALUES (%s, %s, '{"name": "Snap"}'::jsonb, '[]'::jsonb)
        """, (site_id, i))
        
    # Verify prune trigger worked: total revisions for this site should be 20, keeping 6 to 25
    # This also verifies that standard site owner CAN read/select their own revisions
    cursor.execute("SELECT COUNT(*) FROM public.revisions WHERE site_id = %s", (site_id,))
    assert cursor.fetchone()[0] == 20
    
    cursor.execute("SELECT MIN(revision_number), MAX(revision_number) FROM public.revisions WHERE site_id = %s", (site_id,))
    min_rev, max_rev = cursor.fetchone()
    assert min_rev == 6
    assert max_rev == 25
    
    # Verify standard site owner CANNOT update revisions (should fail RLS/0 rows)
    cursor.execute("""
        UPDATE public.revisions
        SET site_snapshot = '{"name": "Hacked"}'::jsonb
        WHERE site_id = %s AND revision_number = 25
    """, (site_id,))
    assert cursor.rowcount == 0
    
    # Verify standard site owner CANNOT delete revisions (should fail RLS/0 rows)
    cursor.execute("DELETE FROM public.revisions WHERE site_id = %s AND revision_number = 25", (site_id,))
    assert cursor.rowcount == 0
    
    # Verify anonymous users CANNOT select site revisions (should fail since public read was removed)
    cursor.execute("SET ROLE anon")
    cursor.execute("RESET request.jwt.claim.sub")
    cursor.execute("SELECT * FROM public.revisions WHERE site_id = %s", (site_id,))
    assert len(cursor.fetchall()) == 0
    
    # Reset
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")


def test_audit_results_constraints_and_policies(db_conn):
    cursor = db_conn.cursor()
    
    owner = '50000000-0000-0000-0000-000000000001'
    other_user = '50000000-0000-0000-0000-000000000002'
    admin = '50000000-0000-0000-0000-000000000003'
    
    # Setup users
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")
    cursor.execute("""
        INSERT INTO auth.users (id, email) VALUES
        (%s, 'audit_owner@example.com'),
        (%s, 'audit_other@example.com'),
        (%s, 'audit_admin@example.com')
        ON CONFLICT (id) DO NOTHING
    """, (owner, other_user, admin))
    cursor.execute("UPDATE public.user_roles SET role = 'admin' WHERE user_id = %s", (admin,))
    
    # 1. Owner creates a site
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (owner,))
    cursor.execute("""
        INSERT INTO public.sites (owner_id, name, slug, status)
        VALUES (%s, 'Audit Site', 'audit-site', 'draft')
        RETURNING id
    """, (owner,))
    site_id = cursor.fetchone()[0]
    
    # 2. Insert audit result as superuser (to bypass RLS constraints during creation)
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")
    cursor.execute("""
        INSERT INTO public.audit_results (site_id, target_url, score, status)
        VALUES (%s, 'https://example.com/site', 95, 'complete')
        RETURNING id
    """, (site_id,))
    audit_id = cursor.fetchone()[0]
    assert audit_id is not None
    
    # Check invalid url constraint
    with pytest.raises(Exception):
        cursor.execute("""
            INSERT INTO public.audit_results (site_id, target_url, score, status)
            VALUES (%s, 'invalid-url', 95, 'complete')
        """, (site_id,))
    db_conn.rollback()
    
    # Check invalid score constraint
    with pytest.raises(Exception):
        cursor.execute("""
            INSERT INTO public.audit_results (site_id, target_url, score, status)
            VALUES (%s, 'https://example.com/site', 150, 'complete')
        """, (site_id,))
    db_conn.rollback()
    
    # Check invalid status constraint
    with pytest.raises(Exception):
        cursor.execute("""
            INSERT INTO public.audit_results (site_id, target_url, score, status)
            VALUES (%s, 'https://example.com/site', 95, 'in-progress')
        """, (site_id,))
    db_conn.rollback()
    
    # 3. Standard site owner can read (SELECT) their own audits
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (owner,))
    cursor.execute("SELECT score FROM public.audit_results WHERE id = %s", (audit_id,))
    assert cursor.fetchone()[0] == 95
    
    # 4. Standard site owner CANNOT insert audit results (should fail RLS)
    try:
        cursor.execute("""
            INSERT INTO public.audit_results (site_id, target_url, score, status)
            VALUES (%s, 'https://example.com/site', 90, 'complete')
        """, (site_id,))
        assert False, "Owner insert should be blocked by RLS"
    except Exception:
        pass
    db_conn.rollback()
    
    # 5. Standard site owner CANNOT update audit results (should fail RLS/0 rows)
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (owner,))
    cursor.execute("UPDATE public.audit_results SET score = 50 WHERE id = %s", (audit_id,))
    assert cursor.rowcount == 0
    
    # 6. Standard site owner CANNOT delete audit results (should fail RLS/0 rows)
    cursor.execute("DELETE FROM public.audit_results WHERE id = %s", (audit_id,))
    assert cursor.rowcount == 0
    
    # 7. Other standard user tries to insert audit on owner's site (should fail RLS)
    cursor.execute("SET request.jwt.claim.sub = %s", (other_user,))
    try:
        cursor.execute("""
            INSERT INTO public.audit_results (site_id, target_url, score, status)
            VALUES (%s, 'https://example.com/site', 95, 'complete')
        """, (site_id,))
        assert False, "Insert should be blocked by RLS"
    except Exception:
        pass
    db_conn.rollback()
    
    # 8. Other standard user tries to update owner's audit (should fail RLS/0 rows)
    cursor.execute("SET ROLE authenticated")
    cursor.execute("SET request.jwt.claim.sub = %s", (other_user,))
    cursor.execute("UPDATE public.audit_results SET score = 50 WHERE id = %s", (audit_id,))
    assert cursor.rowcount == 0
    
    # 9. Admin can update owner's audit
    cursor.execute("SET request.jwt.claim.sub = %s", (admin,))
    cursor.execute("UPDATE public.audit_results SET score = 100 WHERE id = %s", (audit_id,))
    assert cursor.rowcount == 1
    
    # Reset
    cursor.execute("RESET ROLE")
    cursor.execute("RESET request.jwt.claim.sub")

