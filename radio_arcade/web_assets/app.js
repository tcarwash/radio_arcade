"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const state = { snapshot: null, selectedId: null, session: null, listSignature: "", sessionSignature: "", actionSignature: "", chatSignature: "", busy: false, toastTimer: null, setupShown: false, islandSelection: null, lastTankAnimation: null, lastTankMotion: null };
const RESOURCE_META = [
  ["timber", "♣"], ["clay", "⬟"], ["wool", "✿"], ["grain", "✳"], ["ore", "◈"],
];
const PLAYER_COLORS = ["#8edbfa", "#ffb777", "#d6a8ff", "#a2e4a5"];
const RESOURCE_COLORS = ["#365f4e", "#77503e", "#5b4b76", "#78633e", "#415e73", "#365f4e"];
const GAME_PREVIEWS = {
  "scorched-earth": ["◈", "Artillery duels · Aim, fire, and reshape the terrain."],
  "open-word-tiles": ["▦", "Build words on a shared board and chase the high score."],
  "connect-four": ["●", "Drop discs, build a line, and outthink your opponent."],
  "tic-tac-toe": ["✕", "A quick classic for a friendly on-air rivalry."],
  nim: ["◇", "Take turns removing pieces. Plan the final move."],
};

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  let data;
  try { data = await response.json(); }
  catch { throw new Error(`The game room returned an invalid response (${response.status}).`); }
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}

function notify(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("visible");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => toast.classList.remove("visible"), 3800);
}

function byteLength(text) { return new TextEncoder().encode(text).length; }
function isPractice() { return state.snapshot?.transport === "practice"; }
function isObserver() { return Boolean(state.snapshot?.observer_mode); }
function canAct(session = state.session) {
  return Boolean(session && !isObserver() && session.status === "active" && !session.has_pending_action &&
    (isPractice() || state.snapshot?.tx_enabled) &&
    (isPractice() || session.current_player === state.snapshot?.callsign));
}
function canTransmit() {
  return Boolean(state.snapshot?.ready && !isObserver() && (isPractice() || state.snapshot?.tx_enabled));
}

function switchPane(pane) {
  if (!["lobby", "arena", "station"].includes(pane)) return;
  $(".workspace").dataset.activePane = pane;
  for (const button of document.querySelectorAll(".mobile-tab")) {
    const selected = button.dataset.pane === pane;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-pressed", String(selected));
  }
  window.scrollTo({ top: 0, behavior: "instant" });
}

function placeSessionActions() {
  const actions = $("#session-actions");
  if (window.matchMedia("(max-width: 800px)").matches) {
    const banner = $("#turn-banner");
    if (actions.previousElementSibling !== banner) banner.after(actions);
  } else if (actions.parentElement !== $(".lobby-panel")) {
    $(".lobby-panel").append(actions);
  }
}

function updateTableSetup() {
  const practice = isPractice();
  const hosted = $("#table-mode").value === "hosted" && !practice;
  const peer = $("#peer-input");
  const canCreate = canTransmit();
  $("#table-mode").disabled = !canCreate || practice;
  $("#peer-label").hidden = practice || hosted;
  peer.hidden = practice || hosted;
  peer.disabled = !canCreate || practice || hosted;
  $("#create-game").textContent = practice ? "Start local match →" : hosted ? "Open broadcast table →" : "Create table →";
  $("#lobby-note").textContent = !state.snapshot?.ready
    ? "Connect your station in settings to start playing."
    : isObserver() ? "Receive-only station: observe existing tables or disable observer mode in settings."
    : practice ? "Practice mode · Two or more players share this device. No radio transmission needed."
    : hosted ? "Host as dealer · Stations can discover and request to join your open table."
    : "Direct table · Enter a callsign for an invitation, or invite a station after creating the table.";
}

function setConnection(snapshot) {
  const status = $("#connection-status");
  status.classList.toggle("offline", !snapshot.ready || !snapshot.connected);
  const label = status.lastElementChild;
  if (!snapshot.ready) label.textContent = snapshot.callsign ? "Engine offline" : "Set up station";
  else if (isPractice()) label.textContent = `Local practice · ${snapshot.callsign}`;
  else if (!snapshot.connected) label.textContent = "Data2G offline";
  else label.textContent = `${snapshot.transport === "broadcast" ? "Broadcast" : "Connected"} · ${snapshot.callsign}`;
  $("#transport-label").textContent = !snapshot.ready ? "STATION OFFLINE" : isPractice() ? "LOCAL PRACTICE · HOT-SEAT" : `${snapshot.transport.toUpperCase()} · ${snapshot.group}`;
  document.documentElement.dataset.scheme = snapshot.color_scheme || "midnight";
  const tx = $("#tx-toggle");
  tx.textContent = snapshot.tx_enabled ? "TX ENABLED" : "TX DISABLED";
  tx.setAttribute("aria-pressed", String(Boolean(snapshot.tx_enabled)));
  tx.classList.toggle("tx-off", !snapshot.tx_enabled);
  tx.disabled = isPractice();
  tx.title = isPractice() ? "Local practice does not use radio transmission" : "Allow or block radio transmission";
  const activity = $("#tx-activity"), lastTx = snapshot.last_transmission;
  const recentTx = lastTx && Date.now() / 1000 - (lastTx.completed || lastTx.started) < 6;
  activity.classList.toggle("active", Boolean(snapshot.transmitting));
  activity.textContent = snapshot.transmitting && lastTx
    ? `SENDING · ${lastTx.kind} to ${lastTx.destination} · ${lastTx.reason}`
    : recentTx && lastTx ? `TX SENT · ${lastTx.kind} to ${lastTx.destination} · ${lastTx.reason}` : "";
  activity.title = activity.textContent;
}

function populateGames(catalog) {
  catalog = catalog.filter(game => !game.clients || game.clients.includes("web"));
  const select = $("#game-select");
  const keys = catalog.map(game => game.key).join("|");
  if (select.dataset.keys === keys) return;
  const selected = select.value;
  select.replaceChildren();
  for (const game of catalog) {
    const option = node("option", "", `${game.name}  ·  ${game.min_players}–${game.max_players} players`);
    option.value = game.key;
    select.append(option);
  }
  select.dataset.keys = keys;
  if (catalog.some(game => game.key === selected)) select.value = selected;
  updateGamePreview();
}

function updateGamePreview() {
  const option = $("#game-select").selectedOptions[0];
  const [icon, description] = GAME_PREVIEWS[$("#game-select").value] || ["✦", "A new challenge awaits at this table."];
  $("#game-preview-icon").textContent = icon;
  $("#game-preview-name").textContent = option?.textContent?.split("  ·  ")[0] || "Choose your next match";
  $("#game-preview-description").textContent = description;
}

function renderSessionList(sessions) {
  const list = $("#session-list");
  const previous = state.selectedId;
  $("#session-count").textContent = sessions.length;
  $("#mobile-table-count").textContent = sessions.length;
  if (!sessions.some(session => session.id === previous)) state.selectedId = sessions[0]?.id || null;
  if (state.selectedId !== previous) { state.sessionSignature = ""; state.actionSignature = ""; state.chatSignature = ""; }
  const signature = JSON.stringify([state.selectedId, state.snapshot?.callsign, state.snapshot?.observer_mode,
    sessions.map(session => [session.id, session.game, session.status, session.current_player, session.players, session.coordinator, session.hosted])]);
  if (signature === state.listSignature) return;
  state.listSignature = signature;
  const scroll = list.scrollTop;
  list.replaceChildren();
  if (!sessions.length) {
    const empty = node("div", "empty-list", "No open tables yet. Start a game or wait for an invitation to come over the air.");
    list.append(empty);
    return;
  }
  for (const session of sessions) {
    const card = node("button", `session-card${session.id === state.selectedId ? " selected" : ""}`);
    card.type = "button";
    card.dataset.session = session.id;
    const top = node("div", "session-card-top");
    top.append(node("span", "session-name", session.game));
    const status = node("span", "session-state", session.current_player === state.snapshot.callsign ? "Your turn" : session.status);
    if (!["active", "finished"].includes(session.status)) status.classList.add("waiting");
    top.append(status);
    const summary = node("div", "session-summary");
    const other = isObserver() ? (session.players.join(" / ") || session.coordinator) :
      (session.players.find(player => player !== state.snapshot.callsign) || (session.hosted ? "Open table" : "Waiting for stations"));
    summary.append(node("span", "", other), node("span", "bullet", "·"),
      node("span", "", `${session.players.length} station${session.players.length === 1 ? "" : "s"}`),
      node("span", "bullet", "·"), node("span", "", session.id.slice(-6)));
    card.append(top, summary);
    list.append(card);
  }
  list.scrollTop = scroll;
}

function setTurnBanner(session) {
  const banner = $("#turn-banner");
  let text = "", tone = "";
  if (isObserver()) { text = session.view ? "OBSERVING · RECEIVE ONLY" : "AWAITING GAME SNAPSHOT · RECEIVE ONLY"; tone = "warning"; }
  else if (session.status === "invited") { text = `INVITATION FROM ${session.coordinator} · ACCEPT TO JOIN`; tone = "warning"; }
  else if (session.status === "lobby") { text = session.hosted ? (session.coordinator === state.snapshot.callsign ? "DEALER · OPEN TO JOIN REQUESTS" : "PUBLIC TABLE · JOIN TO PLAY") : "LOBBY · INVITE STATIONS, THEN START"; tone = "waiting"; }
  else if (session.status === "joining") { text = session.hosted ? "JOIN REQUEST SENT · WAITING FOR DEALER" : "WAITING FOR THE HOST TO START"; tone = "waiting"; }
  else if (session.status === "finished") { text = session.view?.winner ? `GAME WON BY ${session.view.winner}` : "GAME OVER · DRAW"; tone = "warning"; }
  else if (session.has_pending_action) { text = "MOVE SENT · WAITING FOR COMMIT"; tone = "waiting"; }
  else if (session.status === "active") {
    text = isPractice() ? `LOCAL PRACTICE · ${session.current_player || "GAME OVER"} TO MOVE` :
      session.current_player === state.snapshot.callsign ? "YOUR TURN · MAKE YOUR MOVE" : `WAITING FOR ${session.current_player || "THE TABLE"}`;
    tone = session.current_player === state.snapshot.callsign || isPractice() ? "" : "waiting";
    if (session.view?.kind === "settlement") text += ` · ${session.view.phase.toUpperCase()} PHASE`;
  } else { text = session.status.toUpperCase(); tone = "waiting"; }
  if (session.error) { text = `MOVE REJECTED · ${session.error}`; tone = "warning"; }
  banner.textContent = text;
  banner.className = `turn-banner ${tone}`.trim();
  banner.hidden = false;
}

function renderRoster(session) {
  const roster = $("#roster");
  roster.className = "roster";
  roster.replaceChildren();
  const players = session.players || [];
  const maxPlayers = state.snapshot.catalog.find(game => game.game_id === session.game_id)?.max_players ?? players.length;
  $("#player-count").textContent = `${players.length} / ${maxPlayers}`;
  for (const [index, player] of players.entries()) {
    const entry = node("div", "roster-entry");
    const avatar = node("span", `station-avatar color-${index % PLAYER_COLORS.length}`, player.slice(0, 2));
    const info = node("div", "station-info");
    info.append(node("div", "station-name", player));
    info.append(node("div", "station-caption", player === session.current_player ? "Taking this turn" : player === session.coordinator ? "Table host" : "At the table"));
    const indicator = node("span", `station-indicator${player === session.current_player ? " active" : ""}`);
    entry.append(avatar, info, indicator);
    roster.append(entry);
  }
  for (const player of session.invited || []) {
    const entry = node("div", "roster-entry");
    entry.append(node("span", "station-avatar color-2", "…"));
    const info = node("div", "station-info");
    info.append(node("div", "station-name", player), node("div", "station-caption", "Invitation pending"));
    entry.append(info, node("span", "station-indicator"));
    roster.append(entry);
  }
  if (!players.length && isObserver()) {
    const text = session.view ? "Listening in on this table." : "This table is waiting for a snapshot.";
    roster.classList.add("empty-roster");
    roster.append(node("span", "roster-icon", "◌"), node("p", "", text));
  }
  const lastMove = $("#last-move");
  if (session.last_move) {
    lastMove.hidden = false;
    lastMove.replaceChildren(node("strong", "", `LAST MOVE · ${session.last_move.actor}`), document.createTextNode(session.last_move.description));
  } else lastMove.hidden = true;
}

function addButton(parent, label, onClick, primary = false, disabled = false) {
  const button = node("button", `button${primary ? " primary" : ""}`, label);
  button.type = "button";
  button.disabled = disabled;
  button.addEventListener("click", onClick);
  parent.append(button);
  return button;
}
function addActionInput(parent, placeholder, id) {
  const input = node("input", "action-input");
  input.placeholder = placeholder;
  input.id = id;
  input.autocomplete = "off";
  parent.append(input);
  return input;
}

function renderSessionActions(session) {
  const actions = $("#session-actions");
  actions.replaceChildren();
  const command = (name, extra = {}) => sendCommand(name, { session_id: session.id, ...extra });
  const owner = session.coordinator === state.snapshot.callsign;
  if (!isObserver() && session.status === "invited") addButton(actions, "Accept invitation", () => command("accept_invite"), true, !canTransmit());
  if (!isObserver() && session.hosted && ["lobby", "joining"].includes(session.status) && !session.players.includes(state.snapshot.callsign))
    addButton(actions, session.status === "joining" ? "Retry join request" : "Join hosted table", () => command("join_game"), true, !canTransmit());
  if (!isObserver() && session.hosted && session.status === "lobby" && owner) {
    const order = addActionInput(actions, `Turn order · ${state.snapshot.callsign},GUEST1,…`, "reply-order");
    const row = node("div", "action-row"); actions.append(row);
    addButton(row, "Set reply order", () => command("set_reply_order", { order: order.value }), false, !canTransmit());
    addButton(row, "Re-announce", () => command("announce_game"), false, !canTransmit());
  }
  if (!isObserver() && !session.hosted && session.status === "lobby" && owner) {
    const peer = addActionInput(actions, "Station callsign to invite", "invite-peer");
    addButton(actions, "Invite station", () => command("invite_player", { peer: peer.value }), false, !canTransmit());
    if (session.invited?.length) {
      const cancelPeer = addActionInput(actions, "Pending station callsign", "cancel-peer");
      addButton(actions, "Cancel invitation", () => command("cancel_invitation", { peer: cancelPeer.value }), false, !canTransmit());
    }
  }
  if (!isObserver() && session.status === "lobby" && owner)
    addButton(actions, "Start this game", () => command("start_game"), true, !canTransmit() || session.invited?.length > 0);
  if (isObserver() || (!owner && ["active", "finished"].includes(session.status)))
    addButton(actions, isObserver() ? "Request snapshot · transmits" : "Sync / recover state", () => {
      if (isObserver() && !window.confirm("This sends one synchronization request over the radio. Continue?")) return;
      command("request_sync");
    }, false, !state.snapshot?.tx_enabled && !isPractice());
  if (!isObserver() && session.hosted && session.has_pending_action)
    addButton(actions, "Retry pending move", () => command("resend_pending_action"), false, !canTransmit());
  if (!isObserver() && state.snapshot.transport === "connected") {
    const peer = addActionInput(actions, "Station callsign", "peer-connect");
    const row = node("div", "action-row"); actions.append(row);
    addButton(row, "Connect / resume", () => sendCommand("connect_peer", { peer: peer.value }), false, !canTransmit());
    addButton(row, "Disconnect", () => sendCommand("disconnect_peer"));
  }
  addButton(actions, "Delete game from this device", () => {
    if (!window.confirm("Delete this game's saved state, chat, and queued messages from this device? This does not notify other stations.")) return;
    command("delete_game");
  });
}

function renderScoreStrip(session, view) {
  const strip = node("div", "score-strip");
  const scores = view.scores || {};
  for (const [index, player] of session.players.entries()) {
    const card = node("div", `score-card${player === session.current_player ? " active" : ""}${view.winner === player ? " winner" : ""}`);
    card.append(node("div", "score-name", player));
    const score = scores[player];
    card.append(node("div", "score-value", typeof score === "number" ? `${score}${view.kind === "settlement" ? " VP" : ""}` : "—"));
    strip.append(card);
  }
  return strip;
}

function renderGrid(session, view, host) {
  const board = node("div", "board-tictactoe");
  const active = canAct(session);
  for (const [index, value] of view.cells.entries()) {
    const cell = node("button", `ttt-cell${value === "O" ? " mark-o" : ""}`, value.trim());
    cell.type = "button";
    cell.setAttribute("aria-label", `Square ${index + 1}${value.trim() ? `, ${value}` : ", empty"}`);
    cell.disabled = !active || Boolean(value.trim());
    cell.addEventListener("click", () => submitAction([index]));
    board.append(cell);
  }
  host.append(board, node("p", "turn-meta", active ? "Choose a square to play. Your move is committed only when selected." : "Follow the active station's turn. The board updates as commits arrive."));
}

function renderNim(session, view, host) {
  const piles = node("div", "nim-piles");
  view.piles.forEach((count, index) => {
    const row = node("div", "nim-pile");
    row.append(node("span", "nim-label", `PILE ${String(index + 1).padStart(2, "0")}`));
    row.append(node("span", "nim-stones", "◆ ".repeat(count).trim() || "—"));
    row.append(node("span", "nim-number", count));
    piles.append(row);
  });
  host.append(piles);
  const controls = node("form", "control-row");
  const label = node("label", "", "Take from");
  const pile = node("select", "field");
  view.piles.forEach((count, index) => {
    const option = node("option", "", `Pile ${index + 1} · ${count}`); option.value = index; option.disabled = count === 0; pile.append(option);
  });
  pile.value = String(view.piles.findIndex(count => count > 0));
  const amount = node("input", "field"); amount.type = "number"; amount.min = "1"; amount.value = "1"; amount.setAttribute("aria-label", "Number to take");
  function updateLimit() { const available = view.piles[Number(pile.value)] || 0; amount.max = String(available); if (Number(amount.value) > available) amount.value = String(Math.max(1, available)); }
  pile.addEventListener("change", updateLimit); updateLimit();
  const take = node("button", "button primary", "Take stones"); take.type = "submit"; take.disabled = !canAct(session);
  controls.append(label, pile, amount, take);
  controls.addEventListener("submit", event => {
    event.preventDefault();
    const selected = Number(pile.value), count = Number(amount.value);
    if (!canAct(session) || !Number.isInteger(count) || count < 1 || count > view.piles[selected]) {
      notify("Choose a non-empty pile and take no more stones than it contains."); return;
    }
    submitAction([selected, count]);
  });
  host.append(controls);
}

function parseWord(start, direction, word, cells) {
  const match = /^([A-Oa-o])(1[0-5]|[1-9])$/.exec(start.trim());
  if (!match) return { positions: new Map(), message: "Choose a starting square from A1 to O15." };
  const tokens = word.toUpperCase().match(/\{[A-Z]\}|[A-Z]/g) || [];
  if (tokens.join("") !== word.toUpperCase()) return { positions: new Map(), message: "Use letters, or {A} for a blank tile." };
  const x0 = match[1].toUpperCase().charCodeAt(0) - 65;
  const y0 = Number(match[2]) - 1;
  const positions = new Map();
  let conflict = false, overflow = false;
  tokens.forEach((token, offset) => {
    const x = x0 + (direction === "H" ? offset : 0);
    const y = y0 + (direction === "V" ? offset : 0);
    if (x > 14 || y > 14) { overflow = true; return; }
    const index = y * 15 + x;
    const letter = token.replace(/[{}]/g, "");
    if (cells[index] && cells[index].toUpperCase() !== letter) conflict = true;
    positions.set(index, letter);
  });
  return { positions, message: overflow ? "That word runs past the edge of the board." : conflict ? "One or more letters conflict with the board." : word ? "Preview only · Place word to validate and submit." : "Click a square, then enter your word." };
}

function renderWordTiles(session, view, host) {
  host.append(renderScoreStrip(session, view));
  const meta = node("div", "word-meta");
  meta.append(node("strong", "", `${view.lexicon} · OPEN RACKS`));
  meta.append(node("span", "", "Every rack is public; plan with the whole table."));
  const bag = node("span", "word-bag", `BAG · ${view.bag}`); meta.append(bag); host.append(meta);
  const boardShell = node("div", "word-board-shell");
  const axes = node("div", "word-axes"); axes.append(node("span", "", ""));
  for (let x = 0; x < 15; x++) axes.append(node("span", "", String.fromCharCode(65 + x)));
  boardShell.append(axes);
  const board = node("div", "word-board");
  const startInput = node("input", "field"); startInput.id = "word-start"; startInput.value = "H8"; startInput.setAttribute("aria-label", "Starting square"); startInput.hidden = true;
  const dir = node("select", "field"); dir.id = "word-direction";
  for (const [value, label] of [["H", "Across"], ["V", "Down"]]) { const option = node("option", "", label); option.value = value; dir.append(option); }
  const wordInput = node("input", "field"); wordInput.id = "word-text"; wordInput.placeholder = "Word · {A} uses a blank as A"; wordInput.autocomplete = "off";
  for (let y = 0; y < 15; y++) {
    const rowLabel = node("span", "word-row-label", y + 1); rowLabel.setAttribute("aria-hidden", "true");
    board.append(rowLabel);
    for (let x = 0; x < 15; x++) {
      const index = y * 15 + x, tile = view.cells[index], premium = view.premiums[index];
      const label = `${String.fromCharCode(65 + x)}${y + 1}`;
      const cell = node("button", `word-cell${tile ? " filled" : ` premium-${premium.toLowerCase()}`}${index === 112 && !tile ? " center-star" : ""}`, tile || (index === 112 ? "★" : premium));
      cell.type = "button"; cell.dataset.index = index; cell.setAttribute("aria-label", `${label}${tile ? `, ${tile}` : `, ${premium} premium`}`);
      cell.addEventListener("click", () => { startInput.value = label; updatePreview(); wordInput.focus(); });
      board.append(cell);
    }
  }
  boardShell.append(board); host.append(boardShell);
  const racks = node("div", "rack-list");
  for (const [player, tiles] of Object.entries(view.racks)) {
    const row = node("div", "rack-row"); row.append(node("span", "rack-name", `${player} RACK`));
    for (const tile of tiles) row.append(node("span", "rack-tile", tile === "·" ? "–" : tile));
    racks.append(row);
  }
  host.append(racks);
  const note = node("div", "preview-note", "Click a square, then enter your word."); host.append(note);
  const form = node("form", "word-controls");
  startInput.hidden = false;
  form.append(startInput, dir, wordInput);
  const place = node("button", "button primary", "Place word"); place.type = "submit"; place.disabled = !canAct(session); form.append(place);
  form.addEventListener("submit", event => { event.preventDefault(); sendCommand("place_word", { session_id: session.id, start: startInput.value, direction: dir.value, word: wordInput.value }); });
  host.append(form);
  const exchangeForm = node("form", "word-secondary");
  const exchange = node("input", "field"); exchange.placeholder = "Exchange letters · ? for blank"; exchange.setAttribute("aria-label", "Tiles to exchange");
  const exchangeButton = node("button", "button", "Exchange"); exchangeButton.type = "submit"; exchangeButton.disabled = !canAct(session) || view.bag < 7;
  const pass = node("button", "button", "Pass"); pass.type = "button"; pass.disabled = !canAct(session); pass.addEventListener("click", () => submitAction([1]));
  exchangeForm.append(exchange, exchangeButton, pass);
  exchangeForm.addEventListener("submit", event => { event.preventDefault(); sendCommand("exchange_word", { session_id: session.id, tiles: exchange.value }); });
  host.append(exchangeForm);
  function updatePreview() {
    const preview = parseWord(startInput.value, dir.value, wordInput.value, view.cells);
    note.textContent = preview.message;
    for (const cell of board.querySelectorAll(".word-cell")) {
      cell.classList.remove("preview", "preview-conflict");
      const index = Number(cell.dataset.index);
      if (preview.positions.has(index)) cell.classList.add(view.cells[index] && view.cells[index].toUpperCase() !== preview.positions.get(index) ? "preview-conflict" : "preview");
    }
  }
  startInput.addEventListener("input", updatePreview); dir.addEventListener("change", updatePreview); wordInput.addEventListener("input", updatePreview);
  updatePreview();
}

function sitePositions() {
  const corners = [[500, 52], [754, 196], [754, 484], [500, 628], [246, 484], [246, 196]];
  const sites = [];
  for (let i = 0; i < corners.length; i++) {
    const [x1, y1] = corners[i], [x2, y2] = corners[(i + 1) % corners.length];
    for (let step = 0; step < 3; step++) sites.push([x1 + (x2 - x1) * step / 3, y1 + (y2 - y1) * step / 3]);
  }
  return sites;
}
function svgElement(name, attrs = {}) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== undefined && value !== null) element.setAttribute(key, String(value));
  }
  return element;
}

function renderIsland(session, view, host) {
  host.append(renderScoreStrip(session, view));
  const layout = node("div", "island-layout");
  const mapWrap = node("div", "island-map-wrap");
  const svg = svgElement("svg", { class: "island-svg", viewBox: "0 0 1000 690", role: "img", "aria-label": "Interactive Island Settlers map" });
  const sites = sitePositions();
  const colors = Object.fromEntries(session.players.map((player, index) => [player, PLAYER_COLORS[index % PLAYER_COLORS.length]]));
  const ocean = svgElement("polygon", { class: "map-ocean", points: sites.map(([x,y]) => `${x},${y}`).join(" ") }); svg.append(ocean);
  const regionCenters = [[590,220],[660,340],[590,460],[410,460],[340,340],[410,220]];
  view.regions.forEach((region, index) => {
    const [cx, cy] = regionCenters[index], rx = 80, ry = 67;
    const points = Array.from({ length: 6 }, (_, corner) => {
      const angle = (Math.PI / 3) * corner - Math.PI / 2;
      return `${cx + Math.cos(angle) * rx},${cy + Math.sin(angle) * ry}`;
    }).join(" ");
    svg.append(svgElement("polygon", { class: "map-hex", points, fill: RESOURCE_COLORS[index] }));
    const number = svgElement("text", { class: "map-region-num", x: cx, y: cy - 5 }); number.textContent = `SECTOR ${region.number}`; svg.append(number);
    const label = svgElement("text", { class: "map-region-label", x: cx, y: cy + 16 }); label.textContent = region.resource.toUpperCase(); svg.append(label);
  });
  const edgeRecords = [...view.roads].sort((a, b) => Boolean(a.owner) - Boolean(b.owner));
  for (const road of edgeRecords) {
    const [x1, y1] = sites[road.from], [x2, y2] = sites[road.to];
    const selected = state.islandSelection?.kind === "road" && state.islandSelection.index === road.index;
    const color = colors[road.owner] || "";
    const group = svgElement("g", { role: "button", tabindex: "0", "aria-label": `Road ${road.index}, site ${road.from} to site ${road.to}${road.owner ? `, owned by ${road.owner}` : ", available"}` });
    const line = svgElement("line", { class: `map-road${road.owner ? " owned" : ""}${selected ? " selected" : ""}`, x1, y1, x2, y2 });
    if (color) line.setAttribute("stroke", color);
    group.append(line);
    const hit = svgElement("line", { class: "map-road hit", x1, y1, x2, y2 });
    group.append(hit);
    group.addEventListener("click", () => selectIslandTarget("road", road.index));
    group.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectIslandTarget("road", road.index); } });
    svg.append(group);
  }
  for (const site of view.sites) {
    const [cx, cy] = sites[site.index];
    const selected = state.islandSelection?.kind === "site" && state.islandSelection.index === site.index;
    const cls = `map-site ${site.owner ? "owned" : "free"}${site.city ? " city" : ""}${selected ? " selected" : ""}`;
    const group = svgElement("g", { role: "button", tabindex: "0", "aria-label": `Site ${site.index}${site.owner ? `, owned by ${site.owner}${site.city ? ", city" : ", outpost"}` : ", available for an outpost"}` });
    const radius = site.city ? 13 : site.owner ? 10 : 7;
    group.append(svgElement("circle", { class: cls, cx, cy, r: radius, fill: site.owner ? colors[site.owner] || "#94b1ab" : undefined }));
    group.append(svgElement("circle", { class: "map-site-hit", cx, cy, r: 19 }));
    const label = svgElement("text", { x: cx + 11, y: cy - 10, fill: "#b2bfd1", "font-size": "9", "font-family": "Space Grotesk, sans-serif" }); label.textContent = `S${String(site.index).padStart(2,"0")}`; group.append(label);
    group.addEventListener("click", () => selectIslandTarget("site", site.index));
    group.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectIslandTarget("site", site.index); } });
    svg.append(group);
  }
  mapWrap.append(svg);
  const side = node("div", "island-side");
  side.append(node("div", "island-goal", `✦ FIRST TO ${view.goal} VICTORY POINTS WINS`));
  const resources = node("div", "resource-list");
  for (const [index, player] of session.players.entries()) {
    const card = node("div", `resource-player${player === session.current_player ? " active" : ""}`);
    const head = node("div", "resource-head"); head.append(node("span", "", player)); head.append(node("span", "resource-score", `${view.scores[player]} VP`)); card.append(head);
    const counts = node("div", "resource-counts");
    RESOURCE_META.forEach(([name, glyph]) => {
      const item = node("div", "resource"); item.append(node("span", "resource-icon", glyph)); item.append(node("span", "", view.resources[player][name])); counts.append(item);
    });
    card.append(counts); card.dataset.playerIndex = index; resources.append(card);
  }
  side.append(resources);
  const selection = node("div", "island-selection", "Select a numbered site or road on the map to preview a build."); selection.id = "island-selection"; side.append(selection);
  const controls = node("div", "island-controls");
  const phaseButtons = node("div", "phase-buttons");
  const roll = node("button", "button primary", `⚄ Roll ${view.phase === "build" ? `· ${view.roll}` : "for production"}`); roll.type = "button"; roll.disabled = !canAct(session) || view.phase !== "roll"; roll.addEventListener("click", () => submitAction([1]));
  const end = node("button", "button", "End turn →"); end.type = "button"; end.disabled = !canAct(session) || view.phase !== "build"; end.addEventListener("click", () => submitAction([6]));
  phaseButtons.append(roll, end); controls.append(phaseButtons);
  const action = node("select", "field"); action.id = "island-action";
  for (const [value, label] of [["road", "Build road · T + C"], ["outpost", "Build outpost · T+C+W+G"], ["city", "Upgrade city · 2G+3O"], ["trade", "Bank trade · 3:1"]]) { const option = node("option", "", label); option.value = value; action.append(option); }
  const target = node("input", "field"); target.id = "island-target"; target.placeholder = "Select on map, or enter index"; target.autocomplete = "off";
  const build = node("button", "button primary", "Build / trade"); build.type = "button"; build.disabled = !canAct(session) || view.phase !== "build";
  if (state.islandSelection) {
    action.value = state.islandSelection.kind === "road" ? "road" : view.sites[state.islandSelection.index]?.owner === session.current_player ? "city" : "outpost";
    target.value = String(state.islandSelection.index);
    target.placeholder = action.value === "road" ? "Road index 0–26" : "Site index 0–17";
  }
  action.addEventListener("change", () => {
    if (action.value === "trade") { state.islandSelection = null; target.value = ""; target.placeholder = "Give:receive · e.g. 0:4"; }
    else { target.placeholder = action.value === "road" ? "Road index 0–26" : "Site index 0–17"; }
    updateIslandSelection(view, selection, action, target);
  });
  target.addEventListener("input", () => updateIslandSelection(view, selection, action, target));
  build.addEventListener("click", () => {
    const opcode = { road: 2, outpost: 3, city: 4, trade: 5 }[action.value];
    let value;
    try {
      if (opcode === 5) {
        const [give, receive, extra] = target.value.split(":").map(Number);
        if (extra !== undefined || !Number.isInteger(give) || !Number.isInteger(receive) || give < 0 || give > 4 || receive < 0 || receive > 4 || give === receive) throw new Error("Choose two different resources numbered 0–4, e.g. 0:4.");
        value = (give << 4) | receive;
      } else { value = Number(target.value); if (!Number.isInteger(value) || value < 0 || value > 255) throw new Error("Choose a valid site or road index."); }
    } catch (error) { notify(error.message); return; }
    submitAction([opcode, value]);
  });
  controls.append(action, target, build); side.append(controls);
  side.append(node("div", "island-legend", "● Outpost · ◆ City · click a road or site to select. Crossing routes are not junctions."));
  layout.append(mapWrap, side); host.append(layout);
  if (state.islandSelection) updateIslandSelection(view, selection, action, target);
}

function selectIslandTarget(kind, index) {
  state.islandSelection = { kind, index };
  if (state.session) {
    const selection = $("#island-selection");
    const action = $("#island-action"), target = $("#island-target");
    if (action && target) {
      action.value = kind === "road" ? "road" : state.session.view.sites[index].owner === state.session.current_player ? "city" : "outpost";
      target.value = String(index);
      updateIslandSelection(state.session.view, selection, action, target);
    }
    renderGame(state.session);
  }
}

function updateIslandSelection(view, label, action, target) {
  if (action.value === "trade") { label.textContent = "Bank trade · give three of one resource for one of another. Nothing transmits until you submit."; return; }
  const index = Number(target.value);
  if (!Number.isInteger(index) || index < 0) { label.textContent = "Select a numbered site or road on the map to preview a build."; return; }
  if (action.value === "road" && view.roads[index]) {
    const road = view.roads[index]; label.textContent = `ROAD ${String(index).padStart(2,"0")} · ${road.from} ↔ ${road.to} · ${road.owner ? `owned by ${road.owner}` : "available to build"} · Submit to transmit.`;
  } else if (["outpost", "city"].includes(action.value) && view.sites[index]) {
    const site = view.sites[index]; label.textContent = `SITE ${String(index).padStart(2,"0")} · ${site.owner || "unclaimed"} · ${site.owner ? (site.city ? "city" : "outpost") : "available for an outpost"} · Submit to transmit.`;
  } else label.textContent = "That index is outside the selected build range.";
}

function renderPlugin(session, view, host) {
  const summary = node("pre", "plugin-summary", JSON.stringify(view, null, 2)); host.append(summary);
  const form = node("form", "control-row"); const action = node("input", "field"); action.placeholder = "Plugin action · hexadecimal bytes"; action.autocomplete = "off";
  const submit = node("button", "button primary", "Submit action"); submit.type = "submit"; submit.disabled = !canAct(session); form.append(action, submit);
  form.addEventListener("submit", event => { event.preventDefault(); if (!/^(?:[0-9a-fA-F]{2})+$/.test(action.value.trim())) { notify("Enter hexadecimal bytes, for example 0102."); return; } sendCommand("legal_action", { session_id: session.id, action: action.value.trim() }); });
  host.append(form);
}

function renderScorchedEarth(session, view, host) {
  const wrap = node("div", "tank-layout");
  const arena = node("div", "tank-arena");
  arena.setAttribute("tabindex", "0");
  arena.setAttribute("aria-label", "Scrollable battlefield. Swipe, drag the scrollbar, or use horizontal arrow keys to pan.");
  const svg = svgElement("svg", { class: "tank-map", viewBox: `0 0 ${view.width * 24} ${view.sky_height * 24}`,
    role: "img", "aria-label": `${view.map_name} battlefield. Both tanks are visible; adjust the weapon and firing angle.` });
  const width = view.width * 24, height = view.sky_height * 24;
  const defs = svgElement("defs");
  const skyGradient = svgElement("linearGradient", { id: "scorched-sky", x1: "0%", y1: "0%", x2: "0%", y2: "100%" });
  skyGradient.append(svgElement("stop", { offset: "0%", "stop-color": "#081426" }),
    svgElement("stop", { offset: "68%", "stop-color": "#1e3850" }),
    svgElement("stop", { offset: "100%", "stop-color": "#58726c" }));
  const landGradient = svgElement("linearGradient", { id: "scorched-ground", x1: "0%", y1: "0%", x2: "0%", y2: "100%" });
  landGradient.append(svgElement("stop", { offset: "0%", "stop-color": "#739067" }),
    svgElement("stop", { offset: "100%", "stop-color": "#27382f" }));
  defs.append(skyGradient, landGradient); svg.append(defs);
  svg.append(svgElement("rect", { x: 0, y: 0, width, height, class: "tank-sky" }));
  svg.append(svgElement("circle", { cx: width * .75, cy: height * .18, r: 28, class: "tank-moon" }));
  const hills = [`0,${height}`];
  for (let i = 0; i <= view.width; i++) hills.push(`${i * 24},${height * (.59 + ((i * 13 + 7) % 9) / 90)}`);
  hills.push(`${width},${height}`);
  svg.append(svgElement("polygon", { points: hills.join(" "), class: "tank-distant-hills" }));
  const stars = svgElement("g", { class: "tank-stars" });
  for (let i = 0; i < 45; i++) {
    const sx = ((i * 187 + 43) % width), sy = ((i * 79 + 17) % (height * .52));
    stars.append(svgElement("circle", { cx: sx, cy: sy, r: i % 7 === 0 ? 1.4 : .65 }));
  }
  svg.append(stars);
  for (let x = 0; x < view.width; x++) for (let y = 0; y < view.height; y++)
    svg.append(svgElement("rect", { x: x * 24, y: y * 24, width: 24, height: 24, class: "tank-grid-cell" }));
  const terrainPoints = [`0,${height}`];
  view.terrain.forEach((elevation, x) => terrainPoints.push(`${x * 24 + 12},${height - elevation * 24}`));
  terrainPoints.push(`${width},${height}`);
  svg.append(svgElement("polygon", { points: terrainPoints.join(" "), class: "tank-ground" }));
  const barrels = new Map(), tankArtwork = new Map();
  for (const [index, tank] of view.tanks.entries()) {
    const isLocalTank = tank.player === state.snapshot.callsign;
    const seatLabel = isPractice() ? tank.player : isLocalTank ? "YOU" : "OPPONENT";
    const group = svgElement("g", { class: `tank-unit ${isLocalTank ? "friendly" : "enemy"}` });
    const cy = (view.sky_height - tank.world_y) * 24;
    const art = svgElement("g", { class: "tank-art" });
    const barrel = svgElement("line", { class: "tank-barrel" });
    const sprite = svgElement("image", { href: index === 0 ? "/tank-teal.png" : "/tank-amber.png",
      x: tank.x * 24 - 24, y: cy - 22, width: 48, height: 35, preserveAspectRatio: "xMidYMid meet", class: "tank-sprite" });
    const label = svgElement("text", { x: tank.x * 24, y: cy + 23, class: "tank-label" });
    label.textContent = seatLabel;
    art.append(svgElement("ellipse", { cx: tank.x * 24, cy: cy + 11, rx: 21, ry: 4, class: "tank-shadow" }),
      barrel, sprite);
    group.append(art, label);
    if (tank.shielded) group.prepend(svgElement("ellipse", { cx: tank.x * 24, cy, rx: 23, ry: 15, class: "tank-shield" }));
    barrels.set(tank.player, barrel); tankArtwork.set(tank.player, art);
    function setBarrelAngle(degrees) {
      const direction = tank.facing === 0 ? 1 : -1;
      const theta = degrees * Math.PI / 180;
      const x = tank.x * 24 + direction * 3, y = cy - 10;
      barrel.setAttribute("x1", x); barrel.setAttribute("y1", y);
      barrel.setAttribute("x2", x + direction * Math.cos(theta) * 25);
      barrel.setAttribute("y2", y - Math.sin(theta) * 25);
    }
    setBarrelAngle(tank.player === view.last_actor && view.impact ? view.last_action_parameter : 45);
    barrel.setAngle = setBarrelAngle;
    svg.append(group);
    const motionKey = `${session.id}:${session.revision}`;
    if (tank.player === view.last_actor && view.last_action_code === view.actions.drive && state.lastTankMotion !== motionKey) {
      const oldX = tank.x + (view.last_action_parameter === 0 ? 2 : -2);
      const oldY = view.terrain[Math.max(0, Math.min(view.width - 1, oldX))] + 0.65;
      const offsetX = (oldX - tank.x) * 24, offsetY = (tank.world_y - oldY) * 24;
      const motionStarted = performance.now();
      function driveTank(now) {
        const progress = Math.min(1, (now - motionStarted) / 420);
        const eased = 1 - (1 - progress) ** 3;
        group.setAttribute("transform", `translate(${offsetX * (1 - eased)} ${offsetY * (1 - eased)})`);
        if (progress < 1) requestAnimationFrame(driveTank);
        else group.removeAttribute("transform");
      }
      requestAnimationFrame(driveTank);
      state.lastTankMotion = motionKey;
    }
  }
  const trail = svgElement("polyline", { class: "tank-last-trajectory", points: "" });
  if (view.trajectory.length > 1) {
    trail.setAttribute("points", view.trajectory.map(([x, y]) => `${x * 24},${(view.sky_height - y) * 24}`).join(" "));
    svg.append(trail);
  }
  if (view.impact) svg.append(svgElement("circle", { cx: view.impact.x * 24, cy: (view.sky_height - view.impact.y) * 24,
    r: Math.max(8, view.weapons[view.impact.weapon].radius * 24), class: "tank-impact" }));
  const preview = svgElement("polyline", { class: "tank-preview-trajectory", points: "" });
  arena.append(svg);
  const panHint = node("p", "tank-pan-hint", `↔ ${view.width}-column battlefield · Scroll sideways to scout the entire map`);
  const side = node("aside", "tank-side");
  side.append(node("div", "tank-map-name", view.map_name), node("div", "tank-wind", `${view.wind.direction} ${view.wind.label} · shifts each turn`), node("div", "tank-help", isPractice()
    ? "Aim with the wind and reshape the hills. Weapons are unlimited; fuel and repair kits are scarce."
    : "Your browser pilots one tank. The other station pilots the opponent; both browsers resolve each turn and shot."));
  for (const tank of view.tanks) {
    const card = node("div", `tank-health${tank.player === view.turn ? " active" : ""}`);
    const isLocalTank = tank.player === state.snapshot.callsign;
    const label = isPractice() ? tank.player : `${isLocalTank ? "YOU" : "OPPONENT"} · ${tank.player}`;
    const head = node("div", "tank-health-head"); head.append(node("span", "", label), node("strong", "", `${tank.hp} HP`));
    const bar = svgElement("svg", { class: "tank-health-track", viewBox: "0 0 100 5", role: "img", "aria-label": `${tank.hp} health` });
    const fill = svgElement("rect", { x: 0, y: 0, width: tank.hp, height: 5, rx: 2.5, class: "tank-health-fill" });
    card.append(head, bar, node("div", "tank-supplies", `¤${tank.credits} credits · ${tank.fuel} drive · ${tank.repair_kits} repair · ${tank.shielded ? "shield up" : "shield available"}`));
    bar.append(svgElement("rect", { x: 0, y: 0, width: 100, height: 5, rx: 2.5, class: "tank-health-background" }), fill); side.append(card);
  }
  const form = node("form", "tank-controls");
  const weapon = node("select", "field"); weapon.setAttribute("aria-label", "Weapon");
  const activeTank = view.tanks.find(tank => tank.player === (isPractice() ? view.turn : state.snapshot.callsign));
  for (const item of view.weapons) {
    const stock = activeTank?.ammo[item.id] ?? 0;
    const option = node("option", "", `${item.name} · ${item.tag} · ${item.id ? `${stock} rounds` : "∞ rounds"}`);
    option.value = item.id; option.disabled = !stock; weapon.append(option);
  }
  const angleLabel = node("label", "tank-angle-label", "Firing angle · 45°");
  const angle = node("input", "field tank-angle"); angle.type = "range"; angle.min = String(view.min_angle); angle.max = String(view.max_angle); angle.value = "45";
  angleLabel.append(angle);
  const submit = node("button", "button primary", "Fire weapon"); submit.type = "submit"; submit.disabled = !canAct(session);
  form.append(weapon, angleLabel, submit);
  function updatePreview() {
    angleLabel.firstChild.textContent = `Firing angle · ${angle.value}°`;
    const shooterName = isPractice() ? view.turn : state.snapshot.callsign;
    const shooter = view.tanks.find(tank => tank.player === shooterName);
    barrels.get(shooterName)?.setAngle(Number(angle.value));
    if (!shooter || !canAct(session)) { preview.remove(); return; }
    const points = traceTankPreview(view, shooter, view.weapons[Number(weapon.value)], Number(angle.value));
    preview.setAttribute("points", points.map(([x, y]) => `${x * 24},${(view.sky_height - y) * 24}`).join(" "));
    if (!preview.isConnected) svg.append(preview);
  }
  angle.addEventListener("input", updatePreview);
  weapon.addEventListener("change", updatePreview);
  updatePreview();
  form.addEventListener("submit", event => {
    event.preventDefault();
    const degrees = Number(angle.value);
    if (!Number.isInteger(degrees) || degrees < view.min_angle || degrees > view.max_angle) { notify(`Firing angle must be between ${view.min_angle}° and ${view.max_angle}°.`); return; }
    submitAction([Number(weapon.value), degrees]);
  });
  const shop = node("div", "tank-shop");
  shop.append(node("div", "tank-shop-title", "ARMORY · BUY AMMUNITION (USES TURN)"));
  for (const item of view.weapons.slice(1)) {
    const buy = node("button", "button secondary tank-buy", `${item.name} · ¤${item.cost}`);
    buy.type = "button";
    buy.disabled = !canAct(session) || !activeTank || activeTank.credits < item.cost || activeTank.ammo[item.id] >= 254;
    buy.addEventListener("click", () => submitAction([view.actions.buy, item.id]));
    shop.append(buy);
  }
  const tactics = node("div", "tank-tactics");
  const drive = node("div", "tank-drive-controls");
  const left = node("button", "button secondary", "◀ Drive"); left.type = "button"; left.disabled = !canAct(session) || !activeTank?.fuel;
  const right = node("button", "button secondary", "Drive ▶"); right.type = "button"; right.disabled = !canAct(session) || !activeTank?.fuel;
  left.addEventListener("click", () => submitAction([view.actions.drive, 0]));
  right.addEventListener("click", () => submitAction([view.actions.drive, 1]));
  drive.append(left, right); tactics.append(drive);
  const repair = node("button", "button secondary", "Repair +24 HP"); repair.type = "button";
  repair.disabled = !canAct(session) || !activeTank || activeTank.repair_kits <= 0 || activeTank.hp >= 100;
  repair.addEventListener("click", () => submitAction([view.actions.repair, 0]));
  const shield = node("button", "button secondary", "Raise shield"); shield.type = "button";
  shield.disabled = !canAct(session) || !activeTank || activeTank.shielded;
  shield.addEventListener("click", () => submitAction([view.actions.shield, 0]));
  tactics.append(repair, shield); side.append(tactics);
  side.append(form);
  side.append(shop);
  const callout = node("div", "tank-shot-result", view.last_action ? `${view.last_actor} ${view.last_action}` : "Take the first turn");
  side.append(callout);
  wrap.append(arena, panHint, side); host.append(wrap);
  // On a panoramic map, start near the tank whose turn it is; players can pan
  // across the entire battlefield with touch, trackpad or the scrollbar.
  const activeViewTank = view.tanks.find(tank => tank.player === view.turn);
  if (activeViewTank) requestAnimationFrame(() => {
    const x = activeViewTank.x / view.width * svg.getBoundingClientRect().width;
    arena.scrollLeft = Math.max(0, x - arena.clientWidth * .35);
  });
  const actor = view.last_actor;
  const shotKey = `${session.id}:${session.revision}`;
  if (actor && view.impact && view.trajectory.length > 1 && state.lastTankAnimation !== shotKey) {
    state.lastTankAnimation = shotKey;
    const firingArt = tankArtwork.get(actor);
    if (firingArt) { firingArt.classList.add("recoiling"); setTimeout(() => firingArt.classList.remove("recoiling"), 550); }
    view.tanks.forEach((tank, i) => {
      if (view.last_damage?.[i] > 0) {
        const art = tankArtwork.get(tank.player);
        if (art) { art.classList.add("damaged"); setTimeout(() => art.classList.remove("damaged"), 900); }
      }
    });
    animateTankShot(svg, view, actor, callout);
  }
}

function traceTankPreview(view, shooter, weapon, angle) {
  const direction = shooter.facing === 0 ? 1 : -1;
  let x = shooter.x + direction * 0.85, y = shooter.world_y;
  const theta = angle * Math.PI / 180;
  let vx = direction * weapon.speed * Math.cos(theta);
  let vy = weapon.speed * Math.sin(theta);
  const target = view.tanks.find(tank => tank.player !== shooter.player);
  const points = [[x, y]];
  for (let step = 0; step < 240; step++) {
    x += vx; y += vy; vy -= view.gravity; vx += view.wind.acceleration;
    if (direction * vx < 0) vx = 0;
    points.push([x, y]);
    if (target && (x - target.x) ** 2 + (y - target.world_y) ** 2 <= 0.82 ** 2) break;
    if (x < 0 || x >= view.width || y < 0 || y > view.sky_height) break;
    const column = Math.max(0, Math.min(view.width - 1, Math.round(x)));
    if (y <= view.terrain[column]) { points[points.length - 1] = [x, view.terrain[column]]; break; }
  }
  return points;
}

function animateTankShot(svg, view, actor, callout) {
  const shooter = view.tanks.find(tank => tank.player === actor);
  if (!shooter || view.trajectory.length < 2) return;
  const damaged = view.tanks.map((tank, player) => ({ tank, damage: view.last_damage?.[player] ?? 0 }))
    .filter(item => item.damage > 0);
  const hit = damaged.length > 0;
  const totalDamage = damaged.reduce((total, item) => total + item.damage, 0);
  callout.textContent = hit ? `HIT · ${damaged.map(item => `${item.damage} damage to ${item.tank.player}`).join(" · ")}`
    : `MISS · ${view.impact.kind}, no damage`;
  callout.classList.toggle("hit", hit);
  callout.classList.toggle("miss", !hit);
  const points = view.trajectory;
  const first = points[0];
  const flightTrail = svgElement("polyline", { class: "tank-flight-trail", points: "" });
  const projectile = svgElement("circle", { cx: first[0] * 24,
    cy: (view.sky_height - first[1]) * 24, r: 6, class: "tank-projectile" });
  svg.append(flightTrail, projectile);
  const started = performance.now();
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const duration = reducedMotion ? 1500 : Math.min(3200, Math.max(2200, points.length * 42));
  function fly(now) {
    const t = duration ? Math.min(1, (now - started) / duration) : 1;
    const position = t * (points.length - 1);
    const index = Math.min(points.length - 2, Math.floor(position));
    const fraction = position - index;
    const x = points[index][0] + (points[index + 1][0] - points[index][0]) * fraction;
    const y = points[index][1] + (points[index + 1][1] - points[index][1]) * fraction;
    projectile.setAttribute("cx", String(x * 24));
    projectile.setAttribute("cy", String((view.sky_height - y) * 24));
    const trailPoints = points.slice(0, index + 1).map(([px, py]) => `${px * 24},${(view.sky_height - py) * 24}`);
    trailPoints.push(`${x * 24},${(view.sky_height - y) * 24}`);
    flightTrail.setAttribute("points", trailPoints.join(" "));
    if (t < 1) { requestAnimationFrame(fly); return; }
    projectile.remove();
    setTimeout(() => flightTrail.remove(), reducedMotion ? 350 : 800);
    const impact = view.impact;
    const x1 = impact.x * 24, y1 = (view.sky_height - impact.y) * 24;
    const offsets = impact.weapon === 3 ? [-1.2, 0, 1.2] : [0];
    const blasts = offsets.map(offset => svgElement("circle", { cx: x1 + offset * 24, cy: y1 - (offset === 0 || offsets.length === 1 ? 0 : 5),
      r: Math.max(9, view.weapons[impact.weapon].radius * 17), class: `tank-blast${hit ? " hit" : " miss"}` }));
    svg.append(...blasts);
    if (!reducedMotion && impact.kind !== "edge") {
      for (let i = 0; i < 28; i++) {
        const direction = i * Math.PI * 2 / 28;
        const distance = (16 + (i * 19) % 66) * (impact.weapon === 5 ? 1.45 : 1);
        const spark = svgElement("circle", { cx: x1, cy: y1, r: i % 4 === 0 ? 3.1 : 1.8,
          class: "tank-spark", style: `--dx:${Math.cos(direction) * distance}px;--dy:${Math.sin(direction) * distance}px;--delay:${(i % 5) * 22}ms` });
        svg.append(spark);
        setTimeout(() => spark.remove(), 1250);
      }
    }
    const label = svgElement("text", { x: x1, y: y1 - 19, class: `tank-hit-label${hit ? " hit" : " miss"}` });
    label.textContent = hit ? `−${totalDamage} HP` : "MISS";
    svg.append(label);
    setTimeout(() => { label.remove(); }, 1700);
    setTimeout(() => { blasts.forEach(blast => blast.remove()); }, 1100);
  }
  requestAnimationFrame(fly);
}

function renderGame(session) {
  const view = session.view;
  const host = $("#arena-body"); host.replaceChildren();
  host.classList.toggle("tank-arena-body", view?.kind === "scorched-earth");
  state.islandSelection = view?.kind === "settlement" ? state.islandSelection : null;
  if (!view) {
    const empty = node("div", "welcome-state");
    empty.append(node("div", "welcome-emblem", "◈"));
    empty.append(node("p", "eyebrow", session.status === "invited" ? "A SEAT IS WAITING" : "TABLE OPEN"));
    empty.append(node("h2", "", session.status === "invited" ? "You're invited." : "The table is set."));
    empty.append(node("p", "", session.status === "invited" ? `Accept the invitation from ${session.coordinator} in the left panel to take your seat.` : "Invite a station or wait for players to join, then start the game from the table controls."));
    host.append(empty); return;
  }
  const block = node("div", "game-block");
  switch (view.kind) {
    case "grid": renderGrid(session, view, block); break;
    case "scorched-earth": renderScorchedEarth(session, view, block); break;
    case "piles": renderNim(session, view, block); break;
    case "word-tiles": renderWordTiles(session, view, block); break;
    case "settlement": renderIsland(session, view, block); break;
    default: renderPlugin(session, view, block); break;
  }
  host.append(block);
}

function renderChat(messages) {
  const list = $("#chat-list");
  const signature = JSON.stringify(messages);
  if (signature === state.chatSignature) return;
  state.chatSignature = signature;
  list.replaceChildren();
  if (!messages.length) { list.append(node("div", "chat-empty", "No messages yet. Send a friendly note to the table.")); return; }
  for (const message of messages) {
    const mine = message.sender === state.snapshot?.callsign;
    const item = node("div", `chat-message${mine ? " mine" : ""}`);
    item.append(node("div", "chat-author", message.sender));
    item.append(node("div", "chat-bubble", message.body));
    item.append(node("div", "chat-delivery", message.delivery));
    list.append(item);
  }
  list.scrollTop = list.scrollHeight;
}

function renderSession(session, messages) {
  state.session = session;
  const signature = JSON.stringify([session.id, session.status, session.revision, session.sequence,
    session.current_player, session.view, session.has_pending_action, session.invited, session.error, session.last_move]);
  $("#arena-title").textContent = session.game;
  $("#arena-game-tag").textContent = `${session.status.toUpperCase()} · ${session.hosted ? "HOSTED BROADCAST" : "DIRECT TABLE"}`;
  const last = session.last_move ? ` · Last: ${session.last_move.actor} ${session.last_move.description}` : "";
  $("#arena-subtitle").textContent = `TABLE ${session.id.slice(-8)} · ${session.players.length} STATIONS${last}`;
  const revision = $("#arena-revision"); revision.hidden = false; revision.textContent = `REV ${session.revision}`;
  setTurnBanner(session);
  renderRoster(session);
  const actionSignature = JSON.stringify([session.id, session.status, session.players, session.invited,
    session.hosted, session.coordinator, session.has_pending_action, state.snapshot.transport,
    state.snapshot.observer_mode, state.snapshot.callsign, state.snapshot.tx_enabled]);
  if (actionSignature !== state.actionSignature) {
    state.actionSignature = actionSignature;
    renderSessionActions(session);
  }
  const chatEnabled = canTransmit() && ["active", "finished"].includes(session.status);
  $("#chat-input").disabled = !chatEnabled;
  $("#chat-send").disabled = !chatEnabled;
  updateChatCount();
  renderChat(messages);
  const gameSignature = JSON.stringify([signature, state.snapshot.tx_enabled, state.snapshot.observer_mode]);
  if (gameSignature !== state.sessionSignature) {
    state.sessionSignature = gameSignature;
    renderGame(session);
  }
}

async function refresh() {
  if (state.busy) return;
  state.busy = true;
  try {
    const snapshot = await request("/api/state");
    state.snapshot = snapshot;
    setConnection(snapshot);
    populateGames(snapshot.catalog || []);
    const tableMode = $("#table-mode");
    const hostedOption = tableMode.querySelector('option[value="hosted"]');
    hostedOption.disabled = snapshot.transport !== "broadcast" || snapshot.observer_mode;
    if (hostedOption.disabled && tableMode.value === "hosted") tableMode.value = "direct";
    $("#create-game").disabled = !canTransmit();
    $("#game-select").disabled = !canTransmit();
    updateTableSetup();
    renderSessionList(snapshot.sessions || []);
    placeSessionActions();
    if (!snapshot.ready && snapshot.callsign && snapshot.error) $("#transport-label").textContent = `OFFLINE · ${snapshot.error}`;
    if (!snapshot.callsign && !state.setupShown) { state.setupShown = true; openSettings(); }
    if (snapshot.error) $("#transport-label").textContent = `OFFLINE · ${snapshot.error}`;
    if (state.selectedId && snapshot.ready) {
      const detail = await request(`/api/session/${encodeURIComponent(state.selectedId)}`);
      renderSession(detail.session, detail.chat || []);
      $("#mobile-turn-dot").hidden = !canAct(detail.session);
    } else if (!state.selectedId) {
      $("#mobile-turn-dot").hidden = true;
      state.session = null; state.sessionSignature = ""; state.chatSignature = ""; state.islandSelection = null;
      $("#arena-title").textContent = "The airwaves are yours.";
      $("#arena-subtitle").textContent = "Choose a table or start a new match to take your seat.";
      $("#arena-game-tag").textContent = "READY WHEN YOU ARE";
      $("#arena-revision").hidden = true; $("#turn-banner").hidden = true;
      $("#arena-body").replaceChildren(createWelcome());
      $("#roster").className = "roster empty-roster";
      $("#roster").replaceChildren(node("span", "roster-icon", "◌"), node("p", "", "Choose a table to see who's playing."));
      $("#player-count").textContent = "—"; $("#session-actions").replaceChildren(); $("#last-move").hidden = true;
      $("#chat-list").replaceChildren(node("div", "chat-empty", "Choose a table to open its side channel."));
      $("#chat-input").disabled = true; $("#chat-send").disabled = true;
    }
  } catch (error) {
    console.error("Radio Arcade refresh failed:", error);
    $("#connection-status").classList.add("offline");
    $("#connection-status").lastElementChild.textContent = "Web service offline";
    if (!state.snapshot) notify(error.message);
  } finally { state.busy = false; }
}

function createWelcome() {
  const welcome = node("div", "welcome-state");
  welcome.append(node("div", "welcome-orbit orbit-one"), node("div", "welcome-orbit orbit-two"));
  welcome.append(node("div", "welcome-emblem", "◈"));
  welcome.append(node("p", "eyebrow", "ONE ROOM · MANY WORLDS"));
  const heading = node("h2", ""); heading.append(document.createTextNode("Pick a game."), document.createElement("br")); heading.append(node("span", "", "Make a little history."));
  welcome.append(heading);
  welcome.append(node("p", "", "Your table, your station, your next move. All game logic runs through the same engine as the desktop and terminal clients."));
  const browse = node("button", "button primary welcome-cta", "Browse games & create a table →");
  browse.type = "button"; browse.dataset.openTables = ""; welcome.append(browse);
  const chips = node("div", "welcome-chips");
  for (const text of ["✳ Live tables", "⌘ Shared game engine", "◉ Local practice"]) chips.append(node("span", "", text));
  welcome.append(chips); return welcome;
}

async function sendCommand(command, extra = {}) {
  try {
    const result = await request("/api/command", { method: "POST", body: JSON.stringify({ command, ...extra }) });
    if (command === "create_game" && result.session_id) {
      state.selectedId = result.session_id; state.sessionSignature = ""; state.chatSignature = ""; state.islandSelection = null;
      switchPane("arena");
    }
    if (command === "delete_game") {
      state.selectedId = null; state.sessionSignature = ""; state.chatSignature = ""; state.islandSelection = null;
    }
    await refresh();
    return result;
  } catch (error) { notify(error.message); return null; }
}
function submitAction(bytes) {
  if (!state.session) return;
  const action = bytes.map(value => Number(value).toString(16).padStart(2, "0")).join("");
  sendCommand("legal_action", { session_id: state.session.id, action });
}

function openSettings() {
  const form = $("#settings-form");
  if (state.snapshot) {
    for (const key of ["callsign", "host", "transport", "group", "broadcast_mode", "command_port", "kiss_port", "color_scheme", "dx_cluster_url"]) {
      if (form.elements[key]) form.elements[key].value = state.snapshot[key] ?? "";
    }
    form.elements.observer_mode.checked = Boolean(state.snapshot.observer_mode);
    form.elements.sound_enabled.checked = Boolean(state.snapshot.sound_enabled);
  }
  $("#settings-error").hidden = true;
  $("#settings-dialog").showModal();
}

$("#settings-open").addEventListener("click", openSettings);
$("#tx-toggle").addEventListener("click", async () => {
  if (!state.snapshot || isPractice()) return;
  try {
    state.snapshot = await request("/api/tx", { method: "POST", body: JSON.stringify({ enabled: !state.snapshot.tx_enabled }) });
    setConnection(state.snapshot);
    await refresh();
  } catch (error) { notify(error.message); }
});
$("#settings-close").addEventListener("click", () => $("#settings-dialog").close());
$("#settings-cancel").addEventListener("click", () => $("#settings-dialog").close());
$("#settings-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const data = Object.fromEntries(new FormData(form).entries());
  data.command_port = Number(data.command_port); data.kiss_port = Number(data.kiss_port);
  data.observer_mode = form.elements.observer_mode.checked;
  data.sound_enabled = form.elements.sound_enabled.checked;
  try {
    const snapshot = await request("/api/settings", { method: "POST", body: JSON.stringify(data) });
    state.snapshot = snapshot; state.sessionSignature = ""; state.chatSignature = "";
    $("#settings-dialog").close();
    if (snapshot.error) notify(`Settings saved, but the station could not connect: ${snapshot.error}`);
    await refresh();
  } catch (error) { const field = $("#settings-error"); field.textContent = error.message; field.hidden = false; }
});

$("#create-game").addEventListener("click", () => {
  if (!state.snapshot?.ready) { openSettings(); return; }
  sendCommand("create_game", { game: $("#game-select").value, peer: $("#peer-input").value.trim(), hosted: $("#table-mode").value === "hosted" });
});
$("#table-mode").addEventListener("change", updateTableSetup);
$("#game-select").addEventListener("change", updateGamePreview);
$("#arena-body").addEventListener("click", event => {
  if (event.target.closest("[data-open-tables]")) switchPane("lobby");
});
for (const button of document.querySelectorAll(".mobile-tab")) {
  button.addEventListener("click", () => switchPane(button.dataset.pane));
}
window.addEventListener("resize", placeSessionActions);
$("#session-list").addEventListener("click", event => {
  const button = event.target.closest("[data-session]");
  if (!button) return;
  state.selectedId = button.dataset.session; state.sessionSignature = ""; state.chatSignature = ""; state.islandSelection = null;
  switchPane("arena");
  refresh();
});
$("#chat-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (!state.session) return;
  const input = $("#chat-input"), text = input.value.trim();
  if (!text) return;
  if (byteLength(text) > 150) { notify("Messages are limited to 150 UTF-8 bytes."); return; }
  const result = await sendCommand("send_chat", { session_id: state.session.id, text });
  if (result) input.value = "";
  updateChatCount();
});
$("#chat-input").addEventListener("input", updateChatCount);
function updateChatCount() {
  const count = byteLength($("#chat-input").value.trim());
  $("#chat-count").textContent = `${count} / 150 bytes`;
  $("#chat-count").classList.toggle("over-limit", count > 150);
  $("#chat-send").disabled = $("#chat-input").disabled || count === 0 || count > 150;
}

refresh();
setInterval(refresh, 1250);
