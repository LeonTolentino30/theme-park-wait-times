"""Read-only production-path check; emits aggregate results, never observations."""
import argparse
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
from . import history_snapshots as snapshots

DIGEST = "931fc9fffabdae743eb10871e8e75babea4b023a7859d4a4f311f78c26c29de7"


def verify(application):
    registry = snapshots.public_registry()
    provider = application.config["HISTORY_SNAPSHOT_PROVIDER"]
    data = provider.snapshot(registry)
    raw = snapshots.canonical(data)
    if hashlib.sha256(raw).hexdigest() != DIGEST or len(raw) != 176510 or len(data["records"]) != 602:
        raise ValueError("Unexpected approved snapshot")
    client = application.test_client()
    assert not application.debug
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/map-attractions").status_code == 200
    home = client.get("/").get_data(as_text=True)
    assert all(ident in home for ident in registry)
    assert 'value="rolling"' not in home
    assert client.get("/api/history").status_code == 503
    assert client.get("/api/current-waits").status_code == 503
    assert client.post("/api/predict",json={"ride":"Hulk Coaster","day":"Sat","hour":12}).status_code == 200
    charts = 0
    for ident in registry:
        for hours in ("6","24","168"):
            params = {"attraction_id":ident,"hours":hours,"mode":"archive"}
            view = snapshots.window(params,data,registry=registry)
            assert view["end"] == snapshots.utc(data["coverage_end"])
            response = client.get("/history/chart",query_string=params)
            html = response.get_data(as_text=True)
            assert response.status_code == 200
            assert html.count('<circle ') == sum(p["wait"] is not None for p in view["points"])
            assert "ARCHIVED POSTED WAITS" in html and "Incomplete history" in html
            assert '"records"' not in html and "Powered by ThemeParks.wiki" in html
            charts += 1
    return {"passed":True,"sha256":DIGEST,"bytes":len(raw),"observations":len(data["records"]),
            "attractions":len(registry),"charts_passed":charts,"coverage_start":data["coverage_start"],
            "coverage_end":data["coverage_end"],"generated_at":data["generated_at"],
            "raw_history_disabled":True,"sqlite_access":False,"cloud_writes":False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report",required=True)
    args=parser.parse_args()
    report={"passed":False,"error":"Production readiness verification failed; details suppressed."}
    try:
        with patch("sqlite3.connect",side_effect=AssertionError("SQLite is forbidden")):
            from wsgi import application
            report=verify(application)
    except Exception:
        pass
    with Path(args.report).open("x",encoding="utf-8") as output:
        json.dump(report,output,indent=2)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
