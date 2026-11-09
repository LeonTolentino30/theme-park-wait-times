"""Short read-only SQLite snapshots; never calls the provider or collector."""
from contextlib import closing
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from .themeparks_provider import load_registry, utc_now, timestamp, ProviderError
from .collection_pilot import DB, APPROVED

REGISTRY = Path(__file__).resolve().parents[1]/"private/themeparks/attraction_registry.proposed.json"
FRESH_SECONDS = 900
PARK_NAMES = {"267615cc-8943-4c2a-ae2c-5da728ca591f":"Islands of Adventure",
              "eb3f4560-2383-4a36-9152-6b3e5ed6bc57":"Universal Studios Florida"}


def display_time(value):
    return value.astimezone(ZoneInfo("America/New_York")).strftime("%b %d, %Y %I:%M:%S %p %Z") if value else "Not available"


def empty_card(attraction):
    return {"attraction_id":attraction.attraction_id,"name":attraction.provider_name,"alias":attraction.alias,
        "park":PARK_NAMES[attraction.park_id],"status":"UNAVAILABLE","status_label":"Unavailable",
        "posted_wait_minutes":None,"wait_label":"Not available","observed_at":None,
        "observed_at_display":"Not available","provider_last_changed_at":None,
        "provider_last_changed_display":"Not available","freshness":"unavailable",
        "freshness_label":"No stored observation","age_seconds":None}


def card(attraction,row,now):
    result=empty_card(attraction)
    if row is None:
        return result
    try:
        observed=timestamp(row["fetched_at"])
        changed=timestamp(row["provider_last_changed_at"])
        if observed is None or row["source"]!="ThemeParks.wiki" or row["park_id"]!=attraction.park_id:
            return result
        age=(now-observed).total_seconds()
        historical=age>FRESH_SECONDS or age<0 or (changed is not None and changed>observed)
        status=row["status"] if row["status"] in {"OPERATING","CLOSED","DOWN","REFURBISHMENT","UNKNOWN","UNAVAILABLE"} else "UNKNOWN"
        wait=row["standby_wait_minutes"]
        if type(wait) is not int or wait<0:
            wait=None
        labels={"OPERATING":"Operating","CLOSED":"Closed","DOWN":"Temporarily down","REFURBISHMENT":"Refurbishment","UNKNOWN":"Unknown","UNAVAILABLE":"Unavailable"}
        # Closed zero is never presented as a walk-on; the original row remains private.
        posted=wait if status=="OPERATING" and row["present"]==1 else None
        result.update(status=status,status_label=labels[status],posted_wait_minutes=posted,
            wait_label=f"{posted} min" if posted is not None else (labels[status] if status!="OPERATING" else "Wait unavailable"),
            observed_at=observed.isoformat(),observed_at_display=display_time(observed),
            provider_last_changed_at=changed.isoformat() if changed else None,
            provider_last_changed_display=display_time(changed),
            age_seconds=max(0,int(age)),freshness="historical" if historical else "recent",
            freshness_label="Historical snapshot" if historical else "Recent stored observation")
    except (ProviderError,ValueError,TypeError):
        return empty_card(attraction)
    return result


def read_current_waits(db_path=DB,registry_path=REGISTRY,*,now=None):
    now=now or utc_now()
    result={"source":"ThemeParks.wiki","attribution":{"text":"Powered by ThemeParks.wiki","url":"https://www.themeparks.wiki"},
        "as_of":now.isoformat(),"freshness_threshold_seconds":FRESH_SECONDS,"available":False,
        "message":"Stored observations are unavailable. Please try again later.","parks":[]}
    try:
        registry=tuple(a for a in load_registry(registry_path) if a.collectable and a.attraction_id in APPROVED)
        if len(registry)!=7 or {a.attraction_id for a in registry}!=APPROVED:
            return result
        result["parks"]=[{"name":name,"attractions":[empty_card(a) for a in registry if a.park_id==park]} for park,name in PARK_NAMES.items()]
        with closing(sqlite3.connect(Path(db_path).resolve().as_uri()+"?mode=ro",uri=True,timeout=0.25)) as db:
            db.row_factory=sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")  # One consistent WAL read snapshot, promptly closed.
            for group,(park,_) in zip(result["parks"],PARK_NAMES.items()):
                latest=db.execute("SELECT id FROM runs WHERE park_id=? AND outcome='success' ORDER BY completed_at DESC,id DESC LIMIT 1",(park,)).fetchone()
                rows={} if latest is None else {r["attraction_id"]:r for r in db.execute(
                    "SELECT attraction_id,park_id,fetched_at,provider_last_changed_at,status,standby_wait_minutes,source,present FROM observations WHERE run_id=?",(latest["id"],))}
                group["attractions"]=[card(a,rows.get(a.attraction_id),now) for a in registry if a.park_id==park]
        cards=[a for p in result["parks"] for a in p["attractions"]]
        result["available"]=any(a["observed_at"] for a in cards)
        result["message"]="Showing the latest successfully stored observations. This is not a direct real-time feed." if result["available"] else "No observations have been stored yet."
        return result
    except (sqlite3.Error,OSError,ProviderError,ValueError,TypeError):
        # Never return database paths, SQL errors, raw bodies, or logs.
        if result["parks"]:
            result["parks"]=[{"name":name,"attractions":[empty_card(a) for a in registry if a.park_id==park]} for park,name in PARK_NAMES.items()]
        return result
