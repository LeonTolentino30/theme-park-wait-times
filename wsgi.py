"""Render entry point. No SQLite access; cloud history remains explicitly opt-in."""
from app import create_app
from src.history_production import production_config

application = create_app(production_config())
