# pip install chromadb sentence-transformers

import json
import os
import sqlite3
import chromadb
from sentence_transformers import SentenceTransformer

DB_PATH = "./vector_db"
METADATA_DB_PATH = os.path.join(DB_PATH, "metadata.db")
COLLECTION_NAME = "memories"

model = SentenceTransformer("all-MiniLM-L6-v2")

client = chromadb.PersistentClient(path=DB_PATH)

collection = client.get_or_create_collection(
    name=COLLECTION_NAME,
    metadata={"hnsw:space": "cosine"}
)


def metadata_connection():
    os.makedirs(DB_PATH, exist_ok=True)
    conn = sqlite3.connect(METADATA_DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            id TEXT PRIMARY KEY,
            json TEXT NOT NULL
        )
        """
    )
    return conn


def add_semantic_text(items):
    """
    Add items shaped like {"id": "...", "text": "...", ...metadata}.

    Chroma stores only id, text, and embedding. The full JSON object is stored
    in SQLite by id so semantic search can stay focused on the text.
    """
    ids = []
    documents = []
    embeddings = []

    with metadata_connection() as conn:
        for item in items:
            item_id = item["id"]
            metadata_json = json.dumps(item, ensure_ascii=False, sort_keys=True)
            conn.execute(
                """
                INSERT INTO metadata (id, json)
                VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET json = excluded.json
                """,
                (item_id, metadata_json)
            )

    for item in items:
        item_id = item["id"]
        doc = item["text"]

        ids.append(item_id)
        documents.append(doc)
        embeddings.append(model.encode(doc).tolist())

    collection.upsert(
        ids=ids,
        documents=documents,
        embeddings=embeddings
    )


def semantic_search(query, top_k=3):
    query_embedding = model.encode(query).tolist()

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k
    )

    output = []

    for i in range(len(results["ids"][0])):
        output.append({
            "id": results["ids"][0][i],
            "text": results["documents"][0][i],
            "score": results["distances"][0][i]
        })

    return output


def metadata_search(item_id):
    with metadata_connection() as conn:
        row = conn.execute(
            "SELECT json FROM metadata WHERE id = ?",
            (item_id,)
        ).fetchone()

    if row is None:
        return None

    return json.loads(row[0])


if __name__ == "__main__":
    data = [
        {
            "id": "mem_1",
            "text": "a delicious plate of spaghetti bolognese",
            "type": "food",
            "source": "demo"
        },
        {
            "id": "mem_2",
            "text": "a tall building with an office in it",
            "type": "place",
            "source": "demo"
        },
        {
            "id": "mem_3",
            "text": "a cute dog as somebodys pet",
            "type": "animal",
            "source": "demo"
        }

    ]

    #add_semantic_text(data)

    while True:
	    query = input("")

	    results = semantic_search(query)

	    for i in results:
	    	print(i)
	    	print(metadata_search(i["id"]))
	    print('\n')
