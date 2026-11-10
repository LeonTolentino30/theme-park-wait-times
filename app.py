"""Local-only Flask interface to the verified Phase 1 synthetic model."""
import math
import time
from collections import deque
from threading import Lock
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException
from src.ml_pipeline import DAYS, RIDES, input_frame, load_new_model, predict_wait_time

from src.current_waits import read_current_waits, DB as CURRENT_DB, REGISTRY as CURRENT_REGISTRY

from src.history import read_history, HistoryInputError, HistoryUnavailable

DISCLAIMER = "SYNTHETIC MODEL ESTIMATES, NOT LIVE PARK WAIT TIMES"


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(DEBUG=False, MAX_CONTENT_LENGTH=4096)
    app.config.update(CURRENT_WAITS_DB=CURRENT_DB, CURRENT_WAITS_REGISTRY=CURRENT_REGISTRY, OBSERVED_DATA_ENABLED=True, PUBLIC_DEPLOYMENT=False)
    if config:
        app.config.update(config)
    if app.config["PUBLIC_DEPLOYMENT"]:
        app.config.update(DEBUG=False, TESTING=False, PROPAGATE_EXCEPTIONS=False, OBSERVED_DATA_ENABLED=False)
    prediction_requests = deque()
    prediction_lock = Lock()

    @app.before_request
    def public_request_limit():
        if app.config["PUBLIC_DEPLOYMENT"] and request.path == "/api/predict":
            now = time.monotonic()
            with prediction_lock:
                while prediction_requests and prediction_requests[0] <= now-60:
                    prediction_requests.popleft()
                if len(prediction_requests) >= 60:
                    response = jsonify(error="Prediction request limit reached. Please retry in one minute.")
                    response.status_code = 429
                    response.headers["Retry-After"] = "60"
                    return response
                prediction_requests.append(now)

    # Fixed-path, hash-checked Phase 1 loader; never loads the legacy artifact.
    try:
        model = load_new_model()
    except Exception:
        model = None
        app.logger.error("Prediction model unavailable; verify local Phase 1 artifacts.")

    def stored_waits():
        if not app.config["OBSERVED_DATA_ENABLED"]:
            return {"source":"ThemeParks.wiki", "available":False, "parks":[],
                    "message":"Observed posted waits are not configured for this public demo. No synthetic waits are substituted.",
                    "attribution":{"text":"Powered by ThemeParks.wiki", "url":"https://www.themeparks.wiki"}}
        return read_current_waits(app.config["CURRENT_WAITS_DB"], app.config["CURRENT_WAITS_REGISTRY"])

    @app.get("/api/current-waits")
    def current_waits():
        data = stored_waits()
        return jsonify(data), 200 if data["available"] else 503

    @app.get("/api/history")
    def history():
        if any(len(request.args.getlist(key)) != 1 for key in request.args):
            return jsonify(error="Provide each history parameter exactly once."), 400
        if not app.config["OBSERVED_DATA_ENABLED"]:
            return jsonify(error="Historical observations are not configured for this public demo."), 503
        try:
            return jsonify(read_history(request.args.to_dict(),app.config["CURRENT_WAITS_DB"],app.config["CURRENT_WAITS_REGISTRY"]))
        except HistoryInputError as error:
            return jsonify(error=str(error)), 400
        except HistoryUnavailable:
            return jsonify(error="Stored history is temporarily unavailable. Please try again later."), 503

    @app.get("/healthz")
    def health():
        return jsonify(status="ok" if model is not None else "unavailable"), 200 if model is not None else 503

    @app.get("/")
    def index():
        return render_template("index.html", rides=RIDES, days=DAYS, hours=range(8, 23), disclaimer=DISCLAIMER, current_waits=stored_waits(), observed_data_enabled=app.config["OBSERVED_DATA_ENABLED"])

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
        response.headers["Referrer-Policy"] = "no-referrer"
        if app.config["PUBLIC_DEPLOYMENT"]:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
