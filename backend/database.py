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

# Fast detection for Vercel/serverless environments
client = None
if mongo_url:
    try:
        import pymongo
        sync_c = pymongo.MongoClient(mongo_url, serverSelectionTimeoutMS=400)
        sync_c.admin.command('ping')
        client = AsyncIOMotorClient(mongo_url)
        logger.info("Connected to MongoDB at %s", mongo_url)
    except Exception:
        client = None

if client is None:
    from mongomock_motor import AsyncMongoMockClient
    client = AsyncMongoMockClient()
    logger.info("Using AsyncMongoMockClient for database operations")

db = client[db_name]
