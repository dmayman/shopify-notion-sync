# lib/db.py
import os
from psycopg2 import pool

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("⚠️ DATABASE_URL not set - database functionality will not work")
    db_pool = None
else:
    # Keep the pool module-global so warm invocations reuse it
    db_pool = pool.SimpleConnectionPool(1, 10, DATABASE_URL)
