"""Render entry point. Public demo never reads private provider observations."""
from app import create_app

application = create_app({"PUBLIC_DEPLOYMENT": True})
