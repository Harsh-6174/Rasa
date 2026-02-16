from fastapi import FastAPI
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from sklearn.metrics.pairwise import cosine_similarity
import json

app = FastAPI()

COLLECTION = "troubleshooters"
SIM_THRESHOLD = 0.30
TOP_K = 3
EF_SEARCH = 64

model = SentenceTransformer("all-mpnet-base-v2")
client = QdrantClient(host = "localhost", port = 6333)

@app.post("/match")
def match(payload: dict):
    query = payload.get("query", "").strip()
    if not query:
        return {"matches" : []}
    
    try:
        vector = model.encode(query, normalize_embeddings = True).tolist()

        results = client.query_points(
            collection_name = COLLECTION,
            query = vector,
            with_payload = True,
            limit = 3,
            search_params = {
                "hnsw_ef": EF_SEARCH
            }
        ).points

        matches = []
        for r in results:
            if r.score < SIM_THRESHOLD:
                continue

            print(r.payload)
            matches.append({
                "troubleshooter_id" : r.payload.get("troubleshooter_id"),
                "ps_command_id": r.payload.get("ps_command_id"),
                "name" : r.payload.get("name"),
                "score" : r.score
            })
        
        return {"matches" : matches}
    
    except Exception as e:
        print(f"[ERROR] - {e}")