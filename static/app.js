"use strict";
const form = document.querySelector("#prediction-form");
const button = document.querySelector("#submit-button");
const content = document.querySelector("#result-content");
const panel = document.querySelector("#result-panel");
const error = document.querySelector("#error-message");
const allowed = id => Array.from(document.querySelector(id).options).map(option => option.value).filter(Boolean);
function message(title, description) {
  const heading = document.createElement("h3");
  heading.textContent = title;
  const text = document.createElement("p");
  text.className = "muted";
  text.textContent = description;
  content.replaceChildren(heading, text);
}
form.addEventListener("change", () => {
  error.hidden = true;
  message("Ready for a new estimate.", "Submit your selections to update the result.");
});
form.addEventListener("submit", async event => {
  event.preventDefault();
  error.hidden = true;
  if (!form.reportValidity()) return;
  const ride = form.elements.ride.value;
  const day = form.elements.day.value;
  const rawHour = form.elements.hour.value;
  const hour = Number(rawHour);
  if (!allowed("#ride").includes(ride) || !allowed("#day").includes(day) || !allowed("#hour").includes(rawHour) || !Number.isInteger(hour) || hour < 8 || hour > 22) {
    error.textContent = "Choose a supported ride, weekday, and hour from 08:00 through 22:00.";
    error.hidden = false;
    return;
  }
  button.disabled = true;
  for (const select of form.querySelectorAll("select")) select.disabled = true;
  button.textContent = "Exploring your estimate...";
  panel.setAttribute("aria-busy", "true");
  message("A moment of anticipation...", "Generating your synthetic wait estimate.");
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch("/api/predict", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ride, day, hour}), signal: controller.signal});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "The estimate is unavailable. Please try again.");
    if (!Number.isInteger(data.predicted_minutes) || data.predicted_minutes < 0 || data.synthetic !== true) throw new Error("The estimate is unavailable. Please try again.");
    const caption = document.createElement("p");
    caption.className = "result-ride";
    caption.textContent = ride;
    const number = document.createElement("p");
    number.className = "minutes";
    number.textContent = data.predicted_minutes;
    const units = document.createElement("span");
    units.textContent = " minutes";
    number.append(units);
    const summary = document.createElement("p");
    summary.className = "muted";
    summary.textContent = `${day} at ${String(hour).padStart(2, "0")}:00 / rounded estimate`;
    content.replaceChildren(caption, number, summary);
  } catch (failure) {
    message("Let's give that another try.", "Your selections are still here.");
    error.textContent = failure.name === "AbortError" ? "The request took too long. Please try again." : failure instanceof TypeError ? "Cannot reach the local app. Check that it is running and try again." : failure.message;
    error.hidden = false;
  } finally {
    clearTimeout(timeout);
    button.disabled = false;
    for (const select of form.querySelectorAll("select")) select.disabled = false;
    button.textContent = "Explore wait estimate \u2192";
    panel.setAttribute("aria-busy", "false");
  }
});
