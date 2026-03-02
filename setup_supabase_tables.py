#!/usr/bin/env python3
"""
Quick script to create Supabase tables via direct PostgreSQL connection.
Reads SUPABASE_URL from .env and constructs the connection string.
"""

import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        print(f"✓ Loaded .env from {env_path}")
except ImportError:
    print("⚠️  python-dotenv not found, using environment variables")

# Read Supabase URL and construct PostgreSQL connection string
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL:
    print("❌ SUPABASE_URL not found in environment")
    sys.exit(1)

# Extract project ref from URL like: https://qsjxxswsrykrrrnrpXXX.supabase.co
project_ref = SUPABASE_URL.replace("https://", "").replace(".supabase.co", "")

# Supabase PostgreSQL connection (use password from Supabase Dashboard > Settings > Database)
# Note: This requires the database password, NOT the service_role key
print("""
=================================================================
SUPABASE TABLE SETUP
=================================================================

To create tables in your Supabase database, you have 2 options:

OPTION 1: Manual SQL Execution (Recommended - Easiest)
-------------------------------------------------------
1. Open your Supabase Dashboard: https://supabase.com/dashboard/project/{project_ref}
2. Go to: SQL Editor (left sidebar)
3. Click: "New Query"
4. Copy the SQL from: D:\\AWS\\supabase\\SupaFront\\supabase_schema.sql
5. Paste into the SQL Editor
6. Click: "Run" (or press F5)

OPTION 2: Using psql (Command Line)
------------------------------------
If you have PostgreSQL psql installed:

1. Get your database password from Supabase Dashboard:
   Dashboard > Settings > Database > Database password

2. Run this command:
   psql "postgresql://postgres.{project_ref}:[YOUR-PASSWORD]@aws-0-{region}.pooler.supabase.com:6543/postgres" -f "D:\\AWS\\supabase\\SupaFront\\supabase_schema.sql"

3. Replace [YOUR-PASSWORD] with your actual database password
   Replace {region} with your Supabase region (e.g., us-east-1)

=================================================================

After creating tables, run the push command again:
    python src/export/to_supabase.py --db-path "3- Enrichment + Standardization/jobs_enriched.db"

=================================================================
""".format(project_ref=project_ref, region="us-east-1"))
