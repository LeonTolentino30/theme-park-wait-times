"""Bounded history queries over existing observations only; no writes or HTTP."""
from contextlib import closing
from datetime import timedelta
from pathlib import Path
import sqlite3

from .current_waits import DB, REGISTRY, PARK_NAMES, display_time
from .collection_pilot import APPROVED
from .themeparks_provider import load_registry, utc_now, timestamp, aware_utc, ProviderError

GAP_SECONDS=450
MAX_POINTS=2500


class HistoryInputError(ValueError):
    pass


class HistoryUnavailable(Exception):
    pass


def request_range(params,now):
    allowed={"attraction_id","hours","start","end"}
    if set(params)-allowed or not isinstance(params.get("attraction_id"),str):
        raise HistoryInputError("Provide a supported attraction_id and a valid time window.")
    if "start" in params or "end" in params:
        if "hours" in params or not params.get("start") or not params.get("end"):
            raise HistoryInputError("Provide both start and end with timezone offsets, without hours.")
        try:
            start,end=timestamp(params["start"]),timestamp(params["end"])
        except ProviderError:
            raise HistoryInputError("Dates must be ISO 8601 timestamps with timezone offsets.") from None
    else:
        hours=params.get("hours","24")
        if hours not in {"6","24","168"}:
            raise HistoryInputError("hours must be 6, 24, or 168.")
        end=now
        start=end-timedelta(hours=int(hours))
    if start is None or end is None or not start<end or end>now or end-start>timedelta(days=7):
        raise HistoryInputError("Choose an increasing date range of at most seven days, ending no later than now.")
    return start,end


def read_history(params,db_path=DB,registry_path=REGISTRY,*,now=None):
    now=aware_utc(now or utc_now())
    start,end=request_range(params,now)
    if params["attraction_id"] not in APPROVED:
        raise HistoryInputError("Choose one of the seven supported active attractions.")
    try:
        matches=[a for a in load_registry(registry_path) if a.collectable and a.attraction_id==params["attraction_id"]]
        if len(matches)!=1:
            raise HistoryUnavailable()
        attraction=matches[0]
        with closing(sqlite3.connect(Path(db_path).resolve().as_uri()+"?mode=ro",uri=True,timeout=0.25)) as db:
            db.row_factory=sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            rows=db.execute("""SELECT o.fetched_at,o.status,o.standby_wait_minutes,o.present,o.provider_last_changed_at
                FROM observations o JOIN runs r ON r.id=o.run_id
                WHERE o.attraction_id=? AND o.park_id=? AND o.source='ThemeParks.wiki' AND r.outcome='success'
                AND julianday(o.fetched_at)>=julianday(?) AND julianday(o.fetched_at)<=julianday(?)
                ORDER BY julianday(o.fetched_at),o.run_id LIMIT ?""",
                (attraction.attraction_id,attraction.park_id,start.isoformat(),end.isoformat(),MAX_POINTS+1)).fetchall()
        if len(rows)>MAX_POINTS:
            raise HistoryUnavailable()
    except (sqlite3.Error,OSError,ProviderError,ValueError,TypeError):
        raise HistoryUnavailable() from None
    points=[]
    previous=None
    for row in rows:
        try:
            when=timestamp(row["fetched_at"])
            if when is None or not start<=when<=end:
                continue
            changed=timestamp(row["provider_last_changed_at"])
            status=row["status"] if row["status"] in {"OPERATING","CLOSED","DOWN","REFURBISHMENT","UNKNOWN","UNAVAILABLE"} else "UNKNOWN"
            wait=row["standby_wait_minutes"]
            valid=status=="OPERATING" and row["present"]==1 and type(wait) is int and wait>=0 and (changed is None or changed<=when)
            event=None if valid else ("Wait unavailable" if status=="OPERATING" else status.replace("_"," ").title())
            points.append({"observed_at":when.isoformat(),"time_display":display_time(when),
                "posted_wait_minutes":wait if valid else None,"status":status,"event":event,
                "break_before":previous is not None and (when-previous).total_seconds()>GAP_SECONDS})
            previous=when
        except (ProviderError,ValueError,TypeError):
            # An unparseable record never becomes a numeric chart target.
            previous=None
            if points:
                points[-1]["break_after"]=True
    numeric=[p for p in points if p["posted_wait_minutes"] is not None]
    meaningful=len({p["observed_at"] for p in numeric})>=2
    return {"source":"ThemeParks.wiki","attribution":{"text":"Powered by ThemeParks.wiki","url":"https://www.themeparks.wiki"},
        "attraction_id":attraction.attraction_id,"name":attraction.provider_name,"park":PARK_NAMES[attraction.park_id],
        "timezone":"America/New_York","requested_start":start.isoformat(),"requested_end":end.isoformat(),
        "requested_period_display":f"{display_time(start)} to {display_time(end)}",
        "observed_period_display":f"{points[0]['time_display']} to {points[-1]['time_display']}" if points else None,
        "points":points,"numeric_observations":len(numeric),"meaningful_chart":meaningful,
        "gap_threshold_seconds":GAP_SECONDS,
        "message":"Showing recorded posted standby waits only. Lines connect adjacent recorded samples; missing periods remain breaks." if meaningful else "Not enough recorded numeric observations for a meaningful chart. At least two distinct observation times are needed.",
        "disclaimer":"Historical posted standby waits, not measured queue durations or AI forecasts."}
