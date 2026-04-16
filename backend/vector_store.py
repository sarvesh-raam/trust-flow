import logging
import os
from pathlib import Path
from typing import List, Dict, Any

import chromadb
from llm_client import get_raw_openai_client, get_active_embed_model

log = logging.getLogger(__name__)

# Configuration
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
CHROMA_PATH = DATA_DIR / "chroma_hs_index"
COLLECTION_NAME = "hs_codes"

# ---------------------------------------------------------------------------
# Singleton Collection Support
# ---------------------------------------------------------------------------

_client = None
_collection = None

def get_hs_collection() -> chromadb.Collection:
    """Retrieve the HS codes ChromaDB collection as a singleton (lazy-init)."""
    global _client, _collection
    if _collection is not None:
        return _collection

    if not CHROMA_PATH.exists():
        log.warning(f"HS Index not found at {CHROMA_PATH}. Run 'python scripts/build_hs_index.py' first.")

    # Initialize client 
    if _client is None:
         _client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    
    # Get collection (without an embedding function because we provide manual vectors)
    try:
        _collection = _client.get_collection(name=COLLECTION_NAME)
        return _collection
    except Exception as e:
        log.error(f"Failed to load HS collection '{COLLECTION_NAME}': {e}")
        raise RuntimeError(f"HS collection '{COLLECTION_NAME}' does not exist. Build it first.")


# ---------------------------------------------------------------------------
# Search Implementation (OpenAI)
# ---------------------------------------------------------------------------

async def search_hs_openai(query: str, top_k: int = 8) -> List[Dict[str, Any]]:
    """Search for the most relevant HS codes using OpenAI embeddings.
    
    1. Embed query via OpenAI (text-embedding-3-large).
    2. Query ChromaDB with the raw vector.
    3. Return ranked meta-information with cosine similarity scores.
    """
    try:
        # 1. Embed query
        raw_client = get_raw_openai_client()
        model = get_active_embed_model()
        
        resp = raw_client.embeddings.create(
            model=model,
            input=[query]
        )
        query_vec = resp.data[0].embedding

        # 2. Query ChromaDB 
        collection = get_hs_collection()
        results = collection.query(
            query_embeddings=[query_vec],
            n_results=top_k,
            include=["documents", "metadatas", "distances"]
        )

        # 3. Format output
        # distances are cosine distance (0 to 2), where 0 is identical.
        # score = 1 - distance is a proxy for similarity (can be negative if documents are opposite).
        
        output = []
        if not results["ids"] or not results["ids"][0]:
            return []
            
        for i in range(len(results["ids"][0])):
            meta = results["metadatas"][0][i]
            dist = results["distances"][0][i]
            
            output.append({
                "code":        meta.get("code"),
                "description": meta.get("description"),
                "score":       round(float(1.0 - dist), 4),
            })
            
        return output

    except Exception as e:
        log.error(f"search_hs_openai failed for query '{query}': {e}")
        return []
