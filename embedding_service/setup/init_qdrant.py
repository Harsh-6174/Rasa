import os, json, requests
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, HnswConfigDiff, PointStruct
from sentence_transformers import SentenceTransformer

load_dotenv()

def get_action_list(sync_type):
    url = "https://dev.workelevate.com/api/Chatbot/SyncActionData"

    payload = {
        'machine_name': '',
        'domain_name': 'progressive.in',
        'user_name': 'harsh.vardhan',
        'sync_type': f'{sync_type}',
        'domain_id': 2,
        'platform_id': 1
    }

    headers = {
        "accept": "*/*",
        "Authorization": f"Bearer {os.getenv('JOB_SCHEDULER_SYNC_DATA_BEARER_TOKEN')}",
        "Content-Type": "application/json-patch+json"
    }

    response = requests.post(
        url,
        data=json.dumps(payload),
        headers=headers,
        timeout=10
    )

    response.raise_for_status()

    try:
        return response.json()
    except Exception:
        return response.text

client = QdrantClient(host=os.getenv("QDRANT_HOST"), port=os.getenv("QDRANT_PORT"))
model = SentenceTransformer("all-mpnet-base-v2")

COLLECTION = os.getenv("QDRANT_COLLECTION")
VECTOR_SIZE = 768

troubleshooters = get_action_list(sync_type=3)

if not client.collection_exists(COLLECTION):
    client.create_collection(
        collection_name=COLLECTION,
        vectors_config=VectorParams(
            size=VECTOR_SIZE,
            distance=Distance.COSINE
        ),
        hnsw_config=HnswConfigDiff(
            m=16,
            ef_construct=100,
            full_scan_threshold=1000
        )
    )

points = []

for idx, t in enumerate(troubleshooters):
    name = t.get("name", "")
    description = t.get("description", "")
    text = f"{name} {description}".strip()

    vector = model.encode(text, normalize_embeddings=True)

    points.append(
        PointStruct(
            id = idx,
            vector = vector.tolist(),
            payload = {
                "troubleshooter_id" : t['troubleshooter_id'],
                "name" : name,
                "ps_command_id" : t['ps_command_id']
            }
        )
    )

client.upsert(collection_name = COLLECTION, points = points)

print("Qdrant Initialized")