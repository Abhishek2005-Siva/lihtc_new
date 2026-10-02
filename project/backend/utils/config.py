"""Configuration helpers."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass
class Settings:
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    nvidia_api_key: str = ""
    fast_model: str = "nvidia/llama-3.1-nemotron-70b-instruct"
    synth_model: str = "nvidia/llama-3.1-nemotron-70b-instruct"


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        neo4j_uri=os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687"),
        neo4j_user=os.getenv("NEO4J_USER", "neo4j"),
        neo4j_password=os.getenv("NEO4J_PASSWORD", ""),
        nvidia_api_key=os.getenv("NVIDIA_API_KEY", ""),
        fast_model=os.getenv("FAST_MODEL", "nvidia/llama-3.1-nemotron-70b-instruct"),
        synth_model=os.getenv("SYNTH_MODEL", "nvidia/llama-3.1-nemotron-70b-instruct"),
    )
