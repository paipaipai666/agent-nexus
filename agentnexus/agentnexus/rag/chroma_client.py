import chromadb
from sentence_transformers import SentenceTransformer

from agentnexus.core.config import get_settings

COLLECTION_NAME = "documents"
VECTOR_DIM = 512

_client = None
_collection = None
_model = None


def get_chroma_client():
    global _client
    if _client is None:
        settings = get_settings()
        _client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
    return _client


def get_embedding_model():
    global _model
    if _model is None:
        settings = get_settings()
        _model = SentenceTransformer(settings.embedding_model)
    return _model


def get_collection():
    global _collection
    if _collection is None:
        client = get_chroma_client()
        _collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def insert_documents(texts: list[str], metadatas: list[dict] | None = None):
    col = get_collection()
    model = get_embedding_model()
    embeddings = model.encode(texts, normalize_embeddings=True).tolist()
    ids = [f"doc_{i}" for i in range(col.count(), col.count() + len(texts))]
    col.add(ids=ids, embeddings=embeddings, documents=texts)


def search(query: str, limit: int = 5) -> list[dict]:
    col = get_collection()
    model = get_embedding_model()
    query_vec = model.encode(query, normalize_embeddings=True).tolist()
    results = col.query(query_embeddings=[query_vec], n_results=limit)
    if not results["ids"] or not results["ids"][0]:
        return []
    return [
        {"id": rid, "score": 1.0 - d, "text": doc}
        for rid, d, doc in zip(results["ids"][0], results["distances"][0], results["documents"][0])
    ]


def delete_collection():
    global _collection
    client = get_chroma_client()
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    _collection = None
