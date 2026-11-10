"""Public geographic metadata only. No provider/database/model operations."""
import json
from pathlib import Path
from .collection_pilot import APPROVED

DATA=Path(__file__).resolve().parents[1]/"static/data/attractions.geojson"
PARKS={"all","islands","studios"}
TILES={"url":"https://tile.openstreetmap.org/{z}/{x}/{y}.png","attribution_html":'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',"min_zoom":14,"max_zoom":19}


def map_catalog(park="all"):
    if park not in PARKS:raise ValueError("park must be all, islands, or studios")
    data=json.loads(DATA.read_text(encoding="utf-8"))
    features=data["features"]
    if data["type"]!="FeatureCollection" or len(features)!=7 or {f["id"] for f in features}!=APPROVED:
        raise ValueError("Invalid attraction map catalog")
    for feature in features:
        props=feature["properties"]
        lon,lat=feature["geometry"]["coordinates"]
        if feature["geometry"]["type"]!="Point" or type(lat) not in (int,float) or type(lon) not in (int,float) or not 28.465<lat<28.483 or not -81.478<lon<-81.461:
            raise ValueError("Invalid attraction coordinates")
        if props["park"] not in {"islands","studios"} or props["source_url"]!=f"https://www.openstreetmap.org/{props['osm_type']}/{props['osm_id']}":
            raise ValueError("Invalid map provenance")
    return {**data,"features":[f for f in features if park=="all" or f["properties"]["park"]==park],"tiles":dict(TILES),"location_note":"Approximate attraction locations, not entrances or walking directions. No live waits are displayed."}
