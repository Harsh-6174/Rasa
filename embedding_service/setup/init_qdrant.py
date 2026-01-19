import json, os
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, HnswConfigDiff, PointStruct
from sentence_transformers import SentenceTransformer

client = QdrantClient(host=os.getenv("QDRANT_HOST"), port= os.getenv("QDRANT_PORT"))
model = SentenceTransformer("all-mpnet-base-v2")

COLLECTION = os.getenv("QDRANT_COLLECTION")
VECTOR_SIZE = 768

with open("embedding_service/troubleshooters.json", "r", encoding="utf-8") as f:
    troubleshooters = json.load(f)

collections = client.get_collections()

if not client.collection_exists(COLLECTION):
    client.create_collection(
        collection_name = COLLECTION,
        vectors_config = VectorParams(
            size = VECTOR_SIZE,
            distance = Distance.COSINE
        ),
        hnsw_config = HnswConfigDiff(
            m = 16,
            ef_construct = 100,
            full_scan_threshold = 1000
        )
    )

points = []
for idx, t in enumerate(troubleshooters):
    text = f"{t['name']} {t.get('description', '')}"
    vector = model.encode(text, normalize_embeddings = True)

    points.append(
        PointStruct(
            id = idx,
            vector = vector.tolist(),
            payload = {
                "troubleshooter_id" : t['troubleshooter_id'],
                "name" : t['name'],
                "ps_command_id" : t['ps_command_id']
            }
        )
    )

client.upsert(collection_name = COLLECTION, points = points)

print("Qdrant Initialized")