from fastapi import FastAPI
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from sklearn.metrics.pairwise import cosine_similarity
import json

app = FastAPI()

# TOP_K = 3
# SIM_THRESHOLD = 0.25

# model = SentenceTransformer("all-mpnet-base-v2")

# with open("troubleshooters.json", "r", encoding="utf-8") as f:
#     troubleshooters = json.load(f)

# texts = [f"{t['name']} {t.get('description', '')}" for t in troubleshooters]

# embeddings = model.encode(texts, normalize_embeddings = True, show_progress_bar = True)

# @app.post("/match")
# def match(payload: dict):
#     query = payload.get("query", "").strip()
#     if not query:
#         return {"matches": []}

#     query_embedding = model.encode([query], normalize_embeddings = True)

#     scores = cosine_similarity(query_embedding, embeddings)[0]

#     ranked = sorted(enumerate(scores), key = lambda x: x[1], reverse = True)

#     results = []
#     for idx, score in ranked[:TOP_K]:
#         if score < SIM_THRESHOLD:
#             continue

#         troubleshooter = troubleshooters[idx]
#         results.append({
#             "troubleshooter_id": troubleshooter.get("troubleshooter_id"),
#             "name": troubleshooter.get("name"),
#             "score": float(score)
#         })

#     return {"matches": results}


COLLECTION = "troubleshooters"
SIM_THRESHOLD = 0.45
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