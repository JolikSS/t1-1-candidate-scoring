"""Shared dependencies; MongoDB is the only required storage service."""

from api.config import get_settings


def database():
    from api.main import require_store

    return require_store()


def optional_graph():
    from api import main

    return main.graph if get_settings().neo4j_enabled else None
