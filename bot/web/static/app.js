"use strict";

// TeamTalk Player management interface.
// Live values refresh quietly in place; only the results of the user's own
// actions are announced (status region) or reported as errors (alert region).

const REFRESH_MS = 4000;
const page = document.body.dataset.page;
const argument = document.body.dataset.argument;
const content = document.getElementById("content");

// ------------------------------------------------------------- utilities

function el(tag, attributes, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "text") node.textContent = value;
    else if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

let announceTimer = null;
function announce(message) {
  const region = document.getElementById("status");
  clearAlert();
  region.textContent = "";
  clearTimeout(announceTimer);
  // A short pause makes screen readers repeat a message that is the same as the last one.
  announceTimer = setTimeout(() => { region.textContent = message; }, 60);
}

function showError(message) {
  const region = document.getElementById("alert");
  region.textContent = "";
  setTimeout(() => { region.textContent = message; }, 60);
}

function clearAlert() {
  document.getElementById("alert").textContent = "";
}

async function api(method, path, body) {
  const options = { method, headers: { "X-Player-Request": "1" } };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    throw new Error("The bot could not be reached. It may be restarting; try again in a few seconds.");
  }
  let data = {};
  try { data = await response.json(); } catch (error) { /* not JSON */ }
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || ("The request failed (" + response.status + ")."));
  }
  return data;
}

// Runs an action: disables the button while it works, then announces the result.
async function act(button, work, workingText) {
  if (button) {
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
  }
  if (workingText) announce(workingText);
  try {
    const result = await work();
    if (result && result.message) announce(result.message);
    return result;
  } catch (error) {
    showError(error.message);
    return null;
  } finally {
    if (button && button.isConnected) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  }
}

function setText(id, text) {
  const node = document.getElementById(id);
  if (node && node.textContent !== text) node.textContent = text;
}

function formatClock(seconds) {
  if (seconds === null || seconds === undefined || isNaN(seconds)) return "";
  seconds = Math.max(0, Math.floor(seconds));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = String(seconds % 60).padStart(2, "0");
  return h ? h + ":" + String(m).padStart(2, "0") + ":" + s : m + ":" + s;
}

function formatTime(epoch) {
  if (!epoch) return "";
  return new Date(epoch * 1000).toLocaleString();
}

function nowPlaying(player) {
  if (!player || player.state === "Stopped" || !player.track) return "Nothing";
  let text = (player.state === "Paused" ? "Paused: " : "") + player.track;
  if (player.count > 1) text += " (track " + player.index + " of " + player.count + ")";
  return text;
}

function position(player) {
  if (!player || player.state === "Stopped" || player.position === undefined || player.position === null) return "";
  const total = player.duration ? " of " + formatClock(player.duration) : "";
  return formatClock(player.position) + total;
}

function getAt(object, path) {
  return path.split(".").reduce((value, key) => (value == null ? undefined : value[key]), object);
}

function setAt(object, path, value) {
  const keys = path.split(".");
  let target = object;
  for (const key of keys.slice(0, -1)) {
    if (typeof target[key] !== "object" || target[key] === null) target[key] = {};
    target = target[key];
  }
  target[keys[keys.length - 1]] = value;
}

function fieldId(path) {
  return "field-" + path.replace(/[^A-Za-z0-9_-]/g, "-");
}

// Messages that should survive a page change (e.g. "Server added").
function carryMessage(message) {
  sessionStorage.setItem("player-message", message);
}

function announceCarried() {
  const message = sessionStorage.getItem("player-message");
  if (message) {
    sessionStorage.removeItem("player-message");
    announce(message);
  }
}

function startRefreshing(callback, interval) {
  setInterval(() => { if (!document.hidden) callback(); }, interval || REFRESH_MS);
}

// ------------------------------------------------------------------ forms

let formCache = null;
async function loadForm() {
  if (!formCache) formCache = await api("GET", "/api/form");
  return formCache;
}

function renderField(field, value) {
  const id = fieldId(field.path);
  const helpId = field.help ? id + "-help" : undefined;
  const help = field.help ? el("p", { class: "help", id: helpId, text: field.help }) : null;
  let control;
  if (field.kind === "bool") {
    control = el("input", { type: "checkbox", id, "aria-describedby": helpId });
    control.checked = Boolean(value);
    return el("div", { class: "field checkbox" }, control, el("label", { for: id, text: field.label }), help);
  }
  if (field.kind === "select") {
    control = el("select", { id, "aria-describedby": helpId });
    const values = field.choices.map((choice) => choice.value);
    if (value !== undefined && value !== null && !values.includes(String(value))) {
      control.append(el("option", { value: String(value), text: String(value) }));
    }
    for (const choice of field.choices) {
      control.append(el("option", { value: choice.value, text: choice.label }));
    }
    control.value = String(value ?? "");
  } else if (field.kind === "lines" || field.kind === "json") {
    control = el("textarea", { id, rows: field.kind === "json" ? 4 : 3, "aria-describedby": helpId, spellcheck: "false" });
    control.value = field.kind === "lines" ? (value || []).join("\n") : JSON.stringify(value ?? {}, null, 2);
  } else if (field.kind === "int" || field.kind === "float") {
    control = el("input", {
      type: "number", id, "aria-describedby": helpId,
      step: field.kind === "float" ? "any" : "1",
      min: field.min, max: field.max,
    });
    control.value = value ?? "";
  } else {
    control = el("input", {
      type: field.kind === "password" ? "password" : "text",
      id, "aria-describedby": helpId, autocomplete: "off", spellcheck: "false",
    });
    control.value = value ?? "";
  }
  const parts = [el("label", { for: id, text: field.label }), control, help];
  if (field.kind === "password") {
    const toggleId = id + "-show";
    const toggle = el("input", {
      type: "checkbox", id: toggleId,
      onchange: () => { control.type = toggle.checked ? "text" : "password"; },
    });
    parts.push(el("div", { class: "checkbox small" }, toggle, el("label", { for: toggleId, text: "Show " + field.label.toLowerCase() })));
  }
  return el("div", { class: "field" }, ...parts);
}

function renderSections(sections, values, advancedPaths) {
  const normal = [];
  const advanced = [];
  for (const section of sections) {
    const fieldset = el("fieldset", { id: "section-" + section.path.replace(/\./g, "-") },
      el("legend", { text: section.title }),
      section.description ? el("p", { class: "help", text: section.description }) : null,
      section.fields.map((field) => renderField(field, getAt(values, field.path))),
    );
    (advancedPaths.includes(section.path) ? advanced : normal).push(fieldset);
  }
  const parts = [...normal];
  if (advanced.length) {
    parts.push(el("details", {}, el("summary", { text: "Advanced settings" }), ...advanced));
  }
  return parts;
}

// Reads the form back into a copy of `base`. Throws with the field to focus on errors.
function collectSections(sections, base) {
  const result = JSON.parse(JSON.stringify(base));
  for (const section of sections) {
    for (const field of section.fields) {
      const control = document.getElementById(fieldId(field.path));
      if (!control) continue;
      control.removeAttribute("aria-invalid");
      let value;
      if (field.kind === "bool") value = control.checked;
      else if (field.kind === "int") {
        if (control.value.trim() === "" || !Number.isInteger(Number(control.value))) {
          throw fieldError(control, field.label + " must be a whole number.");
        }
        value = Number(control.value);
      } else if (field.kind === "float") {
        if (control.value.trim() === "" || isNaN(Number(control.value))) {
          throw fieldError(control, field.label + " must be a number.");
        }
        value = Number(control.value);
      } else if (field.kind === "lines") {
        value = control.value.split("\n").map((line) => line.trim()).filter(Boolean);
      } else if (field.kind === "json") {
        try {
          value = control.value.trim() ? JSON.parse(control.value) : {};
        } catch (error) {
          throw fieldError(control, field.label + " is not valid JSON.");
        }
        if (typeof value !== "object" || Array.isArray(value) || value === null) {
          throw fieldError(control, field.label + " must be a JSON object, in curly braces.");
        }
      } else if (field.kind === "int_or_text") {
        value = /^\d+$/.test(control.value.trim()) ? Number(control.value.trim()) : control.value;
      } else {
        value = control.value;
      }
      setAt(result, field.path, value);
    }
  }
  return result;
}

function fieldError(control, message) {
  const error = new Error(message);
  error.control = control;
  return error;
}

function reportFormError(error) {
  showError(error.message);
  if (error.control) {
    error.control.setAttribute("aria-invalid", "true");
    const details = error.control.closest("details");
    if (details) details.open = true;
    error.control.focus();
  }
}

const ADVANCED = ["teamtalk.event_handling"];

// --------------------------------------------------------------- overview

const STATE_LABELS = {
  connected: "Connected", joining: "Joining channel", logging_in: "Logging in",
  connecting: "Connecting", reconnecting: "Reconnecting", off: "Off",
  stopped: "Not running", error: "Error",
};

async function showOverview() {
  const serverHeading = el("h2", { id: "servers-heading", text: "Server list" });
  const tableHolder = el("div", { id: "servers-table" });
  const processInfo = el("p", { id: "process-info" });
  content.replaceChildren(
    el("section", { "aria-labelledby": "servers-heading" },
      serverHeading,
      el("p", {}, el("a", { href: "/add", text: "Add a server" })),
      tableHolder),
    el("section", { "aria-labelledby": "process-heading" },
      el("h2", { id: "process-heading", text: "Bot process" }),
      processInfo),
  );
  let shownServers = "";
  async function refresh() {
    let state;
    try {
      state = await api("GET", "/api/state");
    } catch (error) {
      setText("process-info", error.message);
      return;
    }
    const names = state.servers.map((server) => server.name + ":" + server.enabled).join(",");
    if (names !== shownServers) {
      const focusedId = document.activeElement && document.activeElement.id;
      tableHolder.replaceChildren(serverTable(state.servers, refresh));
      shownServers = names;
      if (focusedId && document.getElementById(focusedId)) document.getElementById(focusedId).focus();
    }
    for (const server of state.servers) updateServerRow(server);
    setText("process-info", state.process.version + ", running since " + formatTime(state.process.started_at) + " (process " + state.process.pid + ").");
  }
  await refresh();
  announceCarried();
  startRefreshing(refresh);
}

function serverTable(servers, refresh) {
  if (!servers.length) {
    return el("p", {}, "There are no servers yet. ", el("a", { href: "/add", text: "Add the first one" }), ".");
  }
  const rows = servers.map((server) => {
    const id = "row-" + server.name;
    const toggle = el("button", {
      type: "button", id: "toggle-" + server.name,
      text: server.enabled ? "Turn off " + server.name : "Turn on " + server.name,
      onclick: async (event) => {
        const result = await act(event.target, () => api("POST", "/api/servers/" + encodeURIComponent(server.name) + "/enabled", { enabled: !server.enabled }),
          (server.enabled ? "Turning off " : "Turning on ") + server.name + "…");
        if (result) refresh();
      },
    });
    const reconnect = server.enabled ? el("button", {
      type: "button", id: "reconnect-" + server.name, text: "Reconnect " + server.name,
      onclick: (event) => act(event.target, () => api("POST", "/api/servers/" + encodeURIComponent(server.name) + "/restart"), "Reconnecting " + server.name + "…"),
    }) : null;
    return el("tr", { id },
      el("th", { scope: "row" }, el("a", { href: "/servers/" + encodeURIComponent(server.name), text: server.name })),
      el("td", { id: id + "-state" }),
      el("td", { id: id + "-playing" }),
      el("td", { id: id + "-volume" }),
      el("td", { id: id + "-listeners" }),
      el("td", { id: id + "-host" }),
      el("td", { class: "actions" }, toggle, " ", reconnect),
    );
  });
  return el("table", {},
    el("caption", { class: "visually-hidden", text: "Servers" }),
    el("thead", {}, el("tr", {},
      ["Server", "State", "Now playing", "Volume", "Others in channel", "Address", "Actions"].map((h) => el("th", { scope: "col", text: h })))),
    el("tbody", {}, rows));
}

function updateServerRow(server) {
  const id = "row-" + server.name;
  setText(id + "-state", server.state === "error" ? server.state_text : (STATE_LABELS[server.state] || server.state_text));
  setText(id + "-playing", server.running ? nowPlaying(server.player) : "");
  setText(id + "-volume", server.player ? String(server.player.volume) : "");
  setText(id + "-listeners", server.listeners === null ? "" : String(server.listeners));
  setText(id + "-host", server.host);
}


// ----------------------------------------------------------------- server

async function showServer(name) {
  const encoded = encodeURIComponent(name);
  const [form, data] = await Promise.all([loadForm(), api("GET", "/api/servers/" + encoded)]);
  let config = data.config;

  // Status
  const statusList = el("dl", { class: "status-list" });
  const statusItems = [
    ["state", "State"], ["host", "Address"], ["nickname", "Nickname"], ["channel", "Channel"],
    ["listeners", "Others in the channel"], ["playing", "Now playing"], ["position", "Position"],
    ["volume", "Volume"], ["service", "Search service"], ["mode", "Playback mode"],
  ];
  for (const [key, label] of statusItems) {
    statusList.append(el("dt", { text: label }), el("dd", { id: "status-" + key }));
  }
  const toggleButton = el("button", { type: "button", id: "toggle" });
  const reconnectButton = el("button", { type: "button", id: "reconnect", text: "Reconnect" });
  const removeButton = el("button", { type: "button", id: "remove", class: "danger", text: "Remove this server" });

  function updateStatus(status) {
    setText("status-state", status.state_text);
    setText("status-host", status.host);
    setText("status-nickname", status.nickname);
    setText("status-channel", status.channel);
    setText("status-listeners", status.listeners === null ? "" : String(status.listeners));
    setText("status-playing", status.running ? nowPlaying(status.player) : "");
    setText("status-position", position(status.player));
    setText("status-volume", status.player ? status.player.volume + " of " + status.player.max_volume : "");
    setText("status-service", status.player ? SERVICE_LABELS[status.player.current_service] || status.player.current_service : "");
    setText("status-mode", status.player ? MODE_LABELS[status.player.mode] || status.player.mode : "");
    const toggleText = status.enabled ? "Turn off" : "Turn on";
    if (toggleButton.textContent !== toggleText) toggleButton.textContent = toggleText;
    toggleButton.dataset.enabled = status.enabled ? "1" : "0";
    reconnectButton.hidden = !status.enabled;
    playback.hidden = !status.running;
    playbackOff.hidden = status.running;
  }

  toggleButton.addEventListener("click", async () => {
    const enable = toggleButton.dataset.enabled !== "1";
    const result = await act(toggleButton, () => api("POST", "/api/servers/" + encoded + "/enabled", { enabled: enable }),
      (enable ? "Turning on " : "Turning off ") + name + "…");
    if (result) { updateStatus(result.status); config.enabled = enable; }
  });
  reconnectButton.addEventListener("click", async () => {
    const result = await act(reconnectButton, () => api("POST", "/api/servers/" + encoded + "/restart"), "Reconnecting " + name + "…");
    if (result) updateStatus(result.status);
  });
  removeButton.addEventListener("click", async () => {
    if (!window.confirm("Remove " + name + "? The bot leaves that server and its settings are deleted.")) return;
    const result = await act(removeButton, () => api("DELETE", "/api/servers/" + encoded), "Removing " + name + "…");
    if (result) {
      carryMessage(result.message);
      window.location.href = "/";
    }
  });

  // Playback
  const playback = el("div", { id: "playback" }, ...playbackControls(name, (status) => updateStatus(status)));
  const playbackOff = el("p", { text: "Playback controls appear when the server is running." });

  // Settings
  const enabledField = renderField({ path: "enabled", label: "Connect to this server (turned on)", kind: "bool" }, config.enabled);
  const settingsForm = el("form", { id: "settings-form", novalidate: true },
    ...renderSections(form.server, config, ADVANCED),
    enabledField,
    el("button", { type: "submit", text: "Save settings" }),
    el("p", { class: "help", text: "Saving reconnects this server with the new settings. Other servers are not affected." }));



  settingsForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    let updated;
    try {
      updated = collectSections(form.server, config);
    } catch (error) {
      reportFormError(error);
      return;
    }
    updated.enabled = document.getElementById(fieldId("enabled")).checked;
    const button = settingsForm.querySelector("button[type=submit]");
    const result = await act(button, () => api("PUT", "/api/servers/" + encoded, { config: updated }), "Saving and reconnecting " + name + "…");
    if (result) {
      config = updated;
      updateStatus(result.status);
    }
  });

  // Log
  const logHolder = el("div", { id: "server-log" });

  content.replaceChildren(
    el("section", { "aria-labelledby": "h-status" },
      el("h2", { id: "h-status", text: "Status" }), statusList,
      el("div", { class: "button-row" }, toggleButton, reconnectButton)),
    el("section", { "aria-labelledby": "h-playback" },
      el("h2", { id: "h-playback", text: "Playback" }), playback, playbackOff),
    el("section", { "aria-labelledby": "h-settings" },
      el("h2", { id: "h-settings", text: "Settings" }), settingsForm),
    el("section", { "aria-labelledby": "h-log" },
      el("h2", { id: "h-log", text: "Recent log" }),
      el("p", {}, el("a", { href: "/log?server=" + encoded, text: "Full log for " + name })),
      logHolder),
    el("section", { "aria-labelledby": "h-remove" },
      el("h2", { id: "h-remove", text: "Remove" }),
      el("p", { class: "help", text: "Disconnects the bot from this server and deletes its settings." }),
      removeButton),
  );
  updateStatus(data.status);
  const log = logView(logHolder, name, 50);
  await log.refresh();
  announceCarried();
  startRefreshing(async () => {
    try {
      const fresh = await api("GET", "/api/servers/" + encoded);
      updateStatus(fresh.status);
    } catch (error) { /* keep the last values */ }
    log.refresh();
  });
}

const SERVICE_LABELS = { yt: "YouTube", am: "Apple Music" };
const MODE_LABELS = { st: "Single track", rt: "Repeat track", tl: "Track list", rtl: "Repeat track list", rnd: "Random" };

function playbackControls(name, onStatus) {
  const encoded = encodeURIComponent(name);
  async function player(button, action, value, working) {
    const result = await act(button, () => api("POST", "/api/servers/" + encoded + "/player", { action, value }), working);
    if (result && result.status) onStatus(result.status);
    return result;
  }

  function inputForm(id, label, help, buttonText, action, options) {
    const input = el("input", Object.assign({ type: "text", id, autocomplete: "off", spellcheck: "false", "aria-describedby": help ? id + "-help" : undefined }, options || {}));
    const button = el("button", { type: "submit", text: buttonText });
    const form = el("form", { class: "inline-form" },
      el("label", { for: id, text: label }), input, button,
      help ? el("p", { class: "help", id: id + "-help", text: help }) : null);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!input.value.trim() && options && options.required) {
        showError(label + " is empty.");
        input.focus();
        return;
      }
      await player(button, action, input.value.trim(), action === "play" ? "Searching…" : undefined);
    });
    return form;
  }

  function selectForm(id, label, choices, buttonText, action) {
    const select = el("select", { id }, choices.map(([value, text]) => el("option", { value, text })));
    const button = el("button", { type: "submit", text: buttonText });
    const form = el("form", { class: "inline-form" }, el("label", { for: id, text: label }), select, button);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      await player(button, action, select.value);
    });
    return form;
  }

  const buttons = [
    ["pause", "Pause or resume"], ["stop", "Stop"], ["previous", "Previous track"],
    ["next", "Next track"], ["seek_back", "Back"], ["seek_forward", "Forward"],
    ["alt", "Try another recording"],
  ].map(([action, text]) => el("button", {
    type: "button", text,
    onclick: (event) => player(event.target, action, "", action === "alt" ? "Looking for another recording…" : undefined),
  }));

  const commandInput = el("input", { type: "text", id: "command", autocomplete: "off", spellcheck: "false", "aria-describedby": "command-help" });
  const commandButton = el("button", { type: "submit", text: "Send" });
  const commandForm = el("form", { class: "inline-form" },
    el("label", { for: "command", text: "Bot command" }), commandInput, commandButton,
    el("p", { class: "help", id: "command-help", text: "Any command you could send the bot in TeamTalk, run as an admin. For example: h, cn New nickname, m rnd." }));
  commandForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const result = await act(commandButton, () => api("POST", "/api/servers/" + encoded + "/command", { command: commandInput.value }));
    if (result && result.status) onStatus(result.status);
  });

  return [
    inputForm("play-query", "Search", "Searches the current search service and plays the results.", "Search and play", "play", { required: true }),
    inputForm("play-url", "Link", "A YouTube or Apple Music link, or any stream URL.", "Play link", "url", { required: true, type: "url" }),
    el("div", { class: "button-row", role: "group", "aria-label": "Player" }, buttons),
    inputForm("volume", "Volume", "", "Set volume", "volume", { type: "number", min: "0", inputmode: "numeric" }),
    inputForm("track-number", "Track number", "Plays that track from the current list.", "Play track", "select", { type: "number", min: "1", inputmode: "numeric", required: true }),
    selectForm("service", "Search service", [["yt", "YouTube"], ["am", "Apple Music"]], "Use service", "service"),
    selectForm("mode", "Playback mode", Object.entries(MODE_LABELS), "Set mode", "mode"),
    commandForm,
  ];
}

// -------------------------------------------------------------------- add

// TeamTalk links ("tt://host?tcpport=…"), read the way BearWare's own clients
// read them (qtTeamTalk MainWindow::parseArgs; Android ServerListActivity):
// the address runs up to "?", "/" or "!"; parameters are percent-decoded
// ("+" stays "+"); ports are digits; encrypted is "true" or "1". Anything the
// link leaves out gets the official client's default.
const TT_LINK_DEFAULTS = {
  tcpport: 10333, udpport: 10333, encrypted: false,
  username: "", password: "", channel: "", chanpasswd: "",
};
const TT_LINK_PARAMETERS = Object.keys(TT_LINK_DEFAULTS);

// Like Qt's QUrl::fromPercentEncoding: decodes %XX (as UTF-8), leaves "+" and
// a lone "%" as they are.
function percentDecode(text) {
  try {
    return decodeURIComponent(text.replace(/%(?![0-9A-Fa-f]{2})/g, "%25"));
  } catch (error) {
    return text;  // not valid UTF-8 once decoded
  }
}

function parseTeamTalkLink(text) {
  const link = text.trim();
  const match = /^tt:\/+([^?!/]*)\/?\??/i.exec(link);
  if (!/^tt:/i.test(link)) return { error: "That is not a TeamTalk link. TeamTalk links start with tt://." };
  if (!match || !match[1]) return { error: "The TeamTalk link has no server address after tt://." };
  const result = { host: percentDecode(match[1]), given: new Set(), ignored: [], notes: [] };
  const raw = {};
  for (const part of link.slice(match[0].length).split("&")) {
    if (!part) continue;
    const equals = part.indexOf("=");
    const name = (equals < 0 ? part : part.slice(0, equals)).toLowerCase();
    const value = equals < 0 ? "" : part.slice(equals + 1);
    if (!TT_LINK_PARAMETERS.includes(name)) {
      if (!result.ignored.includes(name)) result.ignored.push(name);
    } else if (!(name in raw)) {
      raw[name] = value;  // like the official client, the first one counts
    }
  }
  const values = Object.assign({}, TT_LINK_DEFAULTS);
  for (const name of ["tcpport", "udpport"]) {
    if (!(name in raw)) continue;
    const digits = /^\d+/.exec(raw[name]);
    const port = digits ? Number(digits[0]) : NaN;
    if (port >= 1 && port <= 65535) {
      values[name] = port;
      result.given.add(name);
    } else {
      result.notes.push((name === "tcpport" ? "TCP" : "UDP") + " port " + JSON.stringify(raw[name]) + " is not a valid port, so TeamTalk's default " + TT_LINK_DEFAULTS[name] + " is used.");
    }
  }
  if ("encrypted" in raw) {
    values.encrypted = raw.encrypted.toLowerCase() === "true" || raw.encrypted === "1";
    result.given.add("encrypted");
  }
  for (const name of ["username", "password", "channel", "chanpasswd"]) {
    if (name in raw) {
      values[name] = percentDecode(raw[name]);
      result.given.add(name);
    }
  }
  // Not part of the link format, but common: "tt://host:7777". The official
  // clients would not connect to that address, so the port is split off.
  const hostPort = /^(.*[^:]):(\d{1,5})$/.exec(result.host);
  if (hostPort && !result.host.includes("]") && hostPort[1].indexOf(":") < 0) {
    result.host = hostPort[1];
    if (!result.given.has("tcpport")) {
      values.tcpport = Number(hostPort[2]);
      result.given.add("tcpport");
      result.notes.push("The port " + hostPort[2] + " was written after the address; it is used as the TCP port. TeamTalk links normally give it as tcpport=" + hostPort[2] + ".");
    } else {
      result.notes.push("The :" + hostPort[2] + " after the address was dropped, because the link also gives tcpport.");
    }
  }
  result.values = values;
  return result;
}

function suggestServerName(host, taken) {
  let base = host.replace(/^\[|\]$/g, "").toLowerCase();
  if (/^\d+(\.\d+){3}$/.test(base) || base.includes(":")) {
    base = "server-" + base.replace(/[.:]/g, "-");
  } else {
    const labels = base.split(".").filter((label) => label && !["www", "tt", "teamtalk"].includes(label));
    base = labels[0] || base;
  }
  base = base.replace(/[^a-z0-9._-]+/g, "-").replace(/^[^a-z0-9]+/, "").slice(0, 36) || "server";
  let name = base;
  for (let n = 2; taken.includes(name); n++) name = base + "-" + n;
  return name;
}

async function showAdd() {
  const [form, state] = await Promise.all([loadForm(), api("GET", "/api/state")]);
  const defaults = form.defaults;
  const essentials = ["teamtalk.hostname", "teamtalk.tcp_port", "teamtalk.udp_port", "teamtalk.encrypted",
    "teamtalk.username", "teamtalk.password", "teamtalk.nickname", "teamtalk.channel", "teamtalk.channel_password",
    "teamtalk.users.admins", "services.default_service"];
  const allFields = form.server.flatMap((section) => section.fields);
  const essentialSection = [{
    path: "essentials", title: "Connection and account", description: "",
    fields: essentials.map((path) => allFields.find((field) => field.path === path)).filter(Boolean),
  }];

  const nameInput = el("input", { type: "text", id: "server-name", required: true, autocomplete: "off", spellcheck: "false", "aria-describedby": "server-name-help", pattern: "[A-Za-z0-9][A-Za-z0-9._-]*", maxlength: "40" });
  const copySelect = el("select", { id: "copy-from", "aria-describedby": "copy-from-help" },
    el("option", { value: "", text: "Nothing, start from the defaults" }),
    state.servers.map((server) => el("option", { value: server.name, text: server.name })));
  const startNow = el("input", { type: "checkbox", id: "start-now" });
  startNow.checked = true;
  const fieldsHolder = el("div", {}, ...renderSections(essentialSection, defaults, []));
  const submit = el("button", { type: "submit", text: "Add server" });

  const linkInput = el("input", { type: "text", id: "tt-link", autocomplete: "off", spellcheck: "false", "aria-describedby": "tt-link-help", inputmode: "url" });
  let appliedLink = null;

  // Puts a parsed link into the form. Returns a spoken summary of what changed.
  function applyLink(parsed) {
    const set = (path, value) => {
      const control = document.getElementById(fieldId(path));
      if (!control) return;
      if (control.type === "checkbox") control.checked = Boolean(value);
      else control.value = value;
      control.removeAttribute("aria-invalid");
    };
    const v = parsed.values;
    set("teamtalk.hostname", parsed.host);
    set("teamtalk.tcp_port", v.tcpport);
    set("teamtalk.udp_port", v.udpport);
    set("teamtalk.encrypted", v.encrypted);
    set("teamtalk.username", v.username);
    set("teamtalk.password", v.password);
    // An empty channel means the root channel, as in the official client.
    set("teamtalk.channel", v.channel || "/");
    set("teamtalk.channel_password", v.chanpasswd);
    const describe = {
      tcpport: () => "TCP port " + v.tcpport,
      udpport: () => "UDP port " + v.udpport,
      encrypted: () => (v.encrypted ? "encrypted" : "not encrypted"),
      username: () => (v.username ? "username " + v.username : "no username"),
      password: () => (v.password ? "a password" : "no password"),
      channel: () => "channel " + (v.channel || "/ (the root channel)"),
      chanpasswd: () => (v.chanpasswd ? "a channel password" : "no channel password"),
    };
    const given = ["address " + parsed.host].concat(TT_LINK_PARAMETERS.filter((p) => parsed.given.has(p)).map((p) => describe[p]()));
    const defaulted = TT_LINK_PARAMETERS.filter((p) => !parsed.given.has(p)).map((p) => describe[p]());
    let summary = "Filled in from the link: " + given.join(", ") + ".";
    if (defaulted.length) summary += " Not in the link, so set to TeamTalk's defaults: " + defaulted.join(", ") + ".";
    if (parsed.ignored.length) summary += " Ignored unknown link parts: " + parsed.ignored.join(", ") + ".";
    if (parsed.notes.length) summary += " " + parsed.notes.join(" ");
    if (!nameInput.value.trim()) {
      nameInput.value = suggestServerName(parsed.host, state.servers.map((server) => server.name));
      summary += " Suggested server name: " + nameInput.value + ".";
    }
    return summary;
  }

  let appliedText = "";

  // trigger: "paste" or "enter" (focus is in the field), or "leave" (the
  // user tabbed away, so a problem is reported without moving focus back).
  function fillFromLink(trigger) {
    const text = linkInput.value.trim();
    const wasInvalid = linkInput.getAttribute("aria-invalid") === "true";
    linkInput.removeAttribute("aria-invalid");
    if (!text) {
      if (trigger === "enter") reportFormError(fieldError(linkInput, "Paste a TeamTalk link first."));
      else if (wasInvalid) clearAlert();  // the problem it reported is gone
      return;
    }
    if (trigger === "leave" && text === appliedText) return;  // already filled in when pasted
    const parsed = parseTeamTalkLink(text);
    if (parsed.error) {
      if (trigger === "leave") {
        linkInput.setAttribute("aria-invalid", "true");
        showError(parsed.error);
      } else {
        reportFormError(fieldError(linkInput, parsed.error));
      }
      return;
    }
    appliedLink = parsed;
    appliedText = text;
    announce(applyLink(parsed));
  }

  linkInput.addEventListener("paste", () => setTimeout(() => fillFromLink("paste"), 0));
  linkInput.addEventListener("change", () => fillFromLink("leave"));
  linkInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); fillFromLink("enter"); }
  });

  copySelect.addEventListener("change", async () => {
    let values = defaults;
    if (copySelect.value) {
      try {
        values = (await api("GET", "/api/servers/" + encodeURIComponent(copySelect.value))).config;
      } catch (error) { showError(error.message); return; }
    }
    fieldsHolder.replaceChildren(...renderSections(essentialSection, values, []));
    let message = copySelect.value ? "Filled in from " + copySelect.value + ". Change what differs." : "Filled in with the defaults.";
    if (appliedLink) {
      applyLink(appliedLink);
      message += " The connection details from the link were kept.";
    }
    announce(message);
  });

  const addForm = el("form", { id: "add-form", novalidate: true },
    el("div", { class: "field" },
      el("label", { for: "tt-link", text: "TeamTalk link (optional)" }),
      linkInput,
      el("p", { class: "help", id: "tt-link-help", text: "Paste a tt:// link, such as one from Generate TT URL in TeamTalk, to fill in the address, ports, encryption, account and channel. Pasting fills in the form; after typing or editing a link, press Enter. Anything the link leaves out gets TeamTalk's default." })),
    el("div", { class: "field" },
      el("label", { for: "server-name", text: "Server name (required)" }), nameInput,
      el("p", { class: "help", id: "server-name-help", text: "A short name for this server, such as home. Letters, numbers, dots, dashes and underscores." })),
    el("div", { class: "field" },
      el("label", { for: "copy-from", text: "Copy settings from" }), copySelect,
      el("p", { class: "help", id: "copy-from-help", text: "Starts with another server's settings (volume, services, player options and so on)." })),
    fieldsHolder,
    el("div", { class: "field checkbox" }, startNow, el("label", { for: "start-now", text: "Connect right away" })),
    submit,
    el("p", { class: "help", text: "Every other setting can be changed on the server's page after it is added." }));

  addForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = nameInput.value.trim();
    nameInput.removeAttribute("aria-invalid");
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$/.test(name)) {
      reportFormError(fieldError(nameInput, "Please give the server a name using letters, numbers, dots, dashes or underscores, starting with a letter or number."));
      return;
    }
    let config;
    try {
      config = collectSections(essentialSection, {});
    } catch (error) { reportFormError(error); return; }
    config.enabled = startNow.checked;
    const result = await act(submit, () => api("POST", "/api/servers", {
      name, config, copy_from: copySelect.value,
    }), "Adding " + name + "…");
    if (result) {
      carryMessage(result.message);
      window.location.href = "/servers/" + encodeURIComponent(name);
    }
  });

  content.replaceChildren(addForm);
  linkInput.focus();
}

// -------------------------------------------------------------------- log

function logView(holder, server, limit) {
  let lastId = 0;
  let level = "INFO";
  const tbody = el("tbody");
  const table = el("table", { class: "log" },
    el("caption", { class: "visually-hidden", text: "Log entries, oldest first" }),
    el("thead", {}, el("tr", {}, ["Time", "Server", "Level", "Message"].map((h) => el("th", { scope: "col", text: h })))),
    tbody);
  const empty = el("p", { text: "No log entries yet." });
  holder.replaceChildren(empty, table);
  table.hidden = true;

  async function refresh() {
    const params = new URLSearchParams({ after: String(lastId), level, limit: String(limit) });
    if (server !== null) params.set("server", server === "" ? "_bot" : server);
    let data;
    try { data = await api("GET", "/api/logs?" + params); } catch (error) { return; }
    for (const record of data.records) {
      lastId = Math.max(lastId, record.id);
      tbody.append(el("tr", { class: "level-" + record.level.toLowerCase() },
        el("td", { text: new Date(record.time * 1000).toLocaleTimeString() }),
        el("td", { text: record.server || "Bot" }),
        el("td", { text: record.level.charAt(0) + record.level.slice(1).toLowerCase() }),
        el("td", {}, el("pre", { text: record.message }))));
    }
    while (tbody.rows.length > limit) tbody.deleteRow(0);
    table.hidden = tbody.rows.length === 0;
    empty.hidden = tbody.rows.length !== 0;
  }

  return {
    refresh,
    reset(newServer, newLevel) {
      server = newServer;
      level = newLevel;
      lastId = 0;
      tbody.replaceChildren();
      return refresh();
    },
  };
}

async function showLog() {
  const state = await api("GET", "/api/state");
  const params = new URLSearchParams(window.location.search);
  const serverSelect = el("select", { id: "log-server" },
    el("option", { value: "*", text: "All" }),
    el("option", { value: "", text: "The bot itself" }),
    state.servers.map((server) => el("option", { value: server.name, text: server.name })));
  const levelSelect = el("select", { id: "log-level" },
    [["INFO", "Information and above"], ["WARNING", "Warnings and errors"], ["ERROR", "Errors only"]].map(([value, text]) => el("option", { value, text })));
  const auto = el("input", { type: "checkbox", id: "log-auto" });
  auto.checked = true;
  const holder = el("div", { id: "log-holder" });
  if (params.has("server")) serverSelect.value = params.get("server");
  content.replaceChildren(
    el("form", { class: "filters", onsubmit: (event) => event.preventDefault() },
      el("div", { class: "field" }, el("label", { for: "log-server", text: "Server" }), serverSelect),
      el("div", { class: "field" }, el("label", { for: "log-level", text: "Show" }), levelSelect),
      el("div", { class: "field checkbox" }, auto, el("label", { for: "log-auto", text: "Add new entries automatically" }))),
    el("p", { class: "help", text: "New entries are added quietly at the end of the table." }),
    holder);
  const selectedServer = () => (serverSelect.value === "*" ? null : serverSelect.value);
  const log = logView(holder, selectedServer(), 500);
  const apply = async () => {
    await log.reset(selectedServer(), levelSelect.value);
    announce("Showing " + holder.querySelectorAll("tbody tr").length + " entries.");
  };
  serverSelect.addEventListener("change", apply);
  levelSelect.addEventListener("change", apply);
  await log.reset(selectedServer(), levelSelect.value);
  startRefreshing(() => { if (auto.checked) log.refresh(); }, 3000);
}

// --------------------------------------------------------------- settings

async function showSettings() {
  const [form, settings] = await Promise.all([loadForm(), api("GET", "/api/settings")]);
  const values = settings.logger;
  const save = el("button", { type: "submit", text: "Save logging settings" });
  const settingsForm = el("form", { id: "logger-form", novalidate: true }, ...renderSections(form.logger, values, []), save);
  settingsForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    let logger;
    try { logger = collectSections(form.logger, values); } catch (error) { reportFormError(error); return; }
    // The logger form's paths have no "logger." prefix, so they are read at the top level.
    await act(save, () => api("PUT", "/api/settings", { logger }));
  });
  const restart = el("button", {
    type: "button", class: "danger", text: "Restart the whole bot",
    onclick: async (event) => {
      if (!window.confirm("Restart the bot? Every server disconnects and reconnects, and playback stops everywhere.")) return;
      const result = await act(event.target, () => api("POST", "/api/process/restart"));
      if (result) setTimeout(() => announce("The bot should be back now."), 8000);
    },
  });
  content.replaceChildren(
    el("section", { "aria-labelledby": "h-logging" }, el("h2", { id: "h-logging", text: "Logging" }), settingsForm),
    el("section", { "aria-labelledby": "h-restart" },
      el("h2", { id: "h-restart", text: "Restart" }),
      el("p", { class: "help", text: "Reconnects every server and reads the whole configuration file again. To reconnect a single server, use its own page." }),
      restart),
    el("section", { "aria-labelledby": "h-web" },
      el("h2", { id: "h-web", text: "This web interface" }),
      el("p", { text: "Listening on " + settings.web.host + ":" + settings.web.port + " inside the bot, reachable only over Tailscale. It has no password." })),
  );
}

// ---------------------------------------------------------------- matches

async function showMatches() {
  const search = el("input", { type: "search", id: "match-search", autocomplete: "off", "aria-describedby": "match-help" });
  const holder = el("div", { id: "matches" });
  const form = el("form", { class: "inline-form", role: "search" },
    el("label", { for: "match-search", text: "Find a song" }), search, el("button", { type: "submit", text: "Search" }),
    el("p", { class: "help", id: "match-help", text: "The YouTube recordings the bot remembers for Apple Music songs. Forget a wrong one and the song is matched again the next time it plays; or use Try another recording while it plays." }));
  async function load(announceCount) {
    let data;
    try { data = await api("GET", "/api/matches?q=" + encodeURIComponent(search.value.trim())); } catch (error) { showError(error.message); return; }
    if (!data.matches.length) {
      holder.replaceChildren(el("p", { text: search.value.trim() ? "No remembered song matches that." : "No songs are remembered yet." }));
    } else {
      holder.replaceChildren(el("table", {},
        el("caption", { class: "visually-hidden", text: "Remembered matches, newest first" }),
        el("thead", {}, el("tr", {}, ["Song", "Artist", "Recording", "Remembered", "Action"].map((h) => el("th", { scope: "col", text: h })))),
        el("tbody", {}, data.matches.map((match) => {
          const title = match.title || "Apple Music song " + match.id;
          const forget = el("button", {
            type: "button", text: "Forget " + title,
            onclick: async (event) => {
              const row = event.target.closest("tr");
              const result = await act(event.target, () => api("DELETE", "/api/matches/" + match.id));
              if (result) {
                const next = row.nextElementSibling || row.previousElementSibling;
                row.remove();
                const target = next ? next.querySelector("button") : search;
                target.focus();
              }
            },
          });
          return el("tr", {},
            el("th", { scope: "row" }, match.apple_url ? el("a", { href: match.apple_url, text: title }) : title),
            el("td", { text: match.artist }),
            el("td", {}, match.video_url ? el("a", { href: match.video_url, text: "Choice " + match.choice + " of " + match.candidates }) : "No match found"),
            el("td", { text: formatTime(match.time) }),
            el("td", {}, forget));
        }))));
    }
    if (announceCount) announce(data.matches.length + (data.matches.length === 1 ? " song." : " songs."));
  }
  form.addEventListener("submit", (event) => { event.preventDefault(); load(true); });
  content.replaceChildren(form, holder);
  await load(false);
}

// ------------------------------------------------------------------- start

const pages = {
  overview: showOverview,
  server: () => showServer(argument),
  add: showAdd,
  log: showLog,
  settings: showSettings,
  matches: showMatches,
  notfound: async () => { content.replaceChildren(el("p", {}, "This page does not exist. ", el("a", { href: "/", text: "Go to the server list" }), ".")); },
};

(pages[page] || pages.notfound)().catch((error) => {
  content.replaceChildren(el("p", { text: "This page could not be loaded: " + error.message }));
  showError(error.message);
});
