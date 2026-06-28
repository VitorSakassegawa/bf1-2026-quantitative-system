"""Operational scripts for the BF1-2026 system.

Run inside the API container, e.g.:

    docker exec bf1_api python -m scripts.pipeline          # full pipeline
    docker exec bf1_api python -m scripts.seed              # grid only
    docker exec bf1_api python -m scripts.ingest 2021 2025  # history only
    docker exec bf1_api python -m scripts.build_intelligence
"""
