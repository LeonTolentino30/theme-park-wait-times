"use strict";
(() => {
  const root = document.querySelector("#park-map-section");
  const load = document.querySelector("#load-park-map");
  const feedback = document.querySelector("#map-feedback");
  const list = document.querySelector("#map-ride-list");
  let catalog = null, pending = null, map = null, tiles = null, layer = null, selected = null, filter = "all";
  const markerById = new Map();
  async function data() {
    if (catalog) return catalog;
    if (!pending) pending = fetch("/api/map-attractions", {cache: "default"}).then(response => {
      if (!response.ok) throw new Error("Map catalog unavailable");
      return response.json();
    }).then(value => {
      if (!Array.isArray(value.features) || value.features.length !== 7) throw new Error("Map catalog invalid");
      catalog = value; return value;
    }).catch(error => { pending = null; throw error; });
    return pending;
  }
  function choose(feature, pan = true) {
    selected = feature;
    const properties = feature.properties;
    document.querySelector("#map-info-name").textContent = properties.name;
    document.querySelector("#map-info-park").textContent = properties.park_name;
    document.querySelector("#map-info-note").textContent = "Approximate attraction location. No live wait time is shown. Synthetic estimates are a separate demo.";
    const source = document.querySelector("#map-info-source");
    source.href = properties.source_url; source.hidden = false;
    const link = document.querySelector("#map-synthetic-link"); link.hidden = false;
    for (const button of list.querySelectorAll("button")) button.setAttribute("aria-pressed", String(button.dataset.mapId === feature.id));
    if (map && pan) map.panTo([feature.geometry.coordinates[1], feature.geometry.coordinates[0]]);
    if (map && markerById.has(feature.id)) markerById.get(feature.id).openTooltip();
  }
  function visible() { return catalog.features.filter(feature => filter === "all" || feature.properties.park === filter); }
  function renderMarkers() {
    if (!map) return;
    layer.clearLayers(); markerById.clear();
    const shown = visible();
    shown.forEach(feature => {
      const index = catalog.features.indexOf(feature) + 1;
      const icon = L.divIcon({className: `qa-map-pin pin-${feature.properties.park}`, html: `<span>${index}</span>`, iconSize: [44, 44], iconAnchor: [22, 44]});
      const marker = L.marker([feature.geometry.coordinates[1], feature.geometry.coordinates[0]], {icon, title: feature.properties.name, alt: `${feature.properties.name}, ${feature.properties.park_name}`, keyboard: true});
      const label = document.createElement("span"); label.textContent = feature.properties.name;
      marker.bindTooltip(label, {direction: "top", offset: [0, -36]});
      marker.on("click", () => choose(feature, false)); marker.addTo(layer); markerById.set(feature.id, marker);
    });
    if (shown.length) map.fitBounds(L.latLngBounds(shown.map(f => [f.geometry.coordinates[1], f.geometry.coordinates[0]])), {padding: [35, 35], maxZoom: 17, animate: false});
  }
  root.querySelectorAll("[data-map-park]").forEach(button => button.addEventListener("click", () => {
    filter = button.dataset.mapPark;
    root.querySelectorAll("[data-map-park]").forEach(item => item.setAttribute("aria-pressed", String(item === button)));
    list.querySelectorAll("button").forEach(item => { item.hidden = filter !== "all" && item.dataset.park !== filter; });
    if (selected && filter !== "all" && selected.properties.park !== filter) {
      selected = null; document.querySelector("#map-info-name").textContent = "Choose your next stop.";
      document.querySelector("#map-info-park").textContent = "Select a visible attraction.";
      document.querySelector("#map-info-source").hidden = true; document.querySelector("#map-synthetic-link").hidden = true;
      list.querySelectorAll("button").forEach(item => item.setAttribute("aria-pressed", "false"));
    }
    renderMarkers();
  }));
  list.addEventListener("click", async event => {
    const button = event.target.closest("[data-map-id]"); if (!button) return;
    try { const value = await data(); const feature = value.features.find(item => item.id === button.dataset.mapId); if (feature) choose(feature); }
    catch (_) { feedback.textContent = "Attraction details could not load. The ride names and parks remain available in the list."; }
  });
  document.querySelector("#map-synthetic-link").addEventListener("click", () => {
    if (!selected) return;
    const ride = document.querySelector("#ride"); ride.value = selected.properties.alias;
    ride.dispatchEvent(new Event("change", {bubbles: true}));
  });
  load.addEventListener("click", async () => {
    load.disabled = true; feedback.textContent = "Loading attraction map...";
    try {
      const value = await data();
      if (typeof L === "undefined") throw new Error("Leaflet unavailable");
      document.querySelector("#park-map").replaceChildren();
      const bounds = L.latLngBounds(value.features.map(f => [f.geometry.coordinates[1], f.geometry.coordinates[0]]));
      map = L.map("park-map", {minZoom: value.tiles.min_zoom, maxZoom: value.tiles.max_zoom, scrollWheelZoom: false, maxBounds: bounds.pad(0.8), maxBoundsViscosity: 1});
      layer = L.layerGroup().addTo(map);
      let failures = 0;
      tiles = L.tileLayer(value.tiles.url, {attribution: value.tiles.attribution_html, minZoom: value.tiles.min_zoom, maxZoom: value.tiles.max_zoom, keepBuffer: 0, updateWhenIdle: true, updateWhenZooming: false, referrerPolicy: "strict-origin-when-cross-origin"});
      tiles.on("tileerror", () => { failures++; feedback.textContent = "Some map tiles are unavailable. Markers and the attraction list still work; try again later."; });
      tiles.on("load", () => { if (!failures) feedback.textContent = "Pinch or use the zoom buttons. Tap a numbered marker or ride to explore. Locations are approximate."; });
      tiles.addTo(map); renderMarkers();
      if (selected) choose(selected, false);
      if (typeof ResizeObserver !== "undefined") new ResizeObserver(() => map.invalidateSize({pan: false})).observe(document.querySelector("#park-map"));
      feedback.textContent = "Map ready. Tap a marker or choose a ride. No live wait times are displayed.";
    } catch (_) {
      feedback.textContent = "The interactive map is unavailable. You can still choose attractions from the list.";
      load.disabled = false;
    }
  });
})();
