import json
import logging
import sys
from pathlib import Path

import chromadb
from llm_client import get_raw_openai_client, get_active_embed_model

# Setup relative paths
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
CHROMA_PATH = DATA_DIR / "chroma_hs_index"
SAMPLE_FILE = DATA_DIR / "hs_codes_sample.json"
COLLECTION_NAME = "hs_codes"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("build_hs_index")

def main():
    # 1. Load entries 
    if not SAMPLE_FILE.exists():
        log.error(f"Source file not found: {SAMPLE_FILE}")
        sys.exit(1)
        
    with open(SAMPLE_FILE, "r") as f:
        entries = json.load(f)
    
    log.info(f"Loaded {len(entries)} HS code entries.")

    # 2. Build document text
    docs = [f"HTS Code {e['code']}: {e['description']}" for e in entries]

    # 3. Get OpenAI embeddings in batches 
    try:
        client = get_raw_openai_client()
        model = get_active_embed_model()
    except RuntimeError as e:
        log.error(str(e))
        sys.exit(1)

    log.info(f"Generating embeddings using {model}...")
    
    embeddings = []
    batch_size = 20
    
    for i in range(0, len(docs), batch_size):
        batch_texts = docs[i : i + batch_size]
        log.info(f"Processing batch {i//batch_size + 1} ({len(batch_texts)} entries)...")
        
        resp = client.embeddings.create(
            model=model,
            input=batch_texts
        )
        # Extract embeddings in order
        batch_vecs = [item.embedding for item in resp.data]
        embeddings.extend(batch_vecs)

    # 4. Upsert into ChromaDB
    # Ensure dir exists
    CHROMA_PATH.mkdir(parents=True, exist_ok=True)
    
    db_client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    
    # We clear the existing collection to avoid duplication
    try:
        db_client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
        
    collection = db_client.create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"}
    )

    collection.add(
        ids=[e["code"] for e in entries],
        embeddings=embeddings,
        documents=docs,
        metadatas=[{"code": e["code"], "description": e["description"]} for e in entries]
    )

    log.info(f"Successfully indexed {len(entries)} HS codes into {CHROMA_PATH}")
    log.info(f"Collection: {COLLECTION_NAME}")

if __name__ == "__main__":
    main()
