import os
from typing import Any, Dict, List, Optional
from datetime import datetime
from pymongo import MongoClient
from pymongo.collection import Collection

# Initialize MongoDB connection using environment variables
DATABASE_URL = os.getenv("DATABASE_URL", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "app_db")

_client: Optional[MongoClient] = None
_db = None

try:
    _client = MongoClient(DATABASE_URL, serverSelectionTimeoutMS=3000)
    # Trigger server selection to validate connection lazily
    _client.server_info()
    _db = _client[DATABASE_NAME]
except Exception:
    _client = None
    _db = None

# Expose db for other modules
db = _db


def _get_collection(name: str) -> Optional[Collection]:
    if db is None:
        return None
    return db[name]


def create_document(collection_name: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Insert a document with auto timestamps. Returns the inserted document (with _id).
    """
    col = _get_collection(collection_name)
    if col is None:
        raise RuntimeError("Database not connected")

    now = datetime.utcnow()
    doc = {**data, "created_at": now, "updated_at": now}
    res = col.insert_one(doc)
    doc["_id"] = str(res.inserted_id)
    return doc


def get_documents(collection_name: str, filter_dict: Optional[Dict[str, Any]] = None, limit: int = 100) -> List[Dict[str, Any]]:
    col = _get_collection(collection_name)
    if col is None:
        raise RuntimeError("Database not connected")

    cursor = col.find(filter_dict or {}).limit(limit)
    items: List[Dict[str, Any]] = []
    for d in cursor:
        d["_id"] = str(d.get("_id"))
        items.append(d)
    return items


def upsert_document(collection_name: str, filter_dict: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    col = _get_collection(collection_name)
    if col is None:
        raise RuntimeError("Database not connected")
    now = datetime.utcnow()
    update = {"$set": {**data, "updated_at": now}, "$setOnInsert": {"created_at": now}}
    res = col.update_one(filter_dict, update, upsert=True)
    if res.upserted_id:
        doc = col.find_one({"_id": res.upserted_id})
    else:
        doc = col.find_one(filter_dict)
    if doc:
        doc["_id"] = str(doc.get("_id"))
    return doc or {}
