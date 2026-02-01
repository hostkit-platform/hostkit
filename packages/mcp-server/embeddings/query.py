#!/usr/bin/env python3
"""
Query Embedding Generator for hostkit-context MCP Server

Generates embeddings for user queries at runtime.
Called via subprocess from the MCP server.

Usage:
    python query.py "how do I enable payments"
    echo "deploy with auth" | python query.py -

Output:
    JSON array of floats (the embedding vector)
"""

import json
import sys
from sentence_transformers import SentenceTransformer

# Use the same model as document embeddings
MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"

# Global model instance (loaded once per process)
_model = None


def get_model() -> SentenceTransformer:
    """Get or load the embedding model."""
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_ID)
    return _model


def embed_query(query: str) -> list[float]:
    """Generate embedding for a query string."""
    model = get_model()
    embedding = model.encode([query])[0]
    return embedding.tolist()


def main():
    # Get query from args or stdin
    if len(sys.argv) > 1:
        if sys.argv[1] == "-":
            query = sys.stdin.read().strip()
        else:
            query = " ".join(sys.argv[1:])
    else:
        print("Usage: python query.py <query>", file=sys.stderr)
        print("       echo <query> | python query.py -", file=sys.stderr)
        sys.exit(1)

    if not query:
        print("Error: Empty query", file=sys.stderr)
        sys.exit(1)

    # Generate and output embedding
    embedding = embed_query(query)
    print(json.dumps(embedding))


if __name__ == "__main__":
    main()
