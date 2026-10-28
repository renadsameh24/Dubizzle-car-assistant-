import math
import os
from pathlib import Path
import numpy as np

from dubizzle_car_assistant.inventory import load_listings

def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("Vectors must have the same length")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def load_gemini_key() -> None:
    """Load the API key from .env. Optional chat-model names are loaded too.

    GEMINI_CHAT_MODEL and GEMINI_CHAT_FALLBACK already set in the environment
    are left unchanged.
    """
    env_path = Path(__file__).resolve().parents[3] / ".env"
    found_key = bool(os.environ.get("GEMINI_API_KEY"))
    optional = {"GEMINI_CHAT_MODEL", "GEMINI_CHAT_FALLBACK"}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            name, separator, value = line.partition("=")
            name = name.strip()
            value = value.strip().strip('"').strip("'")
            if not separator or not value or name.startswith("#"):
                continue
            if name == "GEMINI_API_KEY":
                os.environ["GEMINI_API_KEY"] = value
                found_key = True
            elif name in optional and name not in os.environ:
                os.environ[name] = value
    if not found_key:
        raise RuntimeError("GEMINI_API_KEY is missing from .env")


def embed_texts(texts: list[str]) -> list[list[float]]:
    load_gemini_key()
    import litellm

    response = litellm.embedding(
        model="gemini/gemini-embedding-001",
        input=texts,
    )
    return [item.embedding for item in response.data]

INDEX_PATH = Path(__file__).resolve().parents[3] / "data" / "listing_embeddings.npz"
EMBEDDING_BATCH_SIZE = 20
_index_cache: tuple[np.ndarray, np.ndarray] | None = None
_query_cache: dict[str, np.ndarray] = {}


def listing_text(title: str, description: str) -> str:
    return f"{title}\n{description}"[:6000]


def build_embedding_index(path: Path = INDEX_PATH) -> None:
    frame = load_listings()
    documents = [
        listing_text(title, description)
        for title, description in zip(frame["title"], frame["description"], strict=True)
    ]
    vectors: list[list[float]] = []
    for start in range(0, len(documents), EMBEDDING_BATCH_SIZE):
        chunk = documents[start : start + EMBEDDING_BATCH_SIZE]
        vectors.extend(embed_texts(chunk))
        print(f"embedded {len(vectors)}/{len(documents)}")
    if len(vectors) != len(documents):
        raise RuntimeError("The embedding API returned a different number of vectors than listings")
    matrix = np.asarray(vectors, dtype=np.float32)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, listing_ids=frame["listing_id"].to_numpy(), vectors=matrix)

def _load_index() -> tuple[np.ndarray, np.ndarray]:
    global _index_cache
    if _index_cache is None:
        archive = np.load(INDEX_PATH)
        _index_cache = (archive["listing_ids"], archive["vectors"])
    return _index_cache


def similarity_scores(query: str) -> dict[int, float]:
    listing_ids, matrix = _load_index()
    if query not in _query_cache:
        _query_cache[query] = np.asarray(embed_texts([query])[0], dtype=np.float32)
    query_vector = _query_cache[query]
    row_norms = np.linalg.norm(matrix, axis=1)
    query_norm = float(np.linalg.norm(query_vector))
    scores = matrix @ query_vector
    scores = scores / (np.maximum(row_norms, 1e-12) * max(query_norm, 1e-12))
    return {
        int(listing_id): float(score)
        for listing_id, score in zip(listing_ids, scores, strict=True)
    }


def semantic_search(query: str, limit: int = 5) -> list[dict[str, object]]:
    scores = similarity_scores(query)
    best_ids = sorted(scores, key=scores.__getitem__, reverse=True)[:limit]
    listings = load_listings().set_index("listing_id")
    results: list[dict[str, object]] = []
    for listing_id in best_ids:
        row = listings.loc[listing_id]
        results.append(
            {
                "listing_id": listing_id,
                "score": round(scores[listing_id], 3),
                "year": int(row["year"]),
                "make": row["make"],
                "model": row["model"],
                "title": row["title"],
            }
        )
    return results