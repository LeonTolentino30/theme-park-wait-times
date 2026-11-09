"use strict";
(() => {
  const form = document.querySelector("#history-form");
  const chart = document.querySelector("#history-chart");
  const events = document.querySelector("#history-events");
  const message = document.querySelector("#history-message");
  const period = document.querySelector("#history-period");
  const panel = document.querySelector("#history-panel");
  const button = document.querySelector("#history-button");
  const ns = "http://www.w3.org/2000/svg";
  function svgNode(tag, attrs, text) {
    const node = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function render(data) {
    message.textContent = data.message;
    period.textContent = `Requested: ${data.requested_period_display}. Recorded: ${data.observed_period_display || "No records in this window"}.`;
    chart.replaceChildren(); events.replaceChildren();
    const notices = data.points.filter(p => p.event || p.break_before);
    if (notices.length) {
      const title = document.createElement("h3"); title.textContent = "Closures, missing waits & collection gaps";
      const list = document.createElement("ul");
      for (const point of notices.slice(-20)) {
        const item = document.createElement("li");
        item.textContent = `${point.time_display}: ${[point.event, point.break_before ? "Collection gap before this observation" : null].filter(Boolean).join("; ")}`;
        list.append(item);
      }
      events.append(title, list);
      if (notices.length > 20) { const note = document.createElement("p"); note.textContent = "Showing the latest 20 events; all gaps remain visible in the chart."; events.append(note); }
    }
    if (!data.meaningful_chart) return;
    const start = Date.parse(data.requested_start), end = Date.parse(data.requested_end);
    const numeric = data.points.filter(p => p.posted_wait_minutes !== null);
    const max = Math.max(10, Math.ceil(Math.max(...numeric.map(p => p.posted_wait_minutes)) / 10) * 10);
    const x = p => 65 + (Date.parse(p.observed_at) - start) / (end - start) * 850;
    const y = p => 255 - p.posted_wait_minutes / max * 215;
    const svg = svgNode("svg", {viewBox: "0 0 960 330", role: "img", "aria-label": `${data.name}: historical posted waits in minutes. ${data.requested_period_display}. Gaps are not interpolated.`, class: "history-svg"});
    svg.append(svgNode("title", {}, `${data.name} / posted standby minutes`));
    for (let tick = 0; tick <= 4; tick++) {
      const value = max * tick / 4, yy = 255 - tick / 4 * 215;
      svg.append(svgNode("line", {x1: 65, x2: 915, y1: yy, y2: yy, class: "chart-grid"}), svgNode("text", {x: 53, y: yy + 4, "text-anchor": "end", class: "chart-label"}, String(value)));
    }
    const formatter = new Intl.DateTimeFormat("en-US", {timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short"});
    for (let tick = 0; tick <= 3; tick++) {
      const time = start + (end - start) * tick / 3;
      svg.append(svgNode("text", {x: 65 + tick / 3 * 850, y: 282, "text-anchor": tick === 0 ? "start" : tick === 3 ? "end" : "middle", class: "chart-label"}, formatter.format(time)));
    }
    svg.append(svgNode("text", {x: 65, y: 20, class: "chart-label"}, "POSTED WAIT (MINUTES)"));
    let path = "", previous = false;
    for (const point of data.points) {
      if (point.posted_wait_minutes === null) { previous = false; continue; }
      path += `${previous && !point.break_before ? "L" : "M"}${x(point)},${y(point)} `;
      const dot = svgNode("circle", {cx: x(point), cy: y(point), r: 3, class: "chart-sample"});
      dot.append(svgNode("title", {}, `${point.time_display}: ${point.posted_wait_minutes} min`));
      svg.append(dot);
      previous = !point.break_after;
    }
    svg.prepend(svgNode("path", {d: path, class: "chart-line"}));
    chart.append(svg);
  }
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    chart.replaceChildren(); events.replaceChildren(); period.textContent = "";
    message.textContent = "Reading recorded observations...";
    button.disabled = true;
    for (const select of form.querySelectorAll("select")) select.disabled = true;
    panel.setAttribute("aria-busy", "true");
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const query = new URLSearchParams({attraction_id: document.querySelector("#history-ride").value, hours: document.querySelector("#history-window").value});
      const response = await fetch(`/api/history?${query}`, {cache: "no-store", signal: controller.signal});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Stored history is temporarily unavailable.");
      render(data);
    } catch (error) { message.textContent = error.name === "AbortError" ? "The request timed out. Please try again." : "Historical observations could not be displayed. Please check your selections and try again."; }
    finally { clearTimeout(timeout); button.disabled = false; for (const select of form.querySelectorAll("select")) select.disabled = false; panel.setAttribute("aria-busy", "false"); }
  });
  form.addEventListener("change", () => { chart.replaceChildren(); events.replaceChildren(); period.textContent = ""; message.textContent = "Select Show wait trends to load this observation window."; });
})();
