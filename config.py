import os
from dotenv import load_dotenv

load_dotenv()

CENTRAL_AUTH_URL = os.getenv("CENTRAL_AUTH_URL", "https://clearbat.iiko.tech")

PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = int(os.getenv("PG_PORT", 5432))
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASSWORD = os.getenv("PG_PASSWORD", "postgres")
PG_DATABASE = os.getenv("PG_DATABASE", "clear_node")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    DATABASE_URL = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"

DB_POOL_MIN_SIZE = int(os.getenv("DB_POOL_MIN_SIZE", 5))
DB_POOL_MAX_SIZE = int(os.getenv("DB_POOL_MAX_SIZE", 20))
