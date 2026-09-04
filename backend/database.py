import os
import logging
from pathlib import Path
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger("options_alpha.db")

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
db_name = os.environ.get('DB_NAME', 'options_alpha')

is_serverless = bool(os.environ.get('VERCEL'))

# In Vercel serverless without a remote MONGO_URL, don't stall on localhost connection timeout
if is_serverless and (not os.environ.get('MONGO_URL') or 'localhost' in mongo_url):
    try:
        from mongomock_motor import AsyncMongoMockClient
        client = AsyncMongoMockClient()
        logger.info("Vercel serverless environment with no remote MongoDB; using AsyncMongoMockClient")
    except Exception as e:
        logger.error("Failed to initialize AsyncMongoMockClient: %s", e)
        raise
else:
    try:
        import pymongo
        sync_c = pymongo.MongoClient(mongo_url, serverSelectionTimeoutMS=800)
        sync_c.admin.command('ping')
        client = AsyncIOMotorClient(mongo_url)
        logger.info("Connected to MongoDB at %s", mongo_url)
    except Exception:
        from mongomock_motor import AsyncMongoMockClient
        client = AsyncMongoMockClient()
        logger.info("MongoDB not running locally; using AsyncMongoMockClient")

db = client[db_name]
