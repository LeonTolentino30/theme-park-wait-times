"use strict";
(() => {
  const refresh = document.querySelector("#refresh-waits");
  const message = document.querySelector("#waits-message");
  const groups = document.querySelector("#waits-groups");
  function element(tag, className, text) {
    const node = document.createElement(tag);
    node.className = className;
    node.textContent = text;
    return node;
  }
  let ageTimer;
  function updateAges() {
    clearTimeout(ageTimer);
    for (const card of groups.querySelectorAll("[data-observed-at]")) {
      const observed = Date.parse(card.dataset.observedAt);
      if (!Number.isFinite(observed)) continue;
      if (Date.now() - observed > 900000 || observed > Date.now()) {
        const badge = card.querySelector(".snapshot-badge");
        badge.textContent = "Historical snapshot";
        badge.className = "snapshot-badge freshness-historical";
      }
    }
    // Only ages text already on screen. No network or collection requests.
    ageTimer = setTimeout(updateAges, 1000);
  }
  function render(data) {
    if (!Array.isArray(data.parks)) throw new Error("Invalid response");
    const fragment = document.createDocumentFragment();
    for (const park of data.parks) {
      const section = element("section", "park-group", "");
      section.setAttribute("aria-label", park.name);
      section.append(element("h3", "", park.name));
      const cards = element("div", "ride-cards", "");
      for (const attraction of park.attractions) {
        const card = element("article", "wait-card", "");
        card.dataset.observedAt = attraction.observed_at || "";
        const top = element("div", "wait-card-top", "");
        top.append(element("h4", "", attraction.name), element("span", `status-badge status-${attraction.status.toLowerCase()}`, attraction.status_label));
        const times = element("dl", "observation-times", "");
        times.append(element("dt", "", "Collected"), element("dd", "", attraction.observed_at_display), element("dt", "", "Provider last change"), element("dd", "", attraction.provider_last_changed_display));
        card.append(top, element("p", "posted-wait", attraction.wait_label), element("p", `snapshot-badge freshness-${attraction.freshness}`, attraction.freshness_label), times);
        cards.append(card);
      }
      section.append(cards);
      fragment.append(section);
    }
    groups.replaceChildren(fragment);
    message.textContent = data.message;
    updateAges();
  }
  refresh.addEventListener("click", async () => {
    refresh.disabled = true;
    message.textContent = "Reading the latest stored observations...";
    groups.setAttribute("aria-busy", "true");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch("/api/current-waits", {cache: "no-store", signal: controller.signal});
      const data = await response.json();
      if (!response.ok && response.status !== 503) throw new Error("Unavailable");
      render(data);
    } catch (_) {
      groups.replaceChildren();
      message.textContent = "Stored waits could not be refreshed. Please reload the page or try again.";
    } finally {
      clearTimeout(timeout);
      refresh.disabled = false;
      groups.setAttribute("aria-busy", "false");
    }
  });
  updateAges();
  // No timers poll the server or provider. Recheck timestamps on demand or page reload.
})();
