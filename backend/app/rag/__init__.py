"""
RAG (Retrieval-Augmented Generation) pipeline for the AI_CAO.

Modules:
    document_processor  PDF text extraction, chunking and chunk metadata.
    embeddings          OpenAI text-embedding-3-small client.
    vector_db           Pinecone client (init, upsert, query).
    retrieval           Query -> embedding -> Pinecone -> formatted context.
    ingestion           End-to-end ingestion used by the CLI scripts.

Only the AI_CAO uses this package. The public Dr. Aqua chat does not.
"""
