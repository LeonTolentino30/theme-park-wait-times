"""Local-only Flask interface to the verified Phase 1 synthetic model."""
import math
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException
from src.ml_pipeline import DAYS, RIDES, input_frame, load_new_model, predict_wait_time

from src.current_waits import read_current_waits, DB as CURRENT_DB, REGISTRY as CURRENT_REGISTRY

from src.history import read_history, HistoryInputError, HistoryUnavailable

DISCLAIMER = "SYNTHETIC MODEL ESTIMATES, NOT LIVE PARK WAIT TIMES"


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(DEBUG=False, MAX_CONTENT_LENGTH=4096)
    app.config.update(CURRENT_WAITS_DB=CURRENT_DB, CURRENT_WAITS_REGISTRY=CURRENT_REGISTRY)
    if config:
        app.config.update(config)
    # Fixed-path, hash-checked Phase 1 loader; never loads the legacy artifact.
    try:
        model = load_new_model()
    except Exception:
        model = None
        app.logger.error("Prediction model unavailable; verify local Phase 1 artifacts.")

    def stored_waits():
        return read_current_waits(app.config["CURRENT_WAITS_DB"], app.config["CURRENT_WAITS_REGISTRY"])

    @app.get("/api/current-waits")
    def current_waits():
        data = stored_waits()
        return jsonify(data), 200 if data["available"] else 503

    @app.get("/api/history")
    def history():
        if any(len(request.args.getlist(key)) != 1 for key in request.args):
            return jsonify(error="Provide each history parameter exactly once."), 400
        try:
            return jsonify(read_history(request.args.to_dict(),app.config["CURRENT_WAITS_DB"],app.config["CURRENT_WAITS_REGISTRY"]))
        except HistoryInputError as error:
            return jsonify(error=str(error)), 400
        except HistoryUnavailable:
            return jsonify(error="Stored history is temporarily unavailable. Please try again later."), 503

    @app.get("/")
    def index():
        return render_template("index.html", rides=RIDES, days=DAYS, hours=range(8, 23), disclaimer=DISCLAIMER, current_waits=stored_waits())

    @app.post("/api/predict")
    def predict():
        if not request.is_json:
            return jsonify(error="Send a JSON object with ride, day, and hour."), 400
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {"ride", "day", "hour"}:
            return jsonify(error="Provide exactly ride, day, and hour."), 400
        try:
            input_frame(payload["ride"], payload["day"], payload["hour"])
        except ValueError as error:
            return jsonify(error=str(error)), 400
        if model is None:
            return jsonify(error="Predictions are temporarily unavailable. Please try again later."), 503
        try:
            value = predict_wait_time(payload["ride"], payload["day"], payload["hour"], model=model)
        except Exception:
            app.logger.error("Prediction could not be completed.")
            return jsonify(error="Unable to generate an estimate. Please try again later."), 503
        return jsonify(predicted_minutes=math.floor(value + 0.5),
                       ride=payload["ride"], day=payload["day"], hour=payload["hour"],
                       synthetic=True, disclaimer=DISCLAIMER)

    @app.errorhandler(HTTPException)
    def http_error(error):
        messages = {404: "Page not found.", 405: "Method not allowed.", 413: "Request is too large."}
        return jsonify(error=messages.get(error.code, "Unable to process the request.")), error.code

    @app.errorhandler(Exception)
    def unexpected_error(error):
        app.logger.error("Request could not be completed.")
        return jsonify(error="Unable to complete the request."), 500

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        response.headers["Cache-Control"] = "no-store"
        return response

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
