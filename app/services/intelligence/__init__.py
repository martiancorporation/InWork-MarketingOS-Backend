"""Client intelligence subsystem.

The async pipeline that, after onboarding, ingests all client inputs (fields +
files), builds a versioned summary + a prioritized directive store, and a vector
RAG layer — then serves that context to every downstream agent.
"""
