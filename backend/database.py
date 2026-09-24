import os
import logging
from pathlib import Path
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger("options_alpha.db")

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ.get('MONGO_URL', '')
db_name = os.environ.get('DB_NAME', 'options_alpha')
alpaca_mode = os.environ.get("ALPACA_MODE", "").lower()
has_alpaca_keys = bool(os.environ.get("ALPACA_API_KEY") or os.environ.get("APCA_API_KEY_ID"))
alpaca_backed = alpaca_mode == "live" or (alpaca_mode != "mock" and has_alpaca_keys)

# Fast detection for Vercel/serverless environments
client = None
if mongo_url:
    try:
        import pymongo
        timeout_ms = int(os.environ.get("MONGO_SERVER_SELECTION_TIMEOUT_MS", "5000"))
        sync_c = pymongo.MongoClient(mongo_url, serverSelectionTimeoutMS=timeout_ms)
        sync_c.admin.command('ping')
        client = AsyncIOMotorClient(mongo_url)
        logger.info("Connected to MongoDB at %s", mongo_url)
    except Exception:
        client = None

if client is None:
    if alpaca_backed:
        raise RuntimeError(
            "Persistent MongoDB is required in Alpaca-backed mode. "
            "Refusing to start with an in-memory database because trading state would be lost."
        )
    from mongomock_motor import AsyncMongoMockClient
    client = AsyncMongoMockClient()
    logger.info("Using AsyncMongoMockClient for database operations (mock mode only)")

db = client[db_name]
