"""Escape-safe person-facing SVG charts, never an observation JSON feed."""
from datetime import timedelta
import math
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo


def display(when):
    return when.astimezone(ZoneInfo("America/New_York")).strftime("%b %d, %Y %I:%M %p %Z")


def svg_chart(view):
    if not view["meaningful"]:
        return None
    root = ET.Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "viewBox": "0 0 960 330", "class": "history-svg", "role": "img", "aria-label": "SYNTHETIC TEST FIXTURE / not real park observations" if view["fixture"] else "Historical posted standby waits / archived private local observations, not live waits"})
    def node(tag, attrs=None, text=None):
        child=ET.SubElement(root,tag,attrs or {})
        child.text=text
        return child
    node("title", text=f"{view['name']}: " + ("synthetic fixture chart; not real historical waits" if view["fixture"] else "historical posted standby waits; not measured queue durations or AI forecasts"))
    points=view["points"]
    ceiling=max(10,math.ceil(max(p["wait"] for p in points if p["wait"] is not None)/10)*10)
    span=(view["end"]-view["start"]).total_seconds()
    def x(p):return 65+(p["when"]-view["start"]).total_seconds()/span*850
    def y(p):return 255-p["wait"]/ceiling*215
    for tick in range(5):
        yy=255-tick/4*215
        node("line",{"x1":"65","x2":"915","y1":str(yy),"y2":str(yy),"class":"chart-grid"})
        node("text",{"x":"53","y":str(yy+4),"text-anchor":"end","class":"chart-label"},str(ceiling*tick/4))
    for tick in range(4):
        when=view["start"]+timedelta(seconds=span*tick/3)
        node("text",{"x":str(65+850*tick/3),"y":"282","text-anchor": "start" if tick==0 else "end" if tick==3 else "middle","class":"chart-label"},display(when))
    node("text",{"x":"65","y":"20","class":"chart-label"},"FIXTURE POSTED WAIT (MINUTES)" if view["fixture"] else "HISTORICAL POSTED WAIT (MINUTES)")
    path,previous=[],False
    for point in points:
        if point["wait"] is None:
            previous=False
            continue
        path.append(f"{'L' if previous and not point['gap'] else 'M'}{x(point):.3f},{y(point):.3f}")
        dot=node("circle",{"cx":f"{x(point):.3f}","cy":f"{y(point):.3f}","r":"3","class":"chart-sample"})
        ET.SubElement(dot,"title").text=f"{display(point['when'])}: {point['wait']} min / " + ("synthetic fixture" if view["fixture"] else "recorded posted standby wait")
        previous=True
    node("path",{"d":" ".join(path),"class":"chart-line"})
    return ET.tostring(root,encoding="unicode")
