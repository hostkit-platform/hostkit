#!/usr/bin/env python3
"""
Embedding Generator for hostkit-context MCP Server

Generates semantic embeddings for CLAUDE.md documentation chunks.
Uses sentence-transformers with optional MLX acceleration on Apple Silicon.

Usage:
    python generator.py              # Generate embeddings from existing chunks
    python generator.py --rebuild    # Rebuild chunks and embeddings

Output:
    ~/.hostkit-context/index/embeddings.json
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Try MLX first (preferred on Apple Silicon)
try:
    import mlx.core as mx
    HAS_MLX = True
    print("MLX available for Apple Silicon acceleration")
except ImportError:
    HAS_MLX = False
    print("MLX not available, using CPU inference")

from sentence_transformers import SentenceTransformer
import numpy as np

# Configuration
MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
CONTEXT_DIR = Path.home() / ".hostkit-context"
INDEX_DIR = CONTEXT_DIR / "index"
CHUNKS_PATH = INDEX_DIR / "chunks.json"
EMBEDDINGS_PATH = INDEX_DIR / "embeddings.json"
METADATA_PATH = INDEX_DIR / "metadata.json"


def load_model() -> SentenceTransformer:
    """Load the embedding model."""
    print(f"Loading model: {MODEL_ID}")
    model = SentenceTransformer(MODEL_ID)
    print(f"Model loaded. Device: {model.device}")
    return model


def load_chunks() -> list[dict]:
    """Load chunks from the index directory."""
    if not CHUNKS_PATH.exists():
        print(f"Error: Chunks file not found at {CHUNKS_PATH}")
        print("Run the TypeScript indexer first to generate chunks.")
        sys.exit(1)

    with open(CHUNKS_PATH, "r") as f:
        chunks = json.load(f)

    print(f"Loaded {len(chunks)} chunks")
    return chunks


def generate_embeddings(model: SentenceTransformer, chunks: list[dict]) -> list[list[float]]:
    """Generate embeddings for all chunks."""
    print(f"Generating embeddings for {len(chunks)} chunks...")

    # Extract text from chunks
    texts = [f"{chunk['title']}\n\n{chunk['content']}" for chunk in chunks]

    # Generate embeddings
    embeddings = model.encode(texts, convert_to_numpy=True, show_progress_bar=True)

    return embeddings.tolist()


def save_embeddings(embeddings: list[list[float]], chunk_count: int) -> None:
    """Save embeddings to disk."""
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    with open(EMBEDDINGS_PATH, "w") as f:
        json.dump(embeddings, f)

    # Update metadata
    if METADATA_PATH.exists():
        with open(METADATA_PATH, "r") as f:
            metadata = json.load(f)
    else:
        metadata = {}

    metadata.update({
        "embeddingsGeneratedAt": datetime.utcnow().isoformat() + "Z",
        "modelId": MODEL_ID,
        "embeddingDimension": len(embeddings[0]) if embeddings else 0,
        "embeddingCount": len(embeddings),
    })

    with open(METADATA_PATH, "w") as f:
        json.dump(metadata, f, indent=2)

    # Calculate file size
    size_kb = EMBEDDINGS_PATH.stat().st_size / 1024
    print(f"Saved embeddings to {EMBEDDINGS_PATH} ({size_kb:.1f} KB)")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Generate embeddings for CLAUDE.md chunks")
    parser.add_argument("--rebuild", action="store_true", help="Force rebuild of embeddings")
    args = parser.parse_args()

    # Check if embeddings already exist
    if EMBEDDINGS_PATH.exists() and not args.rebuild:
        print(f"Embeddings already exist at {EMBEDDINGS_PATH}")
        print("Use --rebuild to regenerate")

        # Check if up to date
        chunks_mtime = CHUNKS_PATH.stat().st_mtime if CHUNKS_PATH.exists() else 0
        emb_mtime = EMBEDDINGS_PATH.stat().st_mtime

        if emb_mtime >= chunks_mtime:
            print("Embeddings are up to date")
            return

        print("Chunks are newer, regenerating embeddings...")

    # Load model and chunks
    model = load_model()
    chunks = load_chunks()

    # Generate embeddings
    embeddings = generate_embeddings(model, chunks)

    # Save embeddings
    save_embeddings(embeddings, len(chunks))

    print("\nDone!")


if __name__ == "__main__":
    main()
