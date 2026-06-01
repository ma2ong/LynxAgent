import os
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

_client: MongoClient | None = None


def _get_client() -> MongoClient:
    global _client
    if _client is None:
        url = os.environ.get("MONGODB_URL", "mongodb://localhost:27017")
        _client = MongoClient(url, serverSelectionTimeoutMS=5000)
    return _client


def get_mongo_db() -> Database:
    db_name = os.environ.get("MONGODB_DB", "tradingagents")
    return _get_client()[db_name]


def get_collection(name: str) -> Collection:
    return get_mongo_db()[name]
