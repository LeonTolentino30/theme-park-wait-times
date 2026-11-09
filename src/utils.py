"""Prediction entry point for the freshly trained synthetic model only."""
try:
    from .ml_pipeline import predict_wait_time
except ImportError:  # Support existing scripts that import utils from src.
    from ml_pipeline import predict_wait_time

__all__ = ["predict_wait_time"]
