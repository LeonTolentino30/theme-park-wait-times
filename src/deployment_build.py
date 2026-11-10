"""Build-time generation only; never deletes or overwrites model artifacts."""
import json
from . import ml_pipeline as ml


def ensure_model():
    model_exists = ml.MODEL.exists()
    manifest_exists = ml.RUNTIME_REPORT.exists()
    if not model_exists and not manifest_exists:
        ml.train()
        return "generated"
    if not model_exists or not manifest_exists:
        raise RuntimeError("Partial runtime artifacts; use a reviewed clean build. Nothing was overwritten.")
    manifest = json.loads(ml.RUNTIME_REPORT.read_text(encoding="utf-8"))
    if (manifest.get("model") != "models/synthetic_rf_v1.joblib"
            or manifest.get("model_sha256") != ml.sha256(ml.MODEL)
            or manifest.get("dataset_sha256") != ml.sha256(ml.DATA)):
        raise RuntimeError("Cached runtime artifacts failed integrity/data checks. Nothing was overwritten.")
    return "verified-existing"


if __name__ == "__main__":
    print("Deployment model: " + ensure_model())
