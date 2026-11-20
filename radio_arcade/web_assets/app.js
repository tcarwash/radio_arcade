"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const state = { snapshot: null, selectedId: null, selectedInitialized: false, session: null, listSignature: "", sessionSignature: "", actionSignature: "", chatSignature: "", busy: false, toastTimer: null, setupShown: false, creating:false, chatVisible:null, focusMode:false, librarySignature:"", submitting:new Set(), islandSelection: null, lastTankAnimation: null, lastTankMotion: null, tankZoom: 1, tankWeapons: new Map(), tankAim: new Map(), tankCameras: new Map(), wordDrafts: new Map(), moveCueSeen:new Map(), moveCueTimer:null, unreadBySession: {} };
const UNREAD_KEY = "radio-arcade-unread-v1";
try { state.unreadBySession = JSON.parse(localStorage.getItem(UNREAD_KEY) || "{}"); } catch { state.unreadBySession = {}; }
if (!state.unreadBySession || typeof state.unreadBySession !== "object" || Array.isArray(state.unreadBySession)) state.unreadBySession = {};
function persistUiState() {
  try { localStorage.setItem(UNREAD_KEY, JSON.stringify(state.unreadBySession)); }
  catch { /* Keep the current tab usable when browser storage is unavailable. */ }
}
function syncPending(session) {
  return Number(session?.sync_requested_at || 0) > Number(session?.sync_at || 0);
}
function recoveryNeeded(session) {
  return syncPending(session) || /missed moves|state conflict|awaiting snapshot/i.test(session?.error || "");
}
function selectSession(id) {
  state.selectedId = id;
  if (id) state.unreadBySession[id] = 0;
  persistUiState();
  state.sessionSignature = ""; state.actionSignature = ""; state.chatSignature = ""; state.islandSelection = null;
}
const RESOURCE_META = [
  ["timber", "♣"], ["clay", "⬟"], ["wool", "✿"], ["grain", "✳"], ["ore", "◈"],
];
const PLAYER_COLORS = ["#8edbfa", "#ffb777", "#d6a8ff", "#a2e4a5"];
const RESOURCE_COLORS = ["#365f4e", "#77503e", "#5b4b76", "#78633e", "#415e73", "#365f4e"];
const GAME_PREVIEWS = {
  "signal-siege": ["✦", "A 1–4 station co-op deck-builder. Earn a card after each wave."],
  "battleship-classic": ["▦", "Hide a fleet of five ships, then hunt your opponent's fleet."],
  "sea-warfare": ["⚓", "A six-ship fleet with compact salvos, sonar, movement, mines, decoys, and silent sinks."],
  "scorched-earth": ["◈", "Artillery duels · Aim, fire, and reshape the terrain."],
  "open-word-tiles": ["▦", "Build words on a shared board and chase the high score."],
  "hexxagon": ["⬢", "Clone, jump, and capture your opponent’s stones."],
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

const activitySeen = new Set();
let activityInitialized = false;
let activityInbox;
let activityOwner = "", activityLastId = null;
function openActivityTable(id) {
  if (!id) {
    activityInbox.hidden = true;
    $("#cq-panel").open = true;
    switchPane("lobby");
    $("#cq-panel").scrollIntoView({ block: "nearest" });
    return;
  }
  selectSession(id);
  activityInbox.hidden = true;
  switchPane("arena"); refresh();
}
function renderActivity(events) {
  const owner = state.snapshot?.callsign || "station";
  if (owner !== activityOwner) {
    activityOwner = owner;
    try {
      const saved = localStorage.getItem(`radio-arcade-activity-${owner}`);
      activityLastId = saved === null ? Math.max(0, ...events.map(event => event.id || 0)) : Number(saved);
    } catch { activityLastId = Math.max(0, ...events.map(event => event.id || 0)); }
    if (events.length && Math.max(...events.map(event => event.id || 0)) < activityLastId) activityLastId = 0;
  }
  if (!activityInbox) {
    const button = node("button", "button secondary", "Notifications"); button.type = "button"; button.id = "activity-open";
    button.setAttribute("aria-label", "Open notifications");
    document.querySelector("#settings-open").before(button);
    activityInbox = node("section", "activity-inbox"); activityInbox.hidden = true;
    activityInbox.setAttribute("aria-label", "Notifications");
    document.body.append(activityInbox);
    button.addEventListener("click", () => { activityInbox.hidden = !activityInbox.hidden; button.textContent = "Notifications"; });
    activityInbox.button = button;
  }
  activityInbox.replaceChildren();
  const enable = node("button", "button secondary", "Enable desktop notifications"); enable.type = "button";
  enable.addEventListener("click", async () => {
    if (!("Notification" in window)) { notify("Desktop notifications are unavailable in this browser."); return; }
    try {
      const permission = await Notification.requestPermission();
      notify(permission === "granted" ? "Desktop notifications enabled." : "Notifications will still appear in the game room.");
    } catch { notify("Notifications will still appear in the game room."); }
  });
  activityInbox.append(enable);
  if (!events.length) activityInbox.append(node("p", "", "No new invitations or messages."));
  for (const event of [...events].reverse()) {
    const entry = node("button", "activity-entry", `${event.title} · ${event.body}`); entry.type = "button";
    entry.addEventListener("click", () => openActivityTable(event.session_id));
    activityInbox.append(entry);
  }
  const fresh = events.filter(event => !activitySeen.has(event.id));
  for (const event of fresh) {
    activitySeen.add(event.id);
    if (event.id > activityLastId && event.title === "New message" && event.session_id && event.session_id !== state.selectedId)
      state.unreadBySession[event.session_id] = (state.unreadBySession[event.session_id] || 0) + 1;
    if (!activityInitialized) continue;
    activityInbox.button.textContent = "Notifications ●";
    notify(`${event.title} · ${event.body}`);
    if (document.hidden && "Notification" in window && Notification.permission === "granted") {
      try {
        const alert = new Notification(event.title, { body: event.body, tag: `radio-arcade-${event.id}` });
        alert.onclick = () => { window.focus(); openActivityTable(event.session_id); alert.close(); };
      } catch { /* The in-app inbox remains available. */ }
    }
  }
  if (fresh.length) persistUiState();
  if (events.length) {
    activityLastId = Math.max(activityLastId || 0, ...events.map(event => event.id || 0));
    try { localStorage.setItem(`radio-arcade-activity-${owner}`, String(activityLastId)); } catch { /* Activity remains visible in this tab. */ }
  }
  activityInitialized = true;
}

function byteLength(text) { return new TextEncoder().encode(text).length; }
function isPractice() { return state.snapshot?.transport === "practice"; }
function isObserver() { return Boolean(state.snapshot?.observer_mode); }
function canAct(session = state.session) {
  return Boolean(session && !isObserver() && session.status === "active" && !session.has_pending_action && !recoveryNeeded(session) &&
    (isPractice() || state.snapshot?.tx_enabled) &&
    session.current_player === state.snapshot?.callsign);
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
  // Table actions live with the board on all screen sizes.
  const actions=$("#session-actions"),banner=$("#turn-banner");
  if(actions.previousElementSibling!==banner)banner.after(actions);
}

function applyWorkspace(){
  const w=$(".workspace");
  w.classList.toggle("focus-mode",state.focusMode);
  w.classList.toggle("chat-collapsed",!state.chatVisible);
  $("#focus-toggle").setAttribute("aria-pressed",String(state.focusMode));
  $("#chat-toggle").setAttribute("aria-pressed",String(Boolean(state.chatVisible)));
}
$("#chat-toggle").addEventListener("click",()=>{state.chatVisible=!state.chatVisible;applyWorkspace();});
$("#focus-toggle").addEventListener("click",()=>{state.focusMode=!state.focusMode;applyWorkspace();});

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
    : practice ? "Practice mode · Play against the computer. No radio transmission needed."
    : hosted ? "Host as dealer · Stations can discover and request to join your open table."
    : "Direct table · Enter a callsign for an invitation, or invite a station after creating the table.";
}

function setConnection(snapshot) {
  const status = $("#connection-status");
  status.classList.toggle("offline", !snapshot.ready || !snapshot.connected);
  const label = status.lastElementChild;
  if (!snapshot.ready) label.textContent = snapshot.callsign ? "Engine offline" : "Set up station";
  else if (isPractice()) label.textContent = `Local practice · No RF`;
  else if (!snapshot.connected) label.textContent = "Data2G offline";
  else label.textContent = `${snapshot.transport === "broadcast" ? "Broadcast" : "Connected"} · ${snapshot.callsign}${snapshot.identification_method === "Data2G CW" ? " · CWID" : " · app ID"}`;
  $("#brand-mode").textContent=isPractice()?"LOCAL ARCADE · NO RADIO":"DATA2G · GAME ROOM";
  status.title = snapshot.identification_warning || snapshot.identification_method || "";
  $("#transport-label").textContent = !snapshot.ready ? "STATION OFFLINE" : isPractice() ? "LOCAL PRACTICE · NO RF" : `${snapshot.transport.toUpperCase()} · ${snapshot.group}`;
  document.documentElement.dataset.scheme = snapshot.color_scheme || "midnight";
  const tx = $("#tx-toggle");
  tx.textContent = isPractice() ? "NO RF" : snapshot.tx_enabled ? "TX ENABLED" : "TX DISABLED";
  tx.hidden=isPractice();
  $(".arena-foot").firstElementChild.textContent=isPractice()?"LOCAL PRACTICE · NO RADIO TRANSMISSION":"RADIO TRAFFIC IS PUBLIC · ACKS AND RETRIES MAY TRANSMIT";
  $(".chat-lock").textContent=isPractice()?"LOCAL CHAT":"PUBLIC RF · TABLE CHAT";
  if(state.chatVisible===null||state.lastTransport!==snapshot.transport){state.chatVisible=!isPractice();state.lastTransport=snapshot.transport;applyWorkspace();}
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
  catalog = catalog.filter(game => (!game.clients || game.clients.includes("web")) &&
    (state.snapshot?.experimental_games || game.key !== "signal-siege"));
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
  const cq = $("#cq-game"), previousCq = cq.value;
  cq.replaceChildren();
  const any = node("option", "", "Any game"); any.value = ""; cq.append(any);
  for (const game of catalog.filter(game => game.max_players >= 2)) {
    const option = node("option", "", game.name); option.value = game.key; cq.append(option);
  }
  if (catalog.some(game => game.key === previousCq)) cq.value = previousCq;
  updateGamePreview();
}

function renderDiscovery(snapshot){
  $("#cq-send").textContent = snapshot.supports_hands ? "Call CQ · discover players" : "Call CQ to all stations";
  $("#cq-explainer").textContent = snapshot.supports_hands ?
    "Data2G Hands available · One CQ opens a short listening window for parallel station replies. Stations respond only if they enabled automatic answers." :
    "Hands unavailable on this host. CQ still works normally with directed invitations.";
  const keys=JSON.stringify([snapshot.discovery_auto_reply,snapshot.discovery_status,snapshot.discovery_games,
                              snapshot.discovery_legacy_cq,snapshot.catalog?.map(g=>[g.game_id,g.key])]);
  if(state.discoverySettingsKey!==keys){
    state.discoverySettingsKey=keys;
    $("#discovery-status").value=snapshot.discovery_status||"looking";
    $("#discovery-auto-reply").checked=Boolean(snapshot.discovery_auto_reply);
    $("#discovery-legacy-cq").checked=Boolean(snapshot.discovery_legacy_cq);
    const selected=new Set(snapshot.discovery_games||[]);
    const grid=$("#discovery-games");grid.replaceChildren();
    for(const game of (snapshot.catalog||[]).filter(g=>g.max_players>=2 && g.game_id<=16)){
      const label=node("label","discovery-choice");
      const check=node("input","");check.type="checkbox";check.value=game.key;
      check.checked=selected.has(game.key);
      label.append(check,document.createTextNode(" "+game.name));
      grid.append(label);
    }
  }
  const list=$("#discovery-nearby");
  const gameKey=$("#cq-game").value;
  const gameId=snapshot.catalog?.find(g=>g.key===gameKey)?.game_id||null;
  const nearby=snapshot.nearby_players||[];
  $(".discovery-heading").textContent = "Stations answering my call · " + nearby.length;
  // A Hand is an arrival, not a repeating status notification. Remember each
  // station/round even though /api/state is refreshed every 1.25 seconds.
  const owner=snapshot.callsign||"";
  if(state.discoveryArrivalOwner!==owner){
    state.discoveryArrivalOwner=owner;
    state.discoverySeenHands=new Set();
    state.discoveryArrivalsInitialized=false;
    state.discoveryListSignature=null;
  }
  const seen=state.discoverySeenHands||(state.discoverySeenHands=new Set());
  const newcomers=new Set();
  for(const station of nearby){
    const key=station.callsign+":"+station.round;
    if(!seen.has(key)){
      if(state.discoveryArrivalsInitialized)newcomers.add(key);
      seen.add(key);
    }
  }
  state.discoveryArrivalsInitialized=true;
  // Age is excluded: polling must not reanimate a row.
  const stableSignature=JSON.stringify([snapshot.supports_hands,gameId,snapshot.tx_enabled,
    snapshot.observer_mode,nearby.map(station=>[station.callsign,station.round,station.status_code,
      station.games,station.rx_snr])]);
  if(state.discoveryListSignature===stableSignature)return;
  state.discoveryListSignature=stableSignature;
  list.replaceChildren();
  if(!snapshot.supports_hands){
    list.append(node("p","micro-copy","Your Data2G host does not support Hands. Classic CQ remains available."));
    return;
  }
  if(!nearby.length){
    list.append(node("p","micro-copy","No responses yet · Choose a game and call CQ to see who's available."));
    return;
  }
  for(const [index,station] of nearby.entries()){
    const row=node("div","cq-request discovery-result");
    const matches=gameId ? Boolean(station.games & (1 << (gameId-1))) : station.games!==0;
    const compatible=station.available && matches;
    if(newcomers.has(station.callsign+":"+station.round)){
      row.classList.add(compatible?"discovery-bounce-in":"discovery-arrive-in");
      row.style.setProperty("--discovery-stagger",Math.min(index,8)*65+"ms");
      row.addEventListener("animationend",event=>{
        if(event.target!==row || !["discovery-player-glow","discovery-player-arrive"].includes(event.animationName))return;
        row.classList.remove("discovery-bounce-in","discovery-arrive-in");
        row.style.removeProperty("--discovery-stagger");
      });
    }
    row.append(node("span","",station.callsign+" · "+station.status+
      (compatible?" · Interested":matches?" · Game interested":" · Other games")+
      (station.rx_snr==null?"":" · "+station.rx_snr+" dB")));
    const invite=node("button","button",compatible?"Invite":"Unavailable");
    invite.type="button";invite.disabled=!compatible || !canTransmit() || !gameKey;
    invite.dataset.discoveryCall=station.callsign;
    invite.title=compatible?"Create a direct invitation for "+gameKey:"Choose a mutually supported game and an available station";
    row.append(invite);list.append(row);
  }
}

function renderCq(requests) {
  const list = $("#cq-list");
  list.replaceChildren();
  if (!requests.length) { list.append(node("p", "micro-copy", "No recent CQ calls heard.")); return; }
  for (const request of requests) {
    const row = node("div", "cq-request");
    row.append(node("span", "", `${request.callsign} · ${request.game}`));
    const answer = node("button", "button", "Answer");
    answer.type = "button"; answer.dataset.cqCall = request.callsign;
    answer.dataset.cqGame = String(request.game_id);
    const requestedGame = state.snapshot?.catalog.find(game => game.game_id === request.game_id);
    answer.disabled = !canTransmit() || (requestedGame?.key === "signal-siege" && !state.snapshot?.experimental_games);
    row.append(answer); list.append(row);
  }
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
  if (!state.selectedInitialized && state.snapshot?.callsign) {
    state.selectedInitialized = true;
    try { state.selectedId = localStorage.getItem(`radio-arcade-selected-${state.snapshot.callsign}`) || null; } catch { /* Keep the in-memory selection. */ }
  }
  $("#session-count").textContent = sessions.length;
  $("#mobile-table-count").textContent = sessions.length;
  if (!sessions.some(session => session.id === state.selectedId)) state.selectedId = sessions[0]?.id || null;
  try { if (state.snapshot?.callsign && state.selectedId) localStorage.setItem(`radio-arcade-selected-${state.snapshot.callsign}`, state.selectedId); } catch { /* Keep the in-memory selection. */ }
  if (state.selectedId !== previous) { state.sessionSignature = ""; state.actionSignature = ""; state.chatSignature = ""; }
  if (state.selectedId && state.unreadBySession[state.selectedId]) {
    state.unreadBySession[state.selectedId] = 0;
    persistUiState();
  }
  const query = $("#table-search").value.trim().toLocaleLowerCase();
  const filter = $("#table-filter").value;
  const signature = JSON.stringify([state.selectedId, state.snapshot?.callsign, state.snapshot?.observer_mode, query, filter,
    sessions.map(session => [session.id, session.game, session.status, session.current_player, session.players, session.coordinator, session.hosted,
      session.revision, session.sequence, session.updated, session.sync_at, session.sync_requested_at, session.error, session.has_pending_action,
      syncPending(session) ? Math.floor(Date.now() / 30_000) : 0, state.unreadBySession[session.id] || 0])]);
  if (signature === state.listSignature) return;
  state.listSignature = signature;
  const scroll = list.scrollTop;
  list.replaceChildren();
  if (!sessions.length) {
    const empty = node("div", "empty-list", isPractice() ?
      "No games yet. Choose a card in the library to play against the computer." :
      "No open tables yet. Start a game or wait for an invitation over the air.");
    list.append(empty);
    return;
  }
  const priority = session => session.status === "invited" ? 0 :
    session.status === "active" && (session.current_player === state.snapshot.callsign || session.error || syncPending(session)) ? 1 :
    session.status === "active" && session.has_pending_action ? 2 : session.status === "active" ? 3 :
    ["lobby", "joining"].includes(session.status) ? 4 : 5;
  const ordered = sessions.map((session, index) => ({ session, index }))
    .sort((a, b) => priority(a.session) - priority(b.session) || a.index - b.index);
  let shown = 0;
  for (const { session } of ordered) {
    const ownTurn = session.current_player === state.snapshot.callsign;
    const needsSync = recoveryNeeded(session);
    const unread = state.unreadBySession[session.id] || 0;
    const other = isObserver() ? (session.players.join(" / ") || session.coordinator) :
      (session.players.find(player => player !== state.snapshot.callsign) || (session.hosted ? "Open table" : "Waiting for stations"));
    const searchable = [session.game, other, session.id, session.status, session.last_move?.actor, session.last_move?.description].join(" ").toLocaleLowerCase();
    if (query && !searchable.includes(query)) continue;
    if (filter === "your-turn" && !(session.status === "active" && ownTurn)) continue;
    if (filter === "waiting" && !(session.status === "active" && !ownTurn && !needsSync && !session.has_pending_action)) continue;
    if (filter === "resync" && !needsSync) continue;
    if (filter === "unread" && !unread) continue;
    if (["finished","invited"].includes(filter)&&session.status!==filter)continue;
    const card = node("button", `session-card${session.id === state.selectedId ? " selected" : ""}`);
    card.type = "button";
    card.dataset.session = session.id;
    const top = node("div", "session-card-top");
    top.append(node("span", "session-name", session.game));
    const label = syncPending(session) ? "Resync pending" : needsSync ? "Needs resync" :
      session.has_pending_action ? "Move awaiting commit" : ownTurn && session.status==="active" ? "Your move" : session.status === "active" ? "Waiting" : session.status;
    const status = node("span", "session-state", label);
    if (needsSync) status.classList.add("sync-needed");
    else if (session.has_pending_action) status.classList.add("waiting");
    if (!["active", "finished"].includes(session.status)) status.classList.add("waiting");
    top.append(status);
    const summary = node("div", "session-summary");
    summary.append(node("span", "", other), node("span", "bullet", "·"),
      node("span", "", isPractice() ? "vs computer" : `${session.players.length} station${session.players.length === 1 ? "" : "s"}`),
      node("span", "bullet", "·"), node("span", "", session.id.slice(-6)));
    const activity = session.last_move ? `${session.last_move.actor}: ${session.last_move.description}` : session.status;
    summary.append(node("span", "session-activity", activity));
    if (unread) summary.append(node("span", "session-unread", `${unread} unread`));
    card.append(top, summary);
    list.append(card);
    shown++;
  }
  if (!shown) list.append(node("div", "empty-list", "No tables match. Try a different search or filter."));
  list.scrollTop = scroll;
}

function setTurnBanner(session) {
  const banner=$("#turn-banner");
  const feedback=session.feedback||{text:session.status,tone:"neutral"};
  const submitting=state.submitting.has(session.id);
  const text=submitting?(isPractice()?"Making your move…":"Sending move to Data2G…"):feedback.text;
  const tone=submitting?"waiting":feedback.tone==="alert"||feedback.tone==="finished"?"warning":feedback.tone==="mine"?"":feedback.tone;
  banner.textContent=text;banner.title=text;banner.className=("turn-banner "+tone).trim();banner.hidden=false;
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
  const menu=node("details","table-action-menu"),summary=node("summary","button secondary","Table ⋯");
  menu.append(summary);const more=node("div","table-action-content");menu.append(more);
  const command = (name, extra = {}) => sendCommand(name, { session_id: session.id, ...extra });
  const owner = session.coordinator === state.snapshot.callsign;
  const definition=state.snapshot.catalog.find(g=>g.game_id===session.game_id);
  if(definition)addButton(more,"How to play",()=>showGuide(definition));
  addButton(more,"Move history",()=>showMoveHistory(session));
  addButton(more,"Visual replay",()=>showReplay(session));
  if(session.last_opponent_cue && session.view?.kind!=="scorched-earth")
    addButton(more,"Replay last move",()=>animateLastOpponentMove(session));
  if (session.status === "active" && !isObserver() && (isPractice() || session.players.includes(state.snapshot.callsign))) {
    const forfeit = addButton(more, "Forfeit game…", () => {
      if (window.confirm("Forfeit this game? This ends the table and awards the result to the other player(s).")) command("forfeit_game");
    }, false, !canTransmit() || session.has_pending_action);
    forfeit.classList.add("forfeit-game-action");
  }
  if (session.status === "finished") {
    addButton(actions, "View match result", () => showMatchResult(session));
    const game=state.snapshot.catalog.find(g=>g.game_id===session.game_id);
    if(game&&!isObserver()&&canTransmit()&&(state.snapshot.experimental_games||game.key!=="signal-siege"))
      addButton(actions,"Play again",()=>chooseGame(game),true);
  }
  if (!isObserver() && session.status === "invited") addButton(actions, "Accept invitation", () => command("accept_invite"), true, !canTransmit());
  if (!isObserver() && session.hosted && ["lobby", "joining"].includes(session.status) && !session.players.includes(state.snapshot.callsign))
    addButton(actions, session.status === "joining" ? "Retry join request" : "Join hosted table", () => command("join_game"), true, !canTransmit());
  if (!isObserver() && session.hosted && session.status === "lobby" && owner) {
    const order = addActionInput(more, `Turn order · ${state.snapshot.callsign},GUEST1,…`, "reply-order");
    const row = node("div", "action-row"); more.append(row);
    addButton(row, "Set reply order", () => command("set_reply_order", { order: order.value }), false, !canTransmit());
    addButton(row, "Re-announce", () => command("announce_game"), false, !canTransmit());
  }
  if (!isObserver() && !session.hosted && session.status === "lobby" && owner) {
    const peer = addActionInput(actions, "Station callsign to invite", "invite-peer");
    addButton(actions, "Invite station", () => command("invite_player", { peer: peer.value }), false, !canTransmit());
    if (session.invited?.length) {
      const cancelPeer = addActionInput(more, "Pending station callsign", "cancel-peer");
      addButton(more, "Cancel invitation", () => command("cancel_invitation", { peer: cancelPeer.value }), false, !canTransmit());
    }
  }
  if (!isObserver() && session.status === "lobby" && owner){
    const min=state.snapshot.catalog.find(g=>g.game_id===session.game_id)?.min_players??2;
    addButton(actions,"Start game",()=>command("start_game"),true,!canTransmit()||session.invited?.length>0||session.players.length<min);
  }
  if (!isPractice() && ["active","finished"].includes(session.status) && (isObserver() || session.players.includes(state.snapshot.callsign))) {
    const waitingForSync = syncPending(session);
    const syncTimedOut = waitingForSync && Date.now() / 1000 - Number(session.sync_requested_at) > 30;
    const label = waitingForSync ? syncTimedOut ? "Resend resync request" : "Resync requested · waiting for table" :
      isObserver() ? "Resync table · transmits" : owner ? "Send latest state to table" : "Resync table";
    addButton(recoveryNeeded(session)?actions:more, label, () => {
      if (isObserver() && !window.confirm("This sends one synchronization request over the radio. Continue?")) return;
      requestSessionSync(session);
    }, false, (waitingForSync && !syncTimedOut) || (!state.snapshot?.tx_enabled && !isPractice()));
  }
  if (!isObserver() && session.hosted && session.has_pending_action)
    addButton(actions, "Retry pending move", () => command("resend_pending_action"), false, !canTransmit());
  if (!isObserver() && state.snapshot.transport === "connected") {
    const peer = addActionInput(more, "Station callsign", "peer-connect");
    const row = node("div", "action-row"); more.append(row);
    addButton(row, "Connect / resume", () => sendCommand("connect_peer", { peer: peer.value }), false, !canTransmit());
    addButton(row, "Disconnect", () => sendCommand("disconnect_peer"));
  }
  if(["finished","cancelled"].includes(session.status))
    addButton(more,"Archive completed table",async()=>{
      const result=await sendCommand("archive_game",{session_id:session.id});
      if(result){selectSession(null);await refresh();}
    });
  addButton(more, "Delete local copy…", () => {
    if (!window.confirm("Delete this game's saved state, chat, and queued messages from this device? This does not notify other stations.")) return;
    command("delete_game");
  });
  actions.append(menu);
}

function requestSessionSync(session) {
  state.actionSignature = "";
  return sendCommand("request_sync", { session_id: session.id });
}

function renderScoreStrip(session, view) {
  const data = session.scoreboard;
  const strip = node("div", "score-strip");
  strip.append(node("div", "score-heading", `SCOREBOARD / ${data?.label || session.status.toUpperCase()}`));
  strip.setAttribute("aria-label", `Scoreboard · ${data?.label || session.status}`);
  for (const entry of data?.entries || []) {
    const card = node("div", `score-card${entry.active ? " active" : ""}${entry.winner ? " winner" : ""}`);
    card.append(node("div", "score-name", entry.player));
    card.append(node("div", "score-value", entry.value));
    card.append(node("div", "score-unit", entry.winner ? "WINNER" : entry.active ? "● TURN" : entry.unit));
    strip.append(card);
  }
  return strip;
}

const completionStatuses = new Map();
const completionAnnounced = new Set();
const completionDelays = new Map();
function showMatchResult(session) {
  clearTimeout(completionDelays.get(session.id)); completionDelays.delete(session.id);
  document.querySelector("#match-result")?.remove();
  const winners = session.view?.winners || (session.view?.winner ? [session.view.winner] : []);
  const title = session.view?.kind === "signal-siege" ? (session.view.result === "victory" ? "VICTORY" : "DEFEAT") : !winners.length ? "DRAW" : isPractice() || isObserver() ? `${winners.join(" / ")} ${winners.length === 1 ? "WINS" : "WIN"}` : winners.includes(state.snapshot.callsign) ? "VICTORY" : "DEFEAT";
  const dialog = node("dialog", `match-result${title === "DEFEAT" ? " result-loss" : title === "DRAW" ? " result-draw" : ""}`); dialog.id = "match-result";
  const heading = node("h2", "result-title", title); heading.id = "match-result-title";
  dialog.setAttribute("aria-labelledby", heading.id);
  dialog.append(node("p", "eyebrow", "MATCH COMPLETE"), heading, node("p", "", session.game), renderScoreStrip(session, session.view));
  const close = node("button", "button primary full", "Back to board"); close.type = "button";
  close.addEventListener("click", () => dialog.close()); dialog.append(close);
  const game=state.snapshot.catalog.find(g=>g.game_id===session.game_id);
  if(game&&canTransmit()&&(state.snapshot.experimental_games||game.key!=="signal-siege")){
    const again=node("button","button secondary full","Play again");again.type="button";
    again.addEventListener("click",()=>{dialog.close();chooseGame(game);});dialog.append(again);
  }
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog); dialog.showModal();
}
function observeCompletion(session) {
  const previous = completionStatuses.get(session.id);
  completionStatuses.set(session.id, session.status);
  if (previous === "active" && session.status === "finished" && !completionAnnounced.has(session.id)) {
    completionAnnounced.add(session.id);
    if (((session.view?.kind === "scorched-earth" && session.view.impact) || session.view?.kind === "hexxagon") && !session.forfeited_by) {
      completionDelays.set(session.id, setTimeout(() => {
        completionDelays.delete(session.id);
        if (state.session?.id === session.id && state.session.status === "finished") showMatchResult(session);
      }, session.view.kind === "hexxagon" ? 1100 : 4800));
    } else showMatchResult(session);
  }
}

const hexAnimations = new Map();
function animateHexxagonTransition(session, view, svg, elements) {
  const prior = hexAnimations.get(session.id);
  hexAnimations.set(session.id, { revision: session.revision, cells: view.cells });
  if (!prior || prior.revision + 1 !== session.revision || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const changed = view.cells.filter((cell, i) => cell.owner !== prior.cells[i].owner);
  const target = changed.find(cell => cell.owner && !prior.cells[cell.id].owner);
  if (!target) return;
  const xy = cell => [52 * (cell.q + cell.r / 2), 45 * cell.r];
  const [x, y] = xy(target);
  elements.get(target.id).classList.add("hex-arrival");
  const effects = svgElement("g", { class: "hex-effects", "aria-hidden": "true" });
  const origin = changed.find(cell => !cell.owner && prior.cells[cell.id].owner);
  if (origin) {
    const [sx, sy] = xy(origin);
    const stone = svgElement("circle", { r: 14, class: "hex-moving-stone" });
    stone.append(svgElement("animateMotion", { dur: ".35s", path: `M ${sx},${sy} Q ${(sx+x)/2},${(sy+y)/2-32} ${x},${y}`, fill: "freeze" }));
    effects.append(stone);
  }
  effects.append(svgElement("circle", { cx: x, cy: y, r: 24, class: "hex-pulse" }));
  for (const cell of changed.filter(cell => cell.owner && prior.cells[cell.id].owner)) {
    const [ex, ey] = xy(cell);
    const points = Array.from({length: 9}, (_, i) => {
      const f = i / 8, jitter = i && i < 8 ? (i % 2 ? 1 : -1) * (5 + cell.id % 4) : 0;
      return `${x+(ex-x)*f+jitter},${y+(ey-y)*f-jitter}`;
    }).join(" ");
    effects.append(svgElement("polyline", { points, class: "hex-lightning-glow" }), svgElement("polyline", { points, class: "hex-lightning" }));
    elements.get(cell.id).classList.add("hex-converted");
  }
  svg.append(effects);
  setTimeout(() => effects.remove(), 1100);
}

function renderHexxagon(session, view, host) {
  const hint = node("p", "turn-meta", "Select your stone, then a highlighted hex. Gold = clone · dashed = jump.");
  const svg = svgElement("svg", { viewBox: "-270 -245 540 490", class: "hexxagon-board", "aria-label": "Hexxagon board" });
  const legal = new Map(view.legal_moves.map(([a, b]) => [`${a}:${b}`, true]));
  const elements = new Map();
  let selected = null;
  function choose(id) {
    if (!canAct(session)) return;
    if (legal.has(`${selected}:${id}`)) {
      const a = view.cells[selected], b = view.cells[id];
      const distance = Math.max(Math.abs(a.q-b.q), Math.abs(a.r-b.r), Math.abs(a.q+a.r-b.q-b.r));
      const code = 0x8000 | selected << 6 | id;
      submitAction(distance === 1 ? [id] : [code >> 8, code & 255]); return;
    }
    selected = view.cells[id].owner === view.active_seat && selected !== id ? id : null;
    for (const cell of view.cells) {
      const el = elements.get(cell.id);
      el.classList.toggle("selected", cell.id === selected);
      el.classList.toggle("legal", legal.has(`${selected}:${cell.id}`));
      const a = selected === null ? null : view.cells[selected];
      el.classList.toggle("jump", !!a && Math.max(Math.abs(a.q-cell.q), Math.abs(a.r-cell.r), Math.abs(a.q+a.r-cell.q-cell.r)) === 2);
    }
  }
  for (const cell of view.cells) {
    const x = 52 * (cell.q + cell.r / 2), y = 45 * cell.r;
    const group = svgElement("g", { class: `hex-cell owner-${cell.owner}`, transform: `translate(${x},${y})`,
      role: "button", tabindex: canAct(session) ? 0 : -1, "aria-disabled": String(!canAct(session)),
      "aria-label": `Hex ${cell.id + 1}, ${cell.owner ? session.players[cell.owner - 1] : "empty"}` });
    group.append(svgElement("polygon", { points: "0,-27 23,-13 23,13 0,27 -23,13 -23,-13" }));
    if (cell.owner) group.append(svgElement("circle", { cx: 0, cy: 0, r: 15 }));
    const label = svgElement("text", { x: 0, y: 4, "text-anchor": "middle" }); label.textContent = cell.id + 1;
    group.append(label); group.addEventListener("click", () => choose(cell.id));
    group.addEventListener("keydown", event => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); choose(cell.id); } });
    group.dataset.moveCell=cell.id;
    elements.set(cell.id, group); svg.append(group);
  }
  host.append(svg, hint);
  if (typeof hexAnimations !== "undefined") animateHexxagonTransition(session, view, svg, elements);
}

function renderGrid(session, view, host) {
  const board = node("div", "board-tictactoe");
  const active = canAct(session);
  for (const [index, value] of view.cells.entries()) {
    const cell = node("button", `ttt-cell${value === "O" ? " mark-o" : ""}`, value.trim());
    cell.type = "button";cell.dataset.moveCell=index;
    cell.setAttribute("aria-label", `Square ${index + 1}${value.trim() ? `, ${value}` : ", empty"}`);
    cell.disabled = !active || Boolean(value.trim());
    cell.addEventListener("click", () => submitAction([index]));
    board.append(cell);
  }
  host.append(board, node("p", "turn-meta", active ? "Choose a square to play. Your move is committed only when selected." : "Follow the active station's turn. The board updates as commits arrive."));
}

function renderConnectFour(session, view, host) {
  const active=canAct(session);
  const board=node("div","connect-four-board");
  const controls=node("div","connect-four-controls");
  for(let col=0;col<7;col++){
    const choose=node("button","button secondary",String(col+1));
    choose.type="button";choose.disabled=!active||view.cells[col]!==" ";
    choose.setAttribute("aria-label","Drop into column "+(col+1));
    choose.addEventListener("click",()=>submitAction([col]));
    controls.append(choose);
  }
  for(let i=0;i<view.cells.length;i++){
    const value=view.cells[i];
    const disc=node("div","four-cell"+(value==="X"?" four-x":value==="O"?" four-o":""));
    disc.dataset.moveCell=i;disc.setAttribute("aria-label","Row "+(Math.floor(i/7)+1)+", column "+(i%7+1)+(value.trim()?", "+value:""));
    board.append(disc);
  }
  host.append(node("p","turn-meta","Drop a disc into a column · first four connected horizontally, vertically or diagonally wins."),controls,board);
}

function renderBattleship(session, view, host) {
  const wrapper = node("div", "battleship-view"), draftKey = `ra-bs-draft-${session.id}`;
  const sea = view.kind === "sea-warfare", fleetSizes = sea ? [5,4,3,3,3,2] : [5,4,3,3,2];
  if (!sea) wrapper.classList.add("bs-classic");
  const shipNames = sea ? ["Carrier","Battleship","Cruiser","Destroyer","Submarine","Patrol boat"] : ["Carrier","Battleship","Cruiser","Destroyer","Patrol boat"];
  const shipSprites = ["battleship-carrier.png", "battleship-hull.png", "battleship-cruiser.png", "battleship-destroyer.png", "battleship-submarine.png", "battleship-patrol.png"];
  if (sea) {
    const rules=node("details","bs-rules"), summary=node("summary","","Sea Warfare abilities · compact radio actions");
    rules.append(summary,node("p","","Carrier: one 50% fighter intercept roll. Battleship: one adjacent double shot. Cruiser: one free move; other moves cost one of three supplies. Destroyer: silently disarms one mine. Submarine: one row sonar scan. Patrol boat: carries one optional hidden mine. One-cell inflatable decoy is optional."));
    wrapper.append(rules);
  }
  const drawFleet = (grid, fleets) => {
    fleets.forEach((cells, index) => {
      if(!cells || !cells.length)return;
      const first = Math.min(...cells), vertical = cells.length > 1 && cells[1] - cells[0] === 10;
      const row = Math.floor(first / 10), col = first % 10;
      const ship = node("span", "bs-fleet-ship");
      ship.style.gridRow = `${row + 1} / span ${vertical ? cells.length : 1}`;
      ship.style.gridColumn = `${col + 1} / span ${vertical ? 1 : cells.length}`;
      const sprite = shipSprites[!sea && index === 4 ? 5 : index].replace(".png", vertical ? ".png" : "-horizontal.png");
      ship.style.backgroundImage = `url("/${sprite}")`;
      ship.setAttribute("aria-label", shipNames[index]);
      grid.append(ship);
    });
  };
  const ownCells = new Set((session.private_fleet || []).flat());
  const ownGrid = (parent=wrapper) => {
    parent.append(node("h3", "", "Your fleet · stored on this station"));
    if (sea) {
      const carrierLost = (session.private_fleet?.[0] || []).length > 0 && session.private_fleet[0].every(cell => (session.private_hits || []).includes(cell));
      const fighter=node("div","bs-fighter-status"); const plane=node("img","","");plane.src="/battleship-fighter.png";plane.alt="Carrier fighter plane";
      fighter.append(plane,node("span","",carrierLost?"Carrier lost · fighter unavailable":session.fighter_used?"Fighter interception expended":"Fighter interception ready · first incoming hit gets a 50% block roll"));parent.append(fighter);
    }
    const grid=node("div","battleship-grid");
    grid.dataset.moveBoard="incoming";
    for(let i=0;i<100;i++){const b=node("span",`battleship-cell ${ownCells.has(i)?"bs-ship":""}`);b.dataset.moveCell=i;grid.append(b);}
    drawFleet(grid, session.private_fleet || []);
    if (!sea) {
      for (const [cell, value] of (view.incoming_shots || []).entries()) {
        if (!value) continue;
        const sunk=(session.private_fleet || []).some(ship=>ship.includes(cell)&&ship.every(c=>view.incoming_shots[c]>=2));
        const token=node("span","bs-token "+(value === 1 ? "bs-own-miss" : sunk ? "bs-own-sunk" : "bs-own-hit"),value===1?"○":sunk?"S":"×");token.style.gridArea=`${Math.floor(cell/10)+1} / ${cell%10+1}`;grid.append(token);
      }
    }
    if (sea) {
      const misses = (view.incoming_shots || []).flatMap((value, cell) => value === 1 ? [cell] : []);
      for (const cell of misses) { const token=node("span","bs-token bs-own-miss","○"); token.style.gridArea=`${Math.floor(cell/10)+1} / ${cell%10+1}`; grid.append(token); }
      for (const cell of session.private_deploy?.mines || []) { const token=node("span","bs-token bs-mine","✹"); token.style.gridArea=`${Math.floor(cell/10)+1} / ${cell%10+1}`; grid.append(token); }
      for (const cell of session.private_deploy?.decoys || []) { const token=node("span","bs-token bs-decoy","◉"); token.style.gridArea=`${Math.floor(cell/10)+1} / ${cell%10+1}`; grid.append(token); }
      for (const cell of session.private_hits || []) { const token=node("span","bs-token bs-own-hit","×"); token.style.gridArea=`${Math.floor(cell/10)+1} / ${cell%10+1}`; grid.append(token); }
    }
    parent.append(grid);
    if (!sea && session.private_fleet) {
      const incoming=view.incoming_shots || [];
      const sunk=ship=>ship.every(c=>incoming[c]>=2);
      const remaining=session.private_fleet.filter(ship=>!sunk(ship)).length;
      parent.append(node("p","turn-meta",remaining+" / 5 ships afloat · "+session.private_fleet.map((ship,i)=>shipNames[i]+": "+(sunk(ship)?"sunk":"afloat")).join(" · ")));
    }
  };
  const pendingText = view.pending_kind === 2 ? `Submarine sonar is checking row ${view.pending+1}. The result returns in a compact reply.` : view.pending_kind === 1 ? `Battleship salvo called across row ${Math.floor(view.pending/10)+1}.` : `Called row ${Math.floor(view.pending/10)+1}, column ${view.pending%10+1}.`;
  wrapper.append(node("p", "turn-meta", view.phase === "setup" ? "Fleet positions stay on this station. Only compact actions and results are transmitted." : session.status === "finished" ? `${view.winner || "No station"} wins.` : view.phase === "reply" ? `${pendingText} Awaiting ${session.current_player}’s report.` : `${session.current_player}: choose a tactical action.`));
  if (!sea && view.phase === "setup" && !session.private_fleet && !isObserver() && session.players.includes(state.snapshot?.callsign)) {
    let saved;
    try { saved = JSON.parse(localStorage.getItem(draftKey) || "{}"); } catch { saved = {}; }
    let ships = Array.isArray(saved) ? saved : (Array.isArray(saved.ships) ? saved.ships : []);
    let vertical = Boolean(saved.vertical);
    let history = Array.isArray(saved.history) ? saved.history : [];
    const allowed = canTransmit() && session.status === "active" && !session.has_pending_action && !recoveryNeeded(session);
    let saving = false, hovered = null;
    const prompt = node("p", "bs-placement-prompt");
    const controls = node("div", "bs-tactical-controls");
    const rotate = node("button", "button secondary"), undo = node("button", "button secondary", "Undo");
    const randomize = node("button", "button secondary", "Randomize"), clear = node("button", "button secondary", "Clear");
    const deploy = node("button", "button primary", "Deploy fleet");
    for (const b of [rotate, undo, randomize, clear, deploy]) b.type = "button";
    controls.append(rotate, undo, randomize, clear);
    const grid = node("div", "battleship-grid bs-placement-grid");
    const roster = node("p", "turn-meta bs-placement-roster");
    const hint = node("p", "turn-meta", "Green preview: fits · Red: blocked. Your fleet stays private.");
    wrapper.append(prompt, controls, grid, roster, hint, deploy);
    const cells = [];
    const persist = () => { try {localStorage.setItem(draftKey, JSON.stringify({ships, vertical, history}));} catch {} };
    const remember = () => { history.push(ships.map(ship => ship.slice())); if (history.length > 30) history.shift(); };
    const footprint = (i, length, vert, occupied) => {
      const row=Math.floor(i/10), col=i%10;
      const points=Array.from({length}, (_, j) => [row+(vert?j:0), col+(vert?0:j)]).filter(([r,c])=>r<10 && c<10).map(([r,c])=>r*10+c);
      return {points, valid:points.length===length && !points.some(c=>occupied.has(c))};
    };
    function paintPreview(cell) {
      hovered=cell;
      for (const b of cells) b.classList.remove("bs-preview-valid","bs-preview-invalid");
      if (cell===null || !allowed || saving || ships.length>=fleetSizes.length) return;
      const candidate=footprint(cell,fleetSizes[ships.length],vertical,new Set(ships.flat()));
      for (const i of candidate.points) cells[i].classList.add(candidate.valid?"bs-preview-valid":"bs-preview-invalid");
    }
    function redraw() {
      grid.querySelectorAll(".bs-fleet-ship").forEach(el=>el.remove());
      const occupied=new Set(ships.flat()), complete=ships.length===fleetSizes.length;
      cells.forEach((b,i)=>{b.classList.toggle("bs-ship",occupied.has(i));b.disabled=!allowed||saving||complete;});
      drawFleet(grid,ships);
      prompt.textContent=complete?"Fleet ready — deploy when you're happy with it": "Place "+shipNames[ships.length]+" · "+fleetSizes[ships.length]+" squares ("+(ships.length+1)+"/5)";
      rotate.textContent=(vertical?"Vertical":"Horizontal")+" · R"; rotate.disabled=!allowed||complete||saving;
      undo.disabled=!allowed||saving||!(history.length||ships.length);
      randomize.disabled=!allowed||saving; clear.disabled=!allowed||saving||!ships.length;
      deploy.disabled=!allowed||saving||!complete;
      roster.textContent=shipNames.map((name,i)=>(i<ships.length?"✓ ":"○ ")+name+" ("+fleetSizes[i]+")").join(" · ");
      paintPreview(hovered);
      persist();
    }
    for(let i=0;i<100;i++){
      const b=node("button","battleship-cell");b.type="button";
      b.title=String.fromCharCode(65+i%10)+(1+Math.floor(i/10));b.setAttribute("aria-label",b.title);
      b.addEventListener("pointerenter",()=>paintPreview(i));b.addEventListener("focus",()=>paintPreview(i));
      b.addEventListener("pointerleave",()=>paintPreview(null));b.addEventListener("blur",()=>paintPreview(null));
      b.addEventListener("click",()=>{
        if (!allowed || saving || ships.length>=fleetSizes.length) return;
        const candidate=footprint(i,fleetSizes[ships.length],vertical,new Set(ships.flat()));
        if (!candidate.valid) return;
        remember();ships.push(candidate.points);redraw();
      });cells.push(b);grid.append(b);
    }
    rotate.addEventListener("click",()=>{vertical=!vertical;redraw();});
    wrapper.addEventListener("keydown",event=>{if(event.key.toLowerCase()==="r"&&!rotate.disabled){event.preventDefault();rotate.click();}});
    undo.addEventListener("click",()=>{if(history.length)ships=history.pop();else ships.pop();redraw();});
    clear.addEventListener("click",()=>{remember();ships=[];redraw();});
    randomize.addEventListener("click",()=>{
      remember();
      let next;
      do {
        next=[];const occupied=new Set();
        for(const length of fleetSizes){
          const options=[];
          for(let cell=0;cell<100;cell++)for(const vert of [false,true]){
            const candidate=footprint(cell,length,vert,occupied);
            if(candidate.valid)options.push(candidate.points);
          }
          if(!options.length)break;
          const ship=options[Math.floor(Math.random()*options.length)];next.push(ship);ship.forEach(c=>occupied.add(c));
        }
      } while(next.length!==fleetSizes.length);
      ships=next;redraw();
    });
    deploy.addEventListener("click",async()=>{
      if(saving||!allowed||ships.length!==fleetSizes.length)return;
      saving=true;redraw();
      try{await request("/api/command",{method:"POST",body:JSON.stringify({command:"place_battleship_fleet",session_id:session.id,ships})});
        localStorage.removeItem(draftKey);await refresh();}
      catch(error){saving=false;notify(error.message);redraw();}
    });
    redraw();
  } else if (sea && view.phase === "setup" && !session.private_fleet && !isObserver() && session.players.includes(state.snapshot?.callsign)) {
    let draft; try { draft = JSON.parse(localStorage.getItem(draftKey) || (sea ? "{}" : "[]")); } catch { draft = sea ? {} : []; }
    if (sea && Array.isArray(draft)) draft = {ships:draft};
    const ships = sea ? (draft.ships || []) : draft;
    draft.ships = ships;
    if (sea) { draft.minePlaced = Boolean(draft.minePlaced); draft.decoyPlaced = Boolean(draft.decoyPlaced); }
    let vertical = false;
    const size = fleetSizes[ships.length];
    const rotate = node("button", "button secondary", "Orientation: horizontal · rotate"); rotate.type = "button";
    rotate.addEventListener("click", () => { vertical = !vertical; rotate.textContent = `Orientation: ${vertical ? "vertical" : "horizontal"} · rotate`; });
    const stage = ships.length < fleetSizes.length ? `Place ${shipNames[ships.length]} ${ships.length+1} of ${fleetSizes.length} · length ${size}` : sea && !draft.minePlaced ? "Place the hidden mine on your patrol boat (optional)" : sea && !draft.decoyPlaced ? "Place the one-cell inflatable decoy in open water (optional)" : "Fleet ready";
    wrapper.append(node("p", "", stage));
    if (ships.length < fleetSizes.length) wrapper.append(rotate);
    if (sea && ships.length === fleetSizes.length && !draft.minePlaced) { const skip=node("button","button secondary","Skip mine");skip.type="button";skip.addEventListener("click",()=>{draft.minePlaced=true;localStorage.setItem(draftKey,JSON.stringify(draft));renderGame(session);});wrapper.append(skip); }
    if (sea && ships.length === fleetSizes.length && draft.minePlaced && !draft.decoyPlaced) { const skip=node("button","button secondary","Skip decoy");skip.type="button";skip.addEventListener("click",async()=>{draft.decoyPlaced=true;localStorage.setItem(draftKey,JSON.stringify(draft));try{await request("/api/command",{method:"POST",body:JSON.stringify({command:"place_battleship_fleet",session_id:session.id,ships,mines:draft.mine==null?[]:[draft.mine],decoys:[]})});localStorage.removeItem(draftKey);await refresh();}catch(error){notify(error.message);renderGame(session);}});wrapper.append(skip); }
    const grid = node("div", "battleship-grid");
    for (let i=0;i<100;i++) {
      const shipCells = ships.flat(), isShip = shipCells.includes(i);
      const button = node("button", `battleship-cell${isShip ? " bs-ship" : ""}`); button.type="button";
      button.addEventListener("click", async () => {
        if (ships.length < fleetSizes.length) {
          const row=Math.floor(i/10), col=i%10;
          const cells=Array.from({length:size},(_,j)=>vertical?(row+j)*10+col:row*10+col+j);
          if ((vertical&&row+size>10)||(!vertical&&col+size>10)||cells.some(x=>shipCells.includes(x))) { notify("That ship does not fit there."); return; }
          ships.push(cells);
        } else if (sea && !draft.minePlaced) {
          if (!(ships[5] || []).includes(i)) { notify("Place the mine on your patrol boat."); return; }
          draft.mine = i; draft.minePlaced = true;
        } else if (sea && draft.minePlaced && !draft.decoyPlaced) {
          if (isShip || draft.mine === i) { notify("Place the decoy in open water."); return; }
          draft.decoy = i; draft.decoyPlaced = true;
        }
        localStorage.setItem(draftKey,JSON.stringify(sea ? draft : ships));
        if(ships.length===fleetSizes.length && (!sea || (draft.minePlaced && draft.decoyPlaced))) {
          try { await request("/api/command",{method:"POST",body:JSON.stringify({command:"place_battleship_fleet",session_id:session.id,ships,mines:sea&&draft.mine!=null?[draft.mine]:[],decoys:sea&&draft.decoy!=null?[draft.decoy]:[]})}); localStorage.removeItem(draftKey); await refresh(); }
          catch(error){notify(error.message);}
        } else renderGame(session);
      }); grid.append(button);
    }
    drawFleet(grid, ships);
    if (sea && draft.mine != null) { const token=node("span","bs-token bs-mine","✹"); token.style.gridArea=`${Math.floor(draft.mine/10)+1} / ${draft.mine%10+1}`; grid.append(token); }
    if (sea && draft.decoy != null) { const token=node("span","bs-token bs-decoy","◉"); token.style.gridArea=`${Math.floor(draft.decoy/10)+1} / ${draft.decoy%10+1}`; grid.append(token); }
    wrapper.append(grid);
  } else if (view.phase === "setup") {
    wrapper.append(node("p", "", session.private_fleet ? "Fleet saved privately. Waiting for both stations to finish placement." : "Waiting for both stations to finish placement."));
    if (session.private_fleet) ownGrid();
  } else {
    if (view.phase === "reply" && sea)
      wrapper.append(node("p","turn-meta","Awaiting the defender's private hit/miss report. Both boards remain visible."));
    const boards=node("div","bs-board-pair");
    wrapper.append(boards);
    if (session.private_fleet){
      const ownPanel=node("section","bs-board-panel bs-own-panel");
      boards.append(ownPanel);ownGrid(ownPanel);
    }
    const enemyPanel=node("section","bs-board-panel bs-enemy-panel");
    boards.append(enemyPanel);
    enemyPanel.append(node("h3", "", !sea && view.shot_owner !== state.snapshot?.callsign ? `${view.shot_owner} · called coordinates` : "Enemy waters · your shots"));
    let seaMode = "shot", seaSeat = session.players.indexOf(state.snapshot?.callsign), used = view.used?.[seaSeat] || 0, seaGridButtons = [];
    if (sea && canAct(session)) {
      const controls=node("div","bs-tactical-controls");
      const sunk = ship => (session.private_fleet?.[ship] || []).length > 0 && session.private_fleet[ship].every(cell => (session.private_hits || []).includes(cell));
      const mode=node("select","field");
      [["shot","Fire one shot"],["double","Battleship salvo · once/game"],["sonar","Submarine sonar · once/game"]].forEach(([value,label])=>{const o=node("option","",label);o.value=value;o.disabled=(value==="double"&&((used&1)||sunk(1)))||(value==="sonar"&&((used&2)||sunk(4)));mode.append(o);});
      controls.append(mode);
      const moves=node("select","field");
      shipNames.forEach((name,i)=>{const o=node("option","",`${name}${i===2?" · free move":""}`);o.value=i;moves.append(o);});
      const direction=node("select","field"); [[0,"up"],[1,"down"],[2,"left"],[3,"right"]].forEach(([v,label])=>{const o=node("option","",label);o.value=v;direction.append(o);});
      const move=node("button","button secondary","Move vessel"); move.type="button"; const supply=view.moves?.[seaSeat]||0;
      const updateMove=()=>{const id=Number(moves.value);move.disabled=sunk(id)||(id===2?Boolean(used&4):supply<=0);};
      updateMove(); moves.addEventListener("change",updateMove); move.title=`Supplies: ${supply}; cruiser moves once for free`;
      move.addEventListener("click",()=>submitAction([200+Number(moves.value)*4+Number(direction.value)]));
      controls.append(moves,direction,move); wrapper.append(controls);
      seaMode=mode.value;
      const scan=node("div","bs-sonar-rows");
      scan.hidden=seaMode!=="sonar";
      for(let row=0;row<10;row++){const b=node("button","button secondary",`Scan row ${row+1}`);b.type="button";b.addEventListener("click",()=>submitAction([190+row]));scan.append(b);}
      wrapper.append(scan);
      mode.addEventListener("change",()=>{seaMode=mode.value;scan.hidden=seaMode!=="sonar";seaGridButtons.forEach((b,i)=>{const col=i%10;b.disabled=view.shots[i]!==0||seaMode==="sonar"||(seaMode==="double"&&(col>8||view.shots[i+1]!==0));});});
      if(view.sonar?.some(x=>x!==255)){const sonar=node("p","turn-meta",`Last sonar: ${view.sonar.map((x,i)=>x===255?null:`row ${i+1}: ${["clear","faint","strong"][x]}`).filter(Boolean).join(" · ")}`);wrapper.append(sonar);}
    }
    const grid=node("div","battleship-grid");
    grid.dataset.moveBoard="enemy";
    view.shots.forEach((value,index)=>{const row=Math.floor(index/10),col=index%10; const b=node("button",`battleship-cell bs-${value}`,value===1?"○":value===2?"✕":value>=3?`S${value-2}`:"");b.type="button";b.dataset.moveCell=index;b.title=`${String.fromCharCode(65+col)}${row+1}: ${value===1?"miss":value===2?"hit":value>=3?"sunk":"uncalled"}`;b.setAttribute("aria-label",b.title);b.disabled=!canAct(session)||(!sea&&(view.phase!=="call"||session.current_player!==state.snapshot?.callsign))||value!==0||(sea&&(seaMode==="sonar"||(seaMode==="double"&&(col>8||view.shots[index+1]!==0))));b.addEventListener("click",()=>submitAction([sea&&seaMode==="double"?100+row*9+col:index]));grid.append(b);if(sea)seaGridButtons.push(b);});
    const revealed=session.revealed_enemy_ships||[];
    if(revealed.length){
      const fleets=Array.from({length:fleetSizes.length},()=>[]);
      for(const ship of revealed)fleets[ship.id]=ship.cells;
      drawFleet(grid,fleets);
      for(const ship of revealed)for(const cell of ship.cells){
        const token=node("span","bs-token bs-own-sunk","S");
        token.style.gridArea=`${Math.floor(cell/10)+1} / ${cell%10+1}`;
        grid.append(token);
      }
    }
    enemyPanel.append(grid);
    if (sea) enemyPanel.append(node("p","turn-meta",revealed.length+" confirmed enemy ship(s) sunk · ○ White: miss · × Red: hit · S Amber: sunk"));
    if (!sea) enemyPanel.append(node("p","turn-meta",(5-new Set(view.shots.filter(v=>v>=3)).size)+" / 5 enemy ships afloat · ○ White: miss · × Red: hit · S Sunk"));
  }
  host.append(wrapper);
}

function renderNim(session, view, host) {
  const piles = node("div", "nim-piles");
  view.piles.forEach((count, index) => {
    const row = node("div", "nim-pile");
    row.dataset.movePile=index;
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
  const actor=state.snapshot.callsign, allowed=canAct(session)&&session.current_player===actor;
  const key=JSON.stringify([session.id,session.revision,actor]);
  for(const k of state.wordDrafts.keys())if(JSON.parse(k)[0]===session.id&&k!==key)state.wordDrafts.delete(k);
  const draft=state.wordDrafts.get(key)||{start:"H8",direction:"H",word:"",rack:[...(view.racks[actor]||[])]};
  state.wordDrafts.set(key,draft);
  const meta=node("div","word-meta",view.lexicon+" · OPEN RACKS · BAG "+view.bag);host.append(meta);
  const draftState=node("div","word-draft-state empty");draftState.id="word-draft-state";draftState.setAttribute("role","status");draftState.setAttribute("aria-live","polite");host.append(draftState);
  const draftLegend=node("div","word-draft-legend");
  draftLegend.append(node("span","word-legend-draft","◇ TEAL DASHED = DRAFT · NOT SENT"),node("span","word-legend-committed","■ TAN SOLID = ON BOARD"));
  host.append(draftLegend);
  const shell=node("div","word-board-shell"),axes=node("div","word-axes");axes.append(node("span"));
  for(let x=0;x<15;x++)axes.append(node("span","",String.fromCharCode(65+x)));
  shell.append(axes);const board=node("div","word-board");shell.append(board);host.append(shell);
  const cells=[];
  for(let y=0;y<15;y++){
    board.append(node("span","word-row-label",y+1));
    for(let x=0;x<15;x++){
      const i=y*15+x,tile=view.cells[i],premium=view.premiums[i],label=String.fromCharCode(65+x)+(y+1);
      const cell=node("button","word-cell"+(tile?" filled":" premium-"+premium.toLowerCase())+(i===112&&!tile?" center-star":""),tile||(i===112?"★":premium));
      cell.type="button";cell.disabled=!allowed;cell.setAttribute("aria-label",label);cell.dataset.index=i;cell.dataset.moveCell=i;
      cell.addEventListener("click",()=>{start.value=label;update();});
      cells.push(cell);board.append(cell);
    }
  }
  host.append(node("p","preview-note","★ Center · TW triple word · DW double word · TL triple letter · DL double letter"));
  const rackTitle=node("strong","","Your rack · click or drag tiles");host.insertBefore(rackTitle,shell);
  const rackRow=node("div","rack-row word-rack-interactive"),rackButtons=[];
  for(let slot=0;slot<draft.rack.length;slot++){
    const b=node("button","rack-tile");b.type="button";b.dataset.rackSlot=slot;
    let drag=null,suppressClick=false;
    b.addEventListener("click",()=>{if(!suppressClick)addTile(slot);suppressClick=false;});
    b.addEventListener("pointerdown",event=>{
      if(b.disabled||event.button!==0)return;
      suppressClick=false;drag={id:event.pointerId,x:event.clientX,y:event.clientY,active:false,target:null,ghost:null};
      b.setPointerCapture(event.pointerId);
    });
    b.addEventListener("pointermove",event=>{
      if(!drag||drag.id!==event.pointerId)return;
      if(!drag.active&&Math.hypot(event.clientX-drag.x,event.clientY-drag.y)<6)return;
      if(!drag.active){
        drag.active=true;drag.ghost=node("div","word-drag-ghost",b.textContent);document.body.append(drag.ghost);
      }
      event.preventDefault();drag.ghost.style.left=event.clientX+"px";drag.ghost.style.top=event.clientY+"px";
      drag.target?.classList.remove("drop-target");
      const target=document.elementFromPoint(event.clientX,event.clientY)?.closest(".word-cell");
      drag.target=target&&board.contains(target)&&!target.disabled?target:null;
      drag.target?.classList.add("drop-target");
    });
    const finishDrag=(event,commit)=>{
      if(!drag||drag.id!==event.pointerId)return;
      const d=drag;drag=null;d.ghost?.remove();d.target?.classList.remove("drop-target");
      if(d.active){suppressClick=true;if(commit&&d.target&&b.isConnected)addTile(slot,Number(d.target.dataset.index));}
      if(b.hasPointerCapture(event.pointerId))b.releasePointerCapture(event.pointerId);
    };
    b.addEventListener("pointerup",event=>finishDrag(event,true));
    b.addEventListener("pointercancel",event=>finishDrag(event,false));
    b.addEventListener("lostpointercapture",event=>finishDrag(event,false));
    rackButtons.push(b);rackRow.append(b);
  }
  host.insertBefore(rackRow,shell);
  for(const [player,rack] of Object.entries(view.racks))if(player!==actor)host.append(node("p","preview-note",player+" · "+rack.join("  ")));
  const form=node("form","word-controls");
  const start=node("input","field");start.id="word-start";start.value=draft.start;start.setAttribute("aria-label","Starting square");start.maxLength=3;
  const dir=node("select","field");dir.id="word-direction";
  for(const [value,label] of [["H","Across"],["V","Down"]]){const option=node("option","",label);option.value=value;dir.append(option);}dir.value=draft.direction;
  const word=node("input","field");word.id="word-text";word.value=draft.word;word.placeholder="Word · {A} for blank";word.autocomplete="off";
  const place=node("button","button primary","Submit word");place.type="submit";place.title="Only this button submits tiles to the game";
  form.append(start,dir,word,place);host.append(form);
  const tools=node("div","word-draft-tools");
  const undo=node("button","button secondary","Undo tile"),clear=node("button","button secondary","Clear"),shuffle=node("button","button secondary","Shuffle rack");
  for(const b of [undo,clear,shuffle])b.type="button";
  tools.append(undo,clear,shuffle);host.append(tools);
  const note=node("p","preview-note word-live-score");note.setAttribute("aria-live","polite");host.append(note);
  const exchangeForm=node("form","word-secondary"),exchange=node("input","field");exchange.placeholder="Exchange letters · ? blank";
  const exchangeButton=node("button","button","Exchange");exchangeButton.type="submit";exchangeButton.disabled=!allowed||view.bag<7;
  const pass=node("button","button","Pass");pass.type="button";pass.disabled=!allowed;pass.addEventListener("click",()=>submitAction([1]));
  exchangeForm.append(exchange,exchangeButton,pass);host.append(exchangeForm);
  exchangeForm.addEventListener("submit",event=>{event.preventDefault();if(allowed)sendCommand("exchange_word",{session_id:session.id,tiles:exchange.value});});
  for(const c of [start,dir,word,undo,clear,shuffle,exchange])c.disabled=!allowed;
  let generation=0,timer=null,valid=false,submitting=false;
  const tokens=()=>word.value.trim().toUpperCase().match(/\{[A-Z]\}|[A-Z]/g)||[];
  function coords(){
    const m=/^([A-O])(1[0-5]|[1-9])$/.exec(start.value.trim().toUpperCase());
    return m?[m[1].charCodeAt(0)-65,Number(m[2])-1]:null;
  }
  function addTile(slot,target=null){
    if(!allowed||!rackButtons[slot]||rackButtons[slot].disabled)return;
    let letter=draft.rack[slot];
    const dx=dir.value==="H"?1:0,dy=1-dx;
    if(target!==null){
      if(target<0||target>=225||view.cells[target]){note.textContent="Drop onto an empty square";return;}
      const xy=coords(),t=tokens();
      if(!word.value.trim()){
        let first=target,step=dx?1:15;
        while((dx?first%15:Math.floor(first/15))>0&&view.cells[first-step])first-=step;
        start.value=String.fromCharCode(65+first%15)+(Math.floor(first/15)+1);
      }else{
        if(!xy||t.join("")!==word.value.trim().toUpperCase())return;
        let x=xy[0]+t.length*dx,y=xy[1]+t.length*dy;
        while(x<15&&y<15&&view.cells[y*15+x]){x+=dx;y+=dy;}
        if(x>=15||y>=15||target!==y*15+x){note.textContent="Extend at the next empty square, or clear the draft to start elsewhere";return;}
      }
    }
    if(letter==="?"){
      const value=(window.prompt("Assign blank tile a letter A–Z")||"").trim().toUpperCase();
      if(!/^[A-Z]$/.test(value))return;letter="{"+value+"}";
    }
    const xy=coords(),t=tokens();if(!xy||t.join("")!==word.value.trim().toUpperCase())return;
    let x=xy[0]+t.length*dx,y=xy[1]+t.length*dy;
    const include=()=>{while(x<15&&y<15&&view.cells[y*15+x]){t.push(view.cells[y*15+x].toUpperCase());x+=dx;y+=dy;}};
    include();if(x>=15||y>=15)return;t.push(letter);x+=dx;y+=dy;include();word.value=t.join("");update();
  }
  undo.addEventListener("click",()=>{
    const t=tokens(),xy=coords(),step=dir.value==="H"?1:15;
    while(t.length){const i=xy?xy[1]*15+xy[0]+(t.length-1)*step:-1;t.pop();if(!view.cells[i])break;}
    word.value=t.join("");update();
  });
  clear.addEventListener("click",()=>{word.value="";update();});
  shuffle.addEventListener("click",()=>{for(let i=draft.rack.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[draft.rack[i],draft.rack[j]]=[draft.rack[j],draft.rack[i]];}update();});
  function update(){
    draft.start=start.value;draft.direction=dir.value;draft.word=word.value;
    const preview=parseWord(start.value,dir.value,word.value.trim(),view.cells), t=tokens(),used=[];
    const selected=parseWord(start.value,dir.value,"A",view.cells).positions;
    let offset=0;
    for(const [i,letter] of preview.positions){
      const token=t[offset++];if(!view.cells[i])used.push(token?.startsWith("{")?"?":letter);
    }
    for(let i=0;i<cells.length;i++){
      const c=cells[i],tile=view.cells[i],letter=preview.positions.get(i);
      c.classList.toggle("preview",Boolean(letter));c.classList.toggle("preview-conflict",Boolean(tile&&letter&&tile.toUpperCase()!==letter));
      c.classList.toggle("draft-start",selected.has(i));c.classList.toggle("draft-tile",Boolean(letter&&!tile));
      c.textContent=tile||letter||(i===112?"★":view.premiums[i]);
    }
    rackButtons.forEach((b,i)=>{const tile=draft.rack[i],n=used.indexOf(tile);b.textContent=tile;b.disabled=!allowed||tile==="·"||n>=0;if(n>=0)used.splice(n,1);});
    valid=false;place.disabled=true;place.textContent="Submit word";clearTimeout(timer);const g=++generation;
    const proposed=[...preview.positions.keys()].filter(i=>!view.cells[i]).length;
    draftState.classList.toggle("empty",!word.value.trim());
    draftState.classList.toggle("confirmed",!word.value.trim()&&Boolean(session.last_confirmed_word));
    draftState.classList.remove("submitting");
    draftState.textContent=word.value.trim()?"◇ DRAFT — NOT SUBMITTED · "+proposed+" proposed tile"+(proposed===1?"":"s"):
      session.last_confirmed_word?"✓ COMMITTED TO BOARD · Revision "+session.last_confirmed_word.revision:
      "NO DRAFT · Choose a square and build a word";
    if(!allowed){note.textContent="Waiting for your turn. Nothing has been sent.";return;}
    if(submitting){note.textContent="Submitting word… waiting for the table";return;}
    if(!word.value.trim()){
      note.textContent=session.last_confirmed_word?"Your last confirmed word: "+session.last_confirmed_word.description+". Tan tiles are permanent.":
        "Click or drag rack tiles, or type a word. Nothing transmits until you press Submit word.";
      return;
    }
    note.textContent="Draft only · Checking dictionary and score…";
    timer=setTimeout(async()=>{
      try{
        const result=await request("/api/command",{method:"POST",body:JSON.stringify({command:"preview_word",session_id:session.id,revision:session.revision,start:start.value,direction:dir.value,word:word.value})});
        if(g!==generation||!host.isConnected)return;
        valid=true;place.disabled=submitting;place.textContent="Submit word · +"+result.score+" points";
        note.textContent="Legal word · "+result.score+" points available. Submit to lock these tiles onto the board.";
      }catch(error){if(g===generation&&host.isConnected)note.textContent="Not ready to submit: "+error.message+". Keep editing; this is still a draft.";}
    },150);
  }
  form.addEventListener("submit",async event=>{
    event.preventDefault();if(!allowed||!valid||submitting)return;
    submitting=true;place.disabled=true;
    const expectedWord=word.value.trim().toUpperCase(),revision=session.revision;
    draftState.textContent="SUBMITTING · Awaiting confirmation";draftState.classList.add("submitting");
    try {
      const result=await sendCommand("place_word",{session_id:session.id,revision:session.revision,start:start.value,direction:dir.value,word:word.value});
      if(result){
        try {
          const journal=await request("/api/moves/"+encodeURIComponent(session.id));
          const committed=journal.moves.some(move=>move.revision>revision&&move.actor===actor&&/place/i.test(move.description));
          notify(committed?"✓ "+expectedWord+" committed to the board":
            "Word submitted for delivery · Awaiting the host's confirmation");
        } catch {notify("Word request sent · Check table status for confirmation");}
      }
    } finally {submitting=false;if(host.isConnected)update();}
  });
  start.addEventListener("input",update);dir.addEventListener("change",update);word.addEventListener("input",update);update();
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

function renderSignalSiege(session, view, host) {
  const wrap = node("section", "siege-layout");
  const crawl = node("a", "button", "Offline dungeon · The Silent Vault"); crawl.href = "siege-crawl.html"; crawl.target = "_blank"; wrap.append(crawl);
  const hero = node("header", "siege-hero");
  const wave = node("div", "siege-wave", `WAVE ${view.wave} / ${view.waves}   ·   ROUND ${view.round + 1}`);
  const title = node("h2", "", view.result === "victory" ? "Signal cleared" : view.result === "defeat" ? "Link lost" : "The interference core");
  const subtitle = node("p", "", view.phase === "draft" ? "Wave cleared. Each station chooses one upgrade for its deck." :
    view.result === "victory" ? "All three waves survived. The crew's earned cards remain in this run." :
    view.result === "defeat" ? "The formation went dark. Try a different mix of attack, defense, and support." :
    `The core broadcasts ${view.pulse_damage} damage to every station ${view.pulse_every === 2 ? "every second round" : "each round"}${view.jam ? `, weakened by ${view.jam} jam` : ""}.`);
  const integrity = node("div", "siege-integrity");
  const meterText = node("div", "siege-meter-label");
  meterText.append(node("span", "", "CORE INTEGRITY"), node("strong", "", `${view.boss_hp} / ${view.boss_max_hp}`));
  const meter = node("div", "siege-meter");
  const fill = node("div", "siege-meter-fill"); fill.style.width = `${100 * view.boss_hp / view.boss_max_hp}%`; meter.append(fill);
  integrity.append(meterText, meter);
  hero.append(wave, title, subtitle, integrity); wrap.append(hero);

  const roster = node("div", "siege-roster");
  for (const station of view.stations) {
    const card = node("article", `siege-station${station.callsign === session.current_player ? " on-turn" : ""}`);
    const top = node("div", "siege-station-top");
    top.append(node("strong", "", station.callsign), node("span", "", `${station.hp}/${station.max_hp} HP`));
    const hp = node("div", "siege-health");
    const hpFill = node("span", ""); hpFill.style.width = `${station.hp / station.max_hp * 100}%`; hp.append(hpFill);
    const stats = node("div", "siege-stats", `◈ ${station.shield} SHIELD    ✦ ${station.focus} FOCUS    ▤ ${station.deck_size} CARDS`);
    const myHand = !isObserver() && (isPractice() ? station.callsign === session.current_player : station.callsign === state.snapshot?.callsign);
    const secret = node("div", "siege-card-backs");
    if (!myHand) {
      for (let i = 0; i < 3; i++) secret.append(node("span", "siege-card-back", "✦"));
    } else {
      secret.append(node("span", "siege-own-hand", "YOUR HAND ↓"));
    }
    card.append(top, hp, stats, secret); roster.append(card);
  }
  wrap.append(roster);

  const controls = node("section", "siege-controls");
  const actor = view.stations.find(station => station.callsign === (isPractice() ? session.current_player : state.snapshot?.callsign));
  const canChoose = canAct(session);
  if (view.phase === "draft") {
    controls.append(node("h3", "", "Choose a card reward"));
    controls.append(node("p", "siege-helper", `${session.current_player} is picking one card to add to their deck. Cards are permanent for this run.`));
    const offers = node("div", "siege-cards");
    for (const reward of view.rewards) {
      const card = node("button", `siege-card ${reward.family}`);
      card.type = "button"; card.disabled = !canChoose;
      card.append(node("span", "siege-card-number", `REWARD ${reward.slot + 1}`), node("strong", "", reward.name), node("span", "siege-art", "✦"), node("p", "", reward.description), node("span", "siege-play", "ADD TO DECK +"));
      card.addEventListener("click", () => submitAction([0x40 | reward.slot])); offers.append(card);
    }
    controls.append(offers);
  } else if (view.phase === "battle") {
    controls.append(node("h3", "", actor ? "Your signal cards" : "Awaiting your turn"));
    controls.append(node("p", "siege-helper", actor ? "Choose one of your three cards. The card cycles to the back of your deck." :
      isObserver() ? "Observer mode · hands are concealed" : "The other station's hand is not shown here."));
    if (actor) {
      const row = node("div", "siege-target-row");
      const text = node("label", "", "Support target"); text.htmlFor = "siege-target";
      const target = node("select", "field"); target.id = "siege-target";
      view.stations.forEach((station, i) => {
        const option = node("option", "", station.callsign); option.value = String(i); target.append(option);
      });
      target.value = String(view.stations.findIndex(station => station.callsign === actor.callsign));
      target.disabled = !canChoose;
      row.append(text, target); controls.append(row);
      const hand = node("div", "siege-cards");
      for (const choice of actor.hand) {
        const card = node("button", `siege-card ${choice.family}`);
        card.type = "button"; card.disabled = !canChoose;
        card.append(node("span", "siege-card-number", `CARD ${choice.slot + 1}`), node("strong", "", choice.name), node("span", "siege-art", "✦"),
          node("p", "", choice.description), node("span", "siege-play", "PLAY CARD →"));
        card.addEventListener("click", () => submitAction([(Number(target.value) << 2) | choice.slot]));
        hand.append(card);
      }
      controls.append(hand);
    }
  } else {
    controls.append(node("h3", "", view.result === "victory" ? "Formation victorious" : "Formation defeated"));
    controls.append(node("p", "siege-helper", "Create a new table to try a different strategy."));
  }
  wrap.append(controls); host.append(wrap);
}

function renderScorchedEarth(session, view, host) {
  view = session.animation_view || view;
  const shotKey = `${session.id}:${session.animation_revision ?? session.revision}`;
  const animateShot = Boolean(view.last_actor && view.impact && view.trajectory.length > 1 && state.lastTankAnimation !== shotKey);
  const beforeTerrain = animateShot ? view.previous_terrain || view.terrain : view.terrain;
  const wrap = node("div", "tank-layout");
  const arena = node("div", "tank-arena");
  const camera = state.tankCameras.get(session.id) || {zoom:1,center:null,follow:true};
  state.tankCameras.set(session.id,camera);
  let followShot = camera.follow;
  const manual = () => {followShot=false;camera.follow=false;follow.checked=false;};
  arena.setAttribute("tabindex", "0");
  arena.setAttribute("aria-label", "Scrollable battlefield. Use WASD or arrows, drag the map, or click to center the view.");
  const svg = svgElement("svg", { class: "tank-map", viewBox: `0 0 ${view.width * 24} ${view.sky_height * 24}`,
    preserveAspectRatio: "none",
    role: "img", "aria-label": `${view.map_name} battlefield. Both tanks are visible; adjust the weapon and firing angle.` });
  svg.style.width = `${Math.round(view.width * 20 * camera.zoom)}px`;
  svg.style.height = `${Math.round(view.sky_height * 14 * camera.zoom)}px`;
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
  beforeTerrain.forEach((elevation, x) => terrainPoints.push(`${x * 24 + 12},${height - elevation * 24}`));
  terrainPoints.push(`${width},${height}`);
  const ground = svgElement("polygon", { points: terrainPoints.join(" "), class: "tank-ground" });
  svg.append(ground);
  function settleTerrain() {
    const started = performance.now();
    const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    function settle(now) {
      if (!svg.isConnected) return;
      const progress = reduced ? 1 : Math.min(1, (now - started) / 650);
      const points = [`0,${height}`];
      view.terrain.forEach((value, x) => points.push(`${x * 24 + 12},${height - (beforeTerrain[x] + (value - beforeTerrain[x]) * progress) * 24}`));
      points.push(`${width},${height}`); ground.setAttribute("points", points.join(" "));
      for (const tank of view.tanks) {
        const group = svg.querySelector(`[data-seat="${view.tanks.indexOf(tank)}"]`);
        group?.setAttribute("transform", `translate(0,${-(beforeTerrain[tank.x] - view.terrain[tank.x]) * (1 - progress) * 24})`);
      }
      if (progress < 1) requestAnimationFrame(settle);
    }
    requestAnimationFrame(settle);
  }
  const barrels = new Map(), tankArtwork = new Map();
  for (const [index, tank] of view.tanks.entries()) {
    const isLocalTank = tank.player === state.snapshot.callsign;
    const seatLabel = isPractice() ? tank.player : isLocalTank ? "YOU" : "OPPONENT";
    const group = svgElement("g", { class: `tank-unit ${isLocalTank ? "friendly" : "enemy"}` });
    group.dataset.seat = String(index);
    group.setAttribute("transform", `translate(0,${-(beforeTerrain[tank.x] - view.terrain[tank.x]) * 24})`);
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
  if (view.impact) svg.append(svgElement("circle", { cx: view.impact.x * 24, cy: (view.sky_height - view.impact.y) * 24,
    r: Math.max(8, view.weapons[view.impact.weapon].radius * 24), class: "tank-impact" }));
  const preview = svgElement("polyline", { class: "tank-preview-trajectory", points: "" });
  arena.append(svg);
  const panHint = node("p", "tank-pan-hint", `↔ ${view.width}-column battlefield · WASD / arrows · Click to center · Drag map or minimap`);
  const navigation = node("div", "tank-navigation");
  const navLabel = node("span", "tank-nav-label", `${view.map_name.toUpperCase()}  ·  SECTOR MAP`);
  const overview = svgElement("svg", { class: "tank-minimap", viewBox: `0 0 ${view.width * 8} 100`,
    preserveAspectRatio: "none", role: "group", tabindex: "0", "aria-label": "Interactive overview. Click or drag to pan; WASD and arrows move the camera." });
  const overviewPoints = [`0,100`, ...view.terrain.map((h, x) => `${x * 8 + 4},${100 - h * 5}`), `${view.width * 8},100`];
  overview.append(svgElement("polygon", { points: overviewPoints.join(" "), class: "tank-minimap-ground" }));
  for (const [index, tank] of view.tanks.entries()) {
    overview.append(svgElement("circle", { cx: tank.x * 8, cy: 100 - tank.world_y * 5, r: 7,
      class: `tank-minimap-player player-${index}` }));
  }
  const visibleRegion = svgElement("rect", { x: 0, y: 0, width: 300, height: 100, rx: 4, class: "tank-minimap-viewport" });
  overview.append(visibleRegion);
  const zoomOut = node("button", "button tank-zoom", "−"); zoomOut.type = "button"; zoomOut.title = "Zoom out";
  const zoomIn = node("button", "button tank-zoom", "+"); zoomIn.type = "button"; zoomIn.title = "Zoom in";
  const follow = node("input");follow.type="checkbox";follow.checked=followShot;follow.addEventListener("change",()=>{followShot=follow.checked;camera.follow=followShot;});
  const followLabel=node("label","tank-follow","Follow shots");followLabel.prepend(follow);
  const zoomText = node("span", "tank-zoom-label", `${Math.round(camera.zoom * 100)}%`);
  function updateMinimap() {
    const mapWidth = svg.getBoundingClientRect().width || 1;
    const viewWidth = Math.min(view.width * 8, view.width * 8 * arena.clientWidth / mapWidth);
    visibleRegion.setAttribute("width", viewWidth);
    visibleRegion.setAttribute("x", view.width * 8 * arena.scrollLeft / mapWidth);
    if(arena.clientWidth&&arena.clientHeight)camera.center=[(arena.scrollLeft+arena.clientWidth/2)/mapWidth,(arena.scrollTop+arena.clientHeight/2)/(svg.getBoundingClientRect().height||1)];
  }
  function centerOn(fraction) {
    manual();
    arena.scrollLeft = Math.max(0, fraction * svg.getBoundingClientRect().width - arena.clientWidth / 2);
    updateMinimap();
  }
  function panOverview(event) {
    const rect = overview.getBoundingClientRect();
    if (rect.width) centerOn(Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)));
  }
  overview.addEventListener("pointerdown", event => {
    event.preventDefault(); overview.focus({ preventScroll: true });
    overview.setPointerCapture(event.pointerId);
    panOverview(event);
  });
  overview.addEventListener("pointermove", event => {
    if (overview.hasPointerCapture(event.pointerId)) panOverview(event);
  });
  overview.addEventListener("pointerup", event => { if (overview.hasPointerCapture(event.pointerId)) overview.releasePointerCapture(event.pointerId); });
  function panKeys(event) {
    if (event.shiftKey || event.ctrlKey || event.altKey || event.metaKey || event.target.closest("input,select,textarea,button,[contenteditable]")) return;
    const delta = { a: [-1, 0], d: [1, 0], w: [0, -1], s: [0, 1], arrowleft: [-1, 0], arrowright: [1, 0], arrowup: [0, -1], arrowdown: [0, 1] }[event.key.toLowerCase()];
    if (!delta) return;
    event.preventDefault(); manual();
    arena.scrollLeft += delta[0] * Math.max(32, arena.clientWidth / 8);
    arena.scrollTop += delta[1] * Math.max(32, arena.clientHeight / 8);
    updateMinimap();
  }
  arena.addEventListener("keydown", panKeys); overview.addEventListener("keydown", panKeys);
  let drag = null;
  arena.addEventListener("pointerdown", event => {
    if (event.button !== 0) return;
    event.preventDefault(); manual(); arena.focus({ preventScroll: true });
    arena.setPointerCapture(event.pointerId); arena.classList.add("panning");
    drag = { x: event.clientX, y: event.clientY, left: arena.scrollLeft, top: arena.scrollTop, moved: false };
  });
  arena.addEventListener("pointermove", event => {
    if (!drag) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    drag.moved ||= Math.abs(dx) + Math.abs(dy) > 4;
    arena.scrollLeft = drag.left - dx; arena.scrollTop = drag.top - dy; updateMinimap();
  });
  function endPan(event) {
    if (drag && !drag.moved && event.type !== "pointercancel") {
      const rect = arena.getBoundingClientRect();
      arena.scrollLeft = drag.left + event.clientX - rect.left - arena.clientWidth / 2;
      arena.scrollTop = drag.top + event.clientY - rect.top - arena.clientHeight / 2;
    }
    drag = null; arena.classList.remove("panning");
    if (arena.hasPointerCapture(event.pointerId)) arena.releasePointerCapture(event.pointerId);
    updateMinimap();
  }
  arena.addEventListener("pointerup", endPan); arena.addEventListener("pointercancel", endPan);
  arena.addEventListener("lostpointercapture", () => { drag = null; arena.classList.remove("panning"); });
  arena.addEventListener("wheel", () => { manual(); }, { passive: true });
  function zoomBy(delta) {
    const mapWidth = svg.getBoundingClientRect().width || 1;
    const centerFraction = (arena.scrollLeft + arena.clientWidth / 2) / mapWidth;
    camera.zoom = Math.max(.75, Math.min(1.6, Math.round((camera.zoom + delta) * 100) / 100));
    svg.style.width = `${Math.round(view.width * 20 * camera.zoom)}px`;
    svg.style.height = `${Math.round(view.sky_height * 14 * camera.zoom)}px`;
    zoomText.textContent = `${Math.round(camera.zoom * 100)}%`;
    centerOn(centerFraction);
  }
  zoomOut.addEventListener("click", () => zoomBy(-.2));
  zoomIn.addEventListener("click", () => zoomBy(.2));
  arena.addEventListener("scroll", updateMinimap, { passive: true });
  navigation.append(navLabel, overview, zoomOut, zoomText, zoomIn);
  const cameraTools=node("div","tank-camera-tools");
  const own=view.tanks.find(t=>t.player===state.snapshot.callsign)||view.tanks[0],other=view.tanks.find(t=>t!==own);
  for(const [label,target] of [[own.player===state.snapshot.callsign?"My tank":"Tank 1",own],[own.player===state.snapshot.callsign?"Opponent":"Tank 2",other]]){
    const btn=node("button","button secondary",label);btn.type="button";
    btn.addEventListener("click",()=>{centerOn(target.x/view.width);arena.scrollTop=(view.sky_height-target.world_y)/view.sky_height*svg.getBoundingClientRect().height-arena.clientHeight/2;updateMinimap();});
    cameraTools.append(btn);
  }
  cameraTools.append(followLabel);
  const side = node("aside", "tank-side");
  side.append(node("div", "tank-map-name", view.map_name), node("div", "tank-wind", `${view.wind.direction} ${view.wind.label} · shifts each turn`), node("div", "tank-help", isPractice()
    ? "Aim with the wind and reshape the hills. Cannon ammunition is unlimited; other rounds, fuel, and repairs are limited."
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
  const activeTank=view.tanks.find(tank=>tank.player===state.snapshot.callsign);
  const allowed=canAct(session)&&session.current_player===state.snapshot.callsign;
  for (const item of view.weapons) {
    const stock = activeTank?.ammo[item.id] ?? 0;
    const option = node("option", "", `${item.name} · ${item.tag} · ${item.id ? `${stock} rounds` : "∞ rounds"}`);
    option.value = item.id; option.disabled = !stock; weapon.append(option);
  }
  const weaponKey = JSON.stringify([session.id, activeTank?.player]);
  const selectedWeapon = state.tankWeapons.get(weaponKey) ?? 0;
  weapon.value = String(activeTank?.ammo[selectedWeapon] ? selectedWeapon : 0);
  weapon.addEventListener("change", () => state.tankWeapons.set(weaponKey, Number(weapon.value)));
  const angleLabel = node("label", "tank-angle-label", "Firing angle · 45°");
  const angle = node("input", "field tank-angle"); angle.type = "range"; angle.min = String(view.min_angle); angle.max = String(view.max_angle); angle.value = String(state.tankAim.get(weaponKey)?.angle ?? 45);
  angleLabel.append(angle);
  const submit = node("button", "button primary", "Fire weapon"); submit.type = "submit"; submit.disabled = !allowed;
  const powerLabel = node("label", "tank-angle-label", "Power · 100%");
  const power = node("input", "field tank-angle"); power.type = "range";
  power.min = String(view.min_power); power.max = String(view.max_power); power.step = String(view.power_step); power.value = String(state.tankAim.get(weaponKey)?.power ?? view.max_power);
  powerLabel.append(power);
  const angleNumber=node("input","field tank-number");angleNumber.type="number";angleNumber.min=angle.min;angleNumber.max=angle.max;angleNumber.value=angle.value;angleNumber.setAttribute("aria-label","Angle in degrees");
  const powerNumber=node("input","field tank-number");powerNumber.type="number";powerNumber.min=power.min;powerNumber.max=power.max;powerNumber.step=power.step;powerNumber.value=power.value;powerNumber.setAttribute("aria-label","Power percent");
  angleLabel.append(angleNumber);powerLabel.append(powerNumber);
  const info=node("p","tank-help"),predicted=node("p","tank-help");
  const adjust=(a,p)=>{if(!allowed)return;angle.value=String(Math.max(+angle.min,Math.min(+angle.max,+angle.value+a)));power.value=String(Math.max(+power.min,Math.min(+power.max,+power.value+p*view.power_step)));updatePreview();};
  for(const target of [arena,overview])target.addEventListener("keydown",event=>{
    if(!event.shiftKey)return;const delta={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,1],ArrowDown:[0,-1]}[event.key];
    if(delta){event.preventDefault();adjust(...delta);}
  });
  for(const control of [weapon,angle,power,angleNumber,powerNumber])control.disabled=!allowed;
  angleNumber.addEventListener("change",()=>{angle.value=angleNumber.value;updatePreview();});
  powerNumber.addEventListener("change",()=>{power.value=String(Math.round(Number(powerNumber.value)/view.power_step)*view.power_step);updatePreview();});
  form.append(weapon,info,angleLabel,powerLabel,predicted,node("p","tank-help","Shift + arrows: precise aim"),submit);
  function updatePreview() {
    angleNumber.value=angle.value;powerNumber.value=power.value;
    state.tankAim.set(weaponKey,{angle:Number(angle.value),power:Number(power.value)});
    const item=view.weapons[Number(weapon.value)]||view.weapons[0];
    info.textContent=item.tag+" · "+item.damage+" damage · "+item.radius+" blast radius · "+(item.id?(activeTank?.ammo[item.id]||0)+" in stock":"Unlimited");
    predicted.textContent=allowed?"Trajectory preview":"Waiting for your turn";
    powerLabel.firstChild.textContent = `Power · ${power.value}%`;
    angleLabel.firstChild.textContent = `Firing angle · ${angle.value}°`;
    const shooterName=state.snapshot.callsign;
    const shooter = view.tanks.find(tank => tank.player === shooterName);
    barrels.get(shooterName)?.setAngle(Number(angle.value));
    if (!shooter || !allowed) { preview.remove(); return; }
    const {points,kind} = traceTankPreview(view, shooter, view.weapons[Number(weapon.value)], Number(angle.value), Number(power.value), true);
    predicted.textContent = "Predicted: " + ({tank:"direct hit",terrain:"terrain impact",edge:"leaves battlefield"}[kind]);
    preview.setAttribute("points", points.map(([x, y]) => `${x * 24},${(view.sky_height - y) * 24}`).join(" "));
    if (!preview.isConnected) svg.append(preview);
  }
  power.addEventListener("input", updatePreview);
  angle.addEventListener("input", updatePreview);
  weapon.addEventListener("change", updatePreview);
  updatePreview();
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!allowed || submit.disabled) return;
    const degrees = Number(angle.value);
    if (!Number.isInteger(degrees) || degrees < view.min_angle || degrees > view.max_angle) { notify(`Firing angle must be between ${view.min_angle}° and ${view.max_angle}°.`); return; }
    const strength = Number(power.value);
    if (!Number.isInteger(strength) || strength < view.min_power || strength > view.max_power || strength % view.power_step) { notify("Choose a valid firing power."); return; }
    const slot = strength === 100 ? 0 : (strength - 25) / 5 + 1;
    const bytes = [Number(weapon.value) | slot << 3, degrees];
    submit.disabled = true;
    try {
      const action = bytes.map(value => value.toString(16).padStart(2, "0")).join("");
      const prediction = await request("/api/command", { method: "POST", body: JSON.stringify({ command: "preview_shot", session_id: session.id, action }) });
      renderGame({ ...session, has_pending_action: true, animation_revision: session.revision + 1, animation_view: prediction.view });
      const result = await sendCommand("legal_action", { session_id: session.id, action });
      if (!result) { state.tankPlayback = null; state.sessionSignature = ""; await refresh(); }
    } catch (error) { submit.disabled = !allowed; notify(error.message); }
  });
  const armory=node("button","button secondary","Armory · "+(activeTank?.credits??0)+" credits");armory.type="button";armory.disabled=!activeTank;
  const shop=node("dialog","tank-armory-dialog");
  shop.append(node("h3","","Armory"),node("p","tank-help","Buying one round uses your turn."));
  for(const item of view.weapons){
    const card=node("div","tank-armory-card");card.append(node("strong","",item.name+" · "+(item.id?(activeTank?.ammo[item.id]||0)+" rounds":"Unlimited")),node("p","tank-help",item.tag+" · Damage "+item.damage+" · Radius "+item.radius+" · Speed "+item.speed));
    if(item.id){const buy=node("button","button secondary","Buy one round · "+item.cost+" credits");buy.type="button";buy.disabled=!allowed||!activeTank||activeTank.credits<item.cost||activeTank.ammo[item.id]>=254;buy.addEventListener("click",()=>{shop.close();submitAction([view.actions.buy,item.id]);});card.append(buy);}
    shop.append(card);
  }
  const close=node("button","button secondary","Close");close.type="button";close.addEventListener("click",()=>shop.close());shop.append(close);
  armory.addEventListener("click",()=>shop.showModal());
  const tactics = node("div", "tank-tactics");
  const drive = node("div", "tank-drive-controls");
  const left = node("button", "button secondary", "◀ Drive"); left.type = "button"; left.disabled = !allowed || !activeTank?.fuel;
  const right = node("button", "button secondary", "Drive ▶"); right.type = "button"; right.disabled = !allowed || !activeTank?.fuel;
  left.addEventListener("click", () => submitAction([view.actions.drive, 0]));
  right.addEventListener("click", () => submitAction([view.actions.drive, 1]));
  drive.append(left, right); tactics.append(drive);
  const repair = node("button", "button secondary", "Repair +24 HP"); repair.type = "button";
  repair.disabled = !allowed || !activeTank || activeTank.repair_kits <= 0 || activeTank.hp >= 100;
  repair.addEventListener("click", () => submitAction([view.actions.repair, 0]));
  const shield = node("button", "button secondary", "Raise shield"); shield.type = "button";
  shield.disabled = !allowed || !activeTank || activeTank.shielded;
  shield.addEventListener("click", () => submitAction([view.actions.shield, 0]));
  tactics.append(repair, shield); side.append(tactics);
  side.append(armory,form,shop);
  const callout = node("div", "tank-shot-result", view.last_action ? `${view.last_actor} ${view.last_action}` : "Take the first turn");
  side.append(callout);
  const replay = node("button", "button secondary", "Instant replay"); replay.type = "button";
  replay.disabled = !view.impact;
  replay.addEventListener("click", () => {
    state.lastTankAnimation = null;
    camera.follow=true;
    renderGame(session);
  });
  side.append(replay);
  wrap.append(navigation,cameraTools,arena,panHint,side); host.append(wrap);
  // On a panoramic map, start near the tank whose turn it is; players can pan
  // across the entire battlefield with touch, trackpad or the scrollbar.
  const savedCenter=camera.center?.slice();
  const activeViewTank = activeTank || view.tanks.find(tank => tank.player === view.turn);
  if (activeViewTank && !animateShot) requestAnimationFrame(() => {
    const x = activeViewTank.x / view.width * svg.getBoundingClientRect().width;
    if(savedCenter){arena.scrollLeft=savedCenter[0]*svg.getBoundingClientRect().width-arena.clientWidth/2;arena.scrollTop=savedCenter[1]*svg.getBoundingClientRect().height-arena.clientHeight/2;updateMinimap();return;}
    arena.scrollLeft = Math.max(0, x - arena.clientWidth * .35);
    arena.scrollTop = Math.max(0, (view.sky_height - activeViewTank.world_y) / view.sky_height * svg.getBoundingClientRect().height - arena.clientHeight * .65);
    updateMinimap();
  });
  const actor = view.last_actor;
  if (animateShot) {
    state.lastTankAnimation = shotKey;
    const playback = { id: session.id, svg };
    state.tankPlayback = playback;
    setTimeout(() => {
      if (state.tankPlayback !== playback) return;
      state.tankPlayback = null;
      state.sessionSignature = "";
      refresh();
    }, 6200);
    const firingArt = tankArtwork.get(actor);
    if (firingArt) { firingArt.classList.add("recoiling"); setTimeout(() => firingArt.classList.remove("recoiling"), 550); }
    animateTankShot(svg, view, actor, callout, settleTerrain, (x, y) => {
      if (!followShot) return;
      const pixel = x / view.width * svg.getBoundingClientRect().width;
      arena.scrollLeft = Math.max(0, pixel - arena.clientWidth / 2);
      arena.scrollTop = Math.max(0, (view.sky_height - y) / view.sky_height * svg.getBoundingClientRect().height - arena.clientHeight / 2); updateMinimap();
    });
  }
}

function traceTankPreview(view, shooter, weapon, angle, power = 100, details = false) {
  // Match the engine's integer coordinates and Python's ties-to-even rounding.
  const roundEven = value => { const floor = Math.floor(value), fraction = value - floor; return fraction === .5 ? floor + (floor % 2 !== 0 ? 1 : 0) : Math.round(value); };
  const direction = shooter.facing === 0 ? 1 : -1;
  let x = shooter.x * 1000 + direction * 850, y = roundEven(shooter.world_y * 1000);
  const theta = angle * Math.PI / 180;
  const speed = Math.floor(roundEven(weapon.speed * 1000) * power / 100);
  let vx = direction * roundEven(speed * Math.cos(theta)), vy = roundEven(speed * Math.sin(theta));
  const target = view.tanks.find(tank => tank.player !== shooter.player);
  const points = [[x / 1000, y / 1000]];
  let kind = "edge";
  for (let step = 0; step < 240; step++) {
    const startX = x, startY = y;
    const steps = Math.max(1, Math.ceil(Math.max(Math.abs(vx), Math.abs(vy)) / 100));
    let collided = false;
    for (let substep = 1; substep <= steps; substep++) {
      x = startX + roundEven(vx * substep / steps);
      y = startY + roundEven(vy * substep / steps);
      const ty = target ? roundEven(target.world_y * 1000) : 0;
      if (target && Math.abs(x - target.x * 1000) <= 1050 && y >= ty - 500 && y <= ty + 1000) { kind = "tank"; collided = true; break; }
      if (x < 0 || x >= view.width * 1000) { collided = true; break; }
      const column = Math.max(0, Math.min(view.width - 1, roundEven(x / 1000)));
      if (y <= view.terrain[column] * 1000) { y = view.terrain[column] * 1000; kind = "terrain"; collided = true; break; }
    }
    points.push([x / 1000, y / 1000]);
    if (collided) break;
    vx += roundEven(view.wind.acceleration * 1000);
    if (direction * vx < 0) vx = 0;
    vy -= roundEven(view.gravity * 1000);
  }
  return details ? {points,kind} : points;
}

function animateTankShot(svg, view, actor, callout, onImpact = () => {}, onPosition = () => {}) {
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
  const duration = reducedMotion ? 0 : Math.min(3200, Math.max(2200, points.length * 42));
  function fly(now) {
    if (!svg.isConnected) return;
    const t = duration ? Math.min(1, (now - started) / duration) : 1;
    const position = t * (points.length - 1);
    const index = Math.min(points.length - 2, Math.floor(position));
    const fraction = position - index;
    const x = points[index][0] + (points[index + 1][0] - points[index][0]) * fraction;
    const y = points[index][1] + (points[index + 1][1] - points[index][1]) * fraction;
    if (!reducedMotion) onPosition(x, y);
    projectile.setAttribute("cx", String(x * 24));
    projectile.setAttribute("cy", String((view.sky_height - y) * 24));
    const trailPoints = points.slice(0, index + 1).map(([px, py]) => `${px * 24},${(view.sky_height - py) * 24}`);
    trailPoints.push(`${x * 24},${(view.sky_height - y) * 24}`);
    flightTrail.setAttribute("points", trailPoints.join(" "));
    if (t < 1) { requestAnimationFrame(fly); return; }
    projectile.remove();
    setTimeout(() => flightTrail.remove(), reducedMotion ? 350 : 800);
    const impact = view.impact;
    onImpact();
    onPosition(impact.x, impact.y);
    view.tanks.forEach((tank, index) => {
      if (!(view.last_damage?.[index] > 0)) return;
      const art = svg.querySelector(`[data-seat="${index}"] .tank-art`);
      if (art) { art.classList.add("damaged"); setTimeout(() => art.classList.remove("damaged"), 900); }
    });
    if (impact.kind === "edge") return;
    const x1 = impact.x * 24, y1 = (view.sky_height - impact.y) * 24;
    const offsets = impact.weapon === 3 ? [-1.2, 0, 1.2] : [0];
    const blasts = offsets.map(offset => svgElement("circle", { cx: x1 + offset * 24, cy: y1 - (offset === 0 || offsets.length === 1 ? 0 : 5),
      r: Math.max(24, view.weapons[impact.weapon].radius * 30), class: `tank-blast${hit ? " hit" : " miss"}` }));
    const radius = Math.max(30, view.weapons[impact.weapon].radius * 30);
    const ring = svgElement("ellipse", { cx: x1, cy: y1, rx: radius, ry: radius * .55, class: "tank-shockwave" });
    const core = svgElement("circle", { cx: x1, cy: y1, r: radius * .55, class: "tank-fire-core" });
    const smoke = Array.from({ length: 9 }, (_, i) => svgElement("circle", {
      cx: x1 + ((i * 17) % 41 - 20), cy: y1 - i * 4, r: radius * (.2 + (i % 3) * .08),
      class: "tank-smoke", style: `--rise:${-60 - i * 12}px;--drift:${(i % 2 ? 1 : -1) * (15 + i * 6)}px;--delay:${i * 35}ms`
    }));
    svg.append(...smoke, ring, ...blasts, core);
    setTimeout(() => { ring.remove(); core.remove(); smoke.forEach(item => item.remove()); }, 2800);
    if (!reducedMotion && impact.kind !== "edge") {
      for (let i = 0; i < 48; i++) {
        const direction = i * Math.PI * 2 / 48;
        const distance = (35 + (i * 19) % 130) * (impact.weapon === 5 ? 1.45 : 1);
        const spark = svgElement("circle", { cx: x1, cy: y1, r: i % 4 === 0 ? 3.1 : 1.8,
          class: i % 3 ? "tank-spark" : "tank-spark tank-debris", style: `--dx:${Math.cos(direction) * distance}px;--dy:${Math.sin(direction) * distance}px;--delay:${(i % 5) * 22}ms` });
        svg.append(spark);
        setTimeout(() => spark.remove(), 1250);
      }
    }
    const label = svgElement("text", { x: x1, y: y1 - 19, class: `tank-hit-label${hit ? " hit" : " miss"}` });
    label.textContent = hit ? `−${totalDamage} HP` : "TERRAIN IMPACT";
    svg.append(label);
    setTimeout(() => { label.remove(); }, 1700);
    setTimeout(() => { blasts.forEach(blast => blast.remove()); }, 1100);
  }
  requestAnimationFrame(fly);
}

function renderGame(session) {
  const view = session.view;
  const host = $("#arena-body"); document.querySelectorAll(".word-drag-ghost").forEach(g=>g.remove()); host.replaceChildren();
  host.classList.toggle("tank-arena-body", view?.kind === "scorched-earth");
  state.islandSelection = view?.kind === "settlement" ? state.islandSelection : null;
  if (!view) {
    const empty = node("div", "welcome-state");
    empty.append(node("div", "welcome-emblem", "◈"));
    empty.append(node("p", "eyebrow", session.status === "invited" ? "A SEAT IS WAITING" : "TABLE OPEN"));
    empty.append(node("h2", "", session.status === "invited" ? "You're invited." : "The table is set."));
    const min=state.snapshot.catalog.find(g=>g.game_id===session.game_id)?.min_players??2;
    empty.append(node("p","",session.players.length+" joined · "+min+" required"));
    for(const player of session.players)empty.append(node("p","", "● "+player+(player===session.coordinator?" · Host":"")+(player===state.snapshot.callsign?" (you)":"")));
    for(const player of session.invited||[])empty.append(node("p","", "○ "+player+" · Invitation pending"));
    empty.append(node("p","",session.status==="invited"?"Accept the invitation above to take your seat.":"Invite stations or wait for players to join, then start the game."));
    host.append(empty); return;
  }
  const block = node("div", "game-block");
  block.append(renderScoreStrip(session, view));
  switch (view.kind) {
    case "signal-siege": renderSignalSiege(session, view, block); break;
    case "hexxagon": renderHexxagon(session, view, block); break;
    case "grid": renderGrid(session, view, block); break;
    case "connect-four": renderConnectFour(session, view, block); break;
    case "battleship": case "sea-warfare": renderBattleship(session, view, block); break;
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

function animateLastOpponentMove(session, {automatic=false}={}){
  const cue=session.last_opponent_cue;
  if(!cue||session.id!==state.selectedId||cue.kind==="scorched-earth")return;
  const key=session.id+":"+cue.revision;
  if(automatic && state.moveCueSeen.get(session.id)===key)return;
  state.moveCueSeen.set(session.id,key);
  const root=$("#arena-body");
  if(!root||!root.isConnected)return;
  root.querySelectorAll(".move-wave,.move-popover").forEach(el=>el.remove());
  const index=cue.indices?.[Math.floor(cue.indices.length/2)];
  const board=cue.board;
  let target;
  if(cue.kind==="nim")target=root.querySelector(`[data-move-pile="${index}"]`);
  else if(["battleship-classic","sea-warfare"].includes(cue.kind))
    target=root.querySelector(`[data-move-board="${board}"] [data-move-cell="${index}"]`);
  else target=root.querySelector(`[data-move-cell="${index}"]`);
  const fallback=root.querySelector(".game-block")||root;
  target=target||fallback;
  target.classList.add("move-cue-target");
  const wave=node("span","move-wave"+(cue.outcome==="hit"||cue.outcome==="sunk"?" move-wave-hit":cue.outcome==="miss"?" move-wave-miss":""));
  wave.setAttribute("aria-hidden","true");
  target.append(wave);
  const label=cue.kind==="battleship-classic"||cue.kind==="sea-warfare"?
    cue.actor+" · "+({"miss":"MISS · white splash","hit":"HIT · red splash","sunk":"SUNK · red splash","incoming":"Incoming shot"}[cue.outcome]||cue.description):
    cue.actor+" · "+cue.description;
  const pop=node("div","move-popover",label);
  pop.setAttribute("role","status");
  fallback.prepend(pop);
  // Prompt stays visible briefly after the board ripple, but never captures input.
  const stop=()=>{wave.remove();pop.remove();target.classList.remove("move-cue-target");};
  if(state.moveCueTimer)clearTimeout(state.moveCueTimer);
  state.moveCueTimer=setTimeout(stop,1800);
}
function syncReplayMoveButton(session){
  const old=$("#replay-move-button");
  const cue=session?.last_opponent_cue;
  if(!cue||session.view?.kind==="scorched-earth"){old?.remove();return;}
  let button=old;
  if(!button){button=node("button","button secondary","↻ Replay last move");button.id="replay-move-button";button.type="button";
    button.addEventListener("click",()=>{if(state.session)animateLastOpponentMove(state.session);});
    $("#turn-banner").after(button);
  }
  button.title="Replay "+cue.actor+"'s latest move without changing the game or transmitting";
}

function renderSession(session, messages) {
  const syncPending = s => Number(s?.sync_requested_at || 0) > Number(s?.sync_at || 0);
  observeCompletion(session);
  state.session = session;
  const signature = JSON.stringify([session.id, session.status, session.revision, session.sequence,
    session.current_player, session.view, session.has_pending_action, session.invited, session.error, session.last_move,
    session.sync_at, session.sync_requested_at, syncPending(session)]);
  $("#arena-title").textContent = session.game;
  const localPractice = state.snapshot?.transport === "practice";
  $("#arena-game-tag").textContent = localPractice ? "LOCAL PRACTICE · NO RADIO" :
    `${session.status.toUpperCase()} · ${session.hosted ? "HOSTED BROADCAST" : "DIRECT TABLE"}`;
  const last = session.last_move ? ` · Last: ${session.last_move.actor} ${session.last_move.description}` : "";
  const subtitle = localPractice ? `PRACTICE · YOU VS COMPUTER${last}` :
    `TABLE ${session.id.slice(-8)} · ${session.players.length} STATIONS${last}`;
  $("#arena-subtitle").textContent = subtitle;
  $("#arena-subtitle").title = subtitle;
  const revision = $("#arena-revision"); revision.hidden = false; revision.textContent = `REV ${session.revision}`;
  setTurnBanner(session);
  syncReplayMoveButton(session);
  renderRoster(session);
  const actionSignature = JSON.stringify([session.id, session.status, session.players, session.invited,
    session.hosted, session.coordinator, session.has_pending_action, state.snapshot.transport,
    state.snapshot.observer_mode, state.snapshot.callsign, state.snapshot.tx_enabled,
    syncPending(session) ? Math.floor(Date.now() / 30_000) : 0, session.error]);
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
  const playing = state.tankPlayback;
  if (playing?.id === session.id && playing.svg.isConnected) return;
  if (gameSignature !== state.sessionSignature) {
    state.sessionSignature = gameSignature;
    renderGame(session);
    if(session.last_opponent_cue && session.view?.kind!=="scorched-earth")
      animateLastOpponentMove(session,{automatic:true});
  }
}

async function refresh() {
  if (state.busy) return;
  state.busy = true;
  try {
    const snapshot = await request("/api/state");
    state.snapshot = snapshot;
    renderActivity(snapshot.notifications || []);
    setConnection(snapshot);
    populateGames(snapshot.catalog || []);
    $("#cq-panel").hidden = snapshot.transport !== "broadcast" || snapshot.observer_mode;
    $("#cq-send").disabled = !canTransmit();
    renderCq(snapshot.cq_requests || []);
    renderDiscovery(snapshot);
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
    $("#new-game").disabled=isObserver();
    if (!snapshot.callsign && !state.setupShown) { state.setupShown=true; $("#welcome-dialog").showModal(); }
    if (snapshot.error) $("#transport-label").textContent = `OFFLINE · ${snapshot.error}`;
    if (state.selectedId && snapshot.ready) {
      const detail = await request(`/api/session/${encodeURIComponent(state.selectedId)}`);
      renderSession(detail.session, detail.chat || []);
      $("#mobile-turn-dot").hidden = !canAct(detail.session);
    } else {
      $("#mobile-turn-dot").hidden = true;
      state.session = null; state.sessionSignature = ""; state.chatSignature = ""; state.islandSelection = null;
      $("#arena-title").textContent = "Game library";
      $("#arena-subtitle").textContent = "Choose a table or start a new match to take your seat.";
      $("#arena-game-tag").textContent = "READY WHEN YOU ARE";
      $("#arena-revision").hidden = true; $("#turn-banner").hidden = true;
      $("#replay-move-button")?.remove();
      const libraryKey=JSON.stringify([snapshot.catalog,snapshot.transport,snapshot.ready,snapshot.tx_enabled,snapshot.observer_mode,snapshot.callsign]);
      if(state.librarySignature!==libraryKey||!$("#arena-body .game-library")){
        state.librarySignature=libraryKey;$("#arena-body").replaceChildren(createWelcome());
      }
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

function openInfo(title){
  $("#info-heading").textContent=title;
  const body=$("#info-content");body.replaceChildren();
  if(!$("#info-dialog").open)$("#info-dialog").showModal();
  return body;
}
async function showGuide(game){
  const body=openInfo("How to play · "+game.name);
  try{
    const guides=await request("/api/guides");
    const steps=guides[game.key]||["Use the available controls and follow the turn indicator."];
    const list=node("ol","game-guide");
    for(const step of steps)list.append(node("li","",step));
    body.append(list);
  }catch(error){body.append(node("p","",error.message));}
}
async function showHistory(){
  const body=openInfo("Match history");
  try{
    const history=await request("/api/history");
    const totals=history.totals;
    body.append(node("p","micro-copy","Local saved results · "+(isPractice()?"Practice":"Radio")+(isObserver()?" · Receive-only":" · "+(state.snapshot.callsign||""))));
    const stats=node("div","history-stats");
    for(const [key,label] of [["played","Played"],["wins","Wins"],["losses","Losses"],["draws","Draws"]]){
      const item=node("div","history-card");item.append(node("strong","",totals[key]),node("span","micro-copy",label));stats.append(item);
    }
    body.append(stats);
    for(const [game,g] of Object.entries(history.by_game))body.append(node("p","",game+" — "+g.wins+"W / "+g.losses+"L / "+g.draws+"D"));
    body.append(node("h3","","Completed matches"));
    if(!history.matches.length)body.append(node("p","micro-copy","Finish a match to see it here."));
    for(const match of history.matches){
      const item=node("div","history-card");
      item.append(node("strong","",match.game+" · "+match.outcome),node("p","micro-copy",match.players.join(" / ")+" · "+(match.updated?new Date(match.updated*1000).toLocaleString():"Unknown date")+" · rev "+match.revision));
      if(match.archived)item.append(node("p","micro-copy","Archived · hidden from active tables"));
      addButton(item,match.archived?"Restore to tables":"View table",async()=>{
        if(match.archived){
          const result=await sendCommand("archive_game",{session_id:match.id,archived:false});
          if(!result)return;
        }
        closeDialog("info-dialog");selectSession(match.id);await refresh();
      });
      body.append(item);
    }
    body.append(node("p","micro-copy","Archived matches stay in your statistics. Deleting a saved match permanently removes its record."));
  }catch(error){body.append(node("p","",error.message));}
}
function drawReplayView(view,host){
  host.replaceChildren();
  const kind=view.kind;
  if(["grid","connect-four","word-tiles","battleship","sea-warfare"].includes(kind)){
    const cells=["battleship","sea-warfare"].includes(kind)?view.shots||[]:view.cells||[];
    const cols=kind==="word-tiles"?15:["battleship","sea-warfare"].includes(kind)?10:view.width||3;
    const board=node("div","replay-grid");
    board.style.setProperty("--replay-cols",cols);
    for(const raw of cells){
      const c=node("div","replay-cell");
      if(["battleship","sea-warfare"].includes(kind)){
        c.textContent=raw===1?"○":raw===2?"×":raw>=3?"S":"";
        if(raw>=1)c.classList.add(raw===1?"miss":raw===2?"hit":"sunk");
      }else{
        c.textContent=String(raw||"").trim();
        if(raw&&String(raw).trim())c.classList.add("occupied");
      }
      board.append(c);
    }
    host.append(board);
  }else if(kind==="piles"){
    const pileRow=node("div","replay-piles");
    for(const n of view.piles||[]){
      const pile=node("div","replay-pile");
      const bar=node("div","replay-pile-bar");bar.style.height=Math.min(260,n*25)+"px";
      pile.append(bar,node("strong","",String(n)));pileRow.append(pile);
    }
    host.append(pileRow);
  }else if(kind==="hexxagon"){
    const field=node("div","replay-hexx");
    for(const cell of view.cells||[]){
      const piece=node("span","replay-hex replay-owner-"+cell.owner);
      piece.style.left=(50+cell.q*6+cell.r*3)+"%";
      piece.style.top=(50+cell.r*7)+"%";
      piece.title="Hex "+cell.id;
      field.append(piece);
    }
    host.append(field);
  }else if(kind==="scorched-earth"){
    const NS="http://www.w3.org/2000/svg",make=(type,attrs={})=>{
      const el=document.createElementNS(NS,type);
      Object.entries(attrs).forEach(([k,v])=>el.setAttribute(k,String(v)));
      return el;
    };
    const field=make("svg",{viewBox:"0 0 600 350",role:"img","aria-label":"Scorched Earth terrain and tanks"});
    const terrain=view.terrain||[],sky=view.sky_height||1;
    if(terrain.length){
      const points=terrain.map((n,i)=>[600*i/Math.max(1,terrain.length-1),350-320*n/sky]);
      field.append(make("polygon",{points:"0,350 "+points.map(([x,y])=>x+","+y).join(" ")+" 600,350",fill:"#346650"}));
      for(const [i,t] of (view.tanks||[]).entries()){
        const x=t.x*600/terrain.length,y=350-320*(t.world_y||0)/sky;
        field.append(make("rect",{x:x-10,y:y-8,width:20,height:15,rx:4,fill:i?"#fbbf24":"#00cfa5"}));
        const label=make("text",{x,y:y-14,"text-anchor":"middle",fill:"#e2e8f0","font-size":12});
        label.textContent=t.hp+" HP";field.append(label);
      }
    }
    host.append(field);
  }else{
    host.append(node("p","", "Board replay for "+String(kind)+" is not available yet."));
  }
}
async function showReplay(session){
  const body=openInfo("Visual replay · "+session.game);
  try{
    const {frames}=await request("/api/replay/"+encodeURIComponent(session.id));
    if(!frames.length){body.append(node("p","micro-copy","No saved board snapshots for this table."));return;}
    const status=node("p","micro-copy"),board=node("div","replay-board");
    const tools=node("div","replay-controls");
    const prev=node("button","button secondary","◀ Previous"),play=node("button","button primary","Play"),next=node("button","button secondary","Next ▶");
    for(const b of [prev,play,next])b.type="button";
    const slider=node("input","replay-slider");slider.type="range";slider.min=0;slider.max=frames.length-1;slider.step=1;slider.value=0;slider.setAttribute("aria-label","Replay frame");
    tools.append(prev,play,next);body.append(status,board,tools,slider);
    body.append(node("p","micro-copy","Public saved board snapshots only. Historical gaps remain explicit; private fleet placements are never revealed."));
    let index=0,timer=null;
    const show=(i)=>{
      index=Math.max(0,Math.min(frames.length-1,i));slider.value=index;
      const frame=frames[index];
      status.textContent="Revision "+frame.revision+" · "+(frame.actor||"Game")+" · "+frame.description+" · "+(index+1)+"/"+frames.length+" snapshots";
      drawReplayView(frame.view,board);
      prev.disabled=index===0;next.disabled=index===frames.length-1;
    };
    const stop=()=>{if(timer!==null){clearInterval(timer);timer=null;}play.textContent="Play";};
    prev.addEventListener("click",()=>{stop();show(index-1);});
    next.addEventListener("click",()=>{stop();show(index+1);});
    slider.addEventListener("input",()=>{stop();show(Number(slider.value));});
    play.addEventListener("click",()=>{
      if(timer!==null){stop();return;}
      if(index===frames.length-1)show(0);
      play.textContent="Pause";
      timer=setInterval(()=>{
        if(index>=frames.length-1){stop();return;}
        show(index+1);
      },900);
    });
    $("#info-dialog").addEventListener("close",stop,{once:true});
    show(0);play.disabled=frames.length<2;
  }catch(error){body.append(node("p","",error.message));}
}
async function showMoveHistory(session){
  const body=openInfo("Move history · "+session.game);
  try{
    const {moves,revision}=await request("/api/moves/"+encodeURIComponent(session.id));
    body.append(node("p","micro-copy","Committed actions only · "+moves.length+" of "+revision+" revisions available"));
    let previous=0;
    for(const move of moves){
      if(move.revision>previous+1)body.append(node("p","micro-copy","Revisions "+(previous+1)+"–"+(move.revision-1)+" not recorded"));
      body.append(node("p","history-card",move.revision+". "+move.actor+" · "+move.description));previous=move.revision;
    }
    if(previous<revision)body.append(node("p","micro-copy","Revisions "+(previous+1)+"–"+revision+" not recorded"));
    if(!moves.length)body.append(node("p","micro-copy","No recorded moves yet."));
  }catch(error){body.append(node("p","",error.message));}
}
$("#history-open").addEventListener("click",showHistory);

function gameLibrary(){
  const container=node("div","game-library");
  container.append(node("h2","","Explore the arcade"),node("p","micro-copy",isPractice()?"Pick a game to play against the computer — no radio needed.":"Pick a game and choose how to invite stations."));
  if(!state.snapshot?.ready){
    addButton(container,"Play offline instead",startPractice,true);
    addButton(container,"Connect my radio station",()=>openSettings("broadcast"));
  }else if(isObserver())container.append(node("p","micro-copy","Receive-only: choose a heard table to observe, or change play mode in Settings."));
  const grid=node("div","game-library-grid");
  const games=(state.snapshot?.catalog||[]).filter(g=>(!g.clients||g.clients.includes("web"))&&(state.snapshot?.experimental_games||g.key!=="signal-siege"));
  for(const game of games){
    const card=node("article","game-library-card"),meta=GAME_PREVIEWS[game.key]||["◈","Open a table for this game."];
    // Use the existing sprite assets when available.
    const art={"battleship-classic":"battleship-hull-horizontal.png","sea-warfare":"battleship-carrier-horizontal.png","scorched-earth":"tank-teal.png"}[game.key];
    const head=node("div","library-card-heading");head.append(node("strong","",meta[0]+"  "+game.name));
    if(art){const image=node("img");image.src="/"+art;image.alt="";head.append(image);}card.append(head);
    card.append(node("p","micro-copy",(game.min_players===game.max_players?game.min_players:game.min_players+"–"+game.max_players)+" players"),node("p","",meta[1]));
    addButton(card,isPractice()?"Play vs computer":"Select game",()=>chooseGame(game),true,!canTransmit()||state.creating);
    addButton(card,"How to play",()=>showGuide(game));
    grid.append(card);
  }
  container.append(grid);return container;
}
function createWelcome(){return gameLibrary();}
function browseGames(){
  $("#library-body").replaceChildren(gameLibrary());$("#library-dialog").showModal();
}
function closeDialog(id){const dialog=$("#"+id);if(dialog.open)dialog.close();}
async function chooseGame(game){
  if(!canTransmit()||state.creating)return;
  closeDialog("library-dialog");$("#game-select").value=game.key;updateGamePreview();
  if(isPractice()){await createTable(game.key,"",false);return;}
  const form=$("#create-form");form.dataset.game=game.key;form.elements.mode.value="direct";
  form.elements.mode.querySelector('[value="hosted"]').disabled=state.snapshot.transport!=="broadcast";
  form.elements.peer.value="";
  $("#create-heading").textContent="New "+game.name+" table";
  $("#create-description").textContent=(GAME_PREVIEWS[game.key]||["",""])[1];
  $("#create-error").hidden=true;updateCreateDialog();$("#create-dialog").showModal();
}
function updateCreateDialog(){
  const form=$("#create-form"),hosted=form.elements.mode.value==="hosted",peer=form.elements.peer.value.trim();
  $("#create-peer-label").hidden=hosted;form.elements.peer.disabled=hosted;
  $("#create-submit").textContent=hosted?"Announce open table":peer?"Create & invite":"Create lobby";
  $("#create-hint").textContent=hosted?"Announce to listening stations. You choose when to start.":peer?"Send an invitation now. The match starts when the roster is full.":"Create a saved lobby and invite stations when ready.";
  $("#create-error").hidden=true;
}
async function createTable(game,peer,hosted){
  if(state.creating||!canTransmit())return;
  state.creating=true;
  try{const result=await sendCommand("create_game",{game,peer,hosted});if(result){closeDialog("create-dialog");state.focusMode=false;applyWorkspace();}}
  finally{state.creating=false;}
}
$("#create-form").addEventListener("submit",async event=>{
  event.preventDefault();const f=event.currentTarget,hosted=f.elements.mode.value==="hosted",peer=hosted?"":f.elements.peer.value.trim().toUpperCase();
  if(peer&&(peer===state.snapshot.callsign||! /^[A-Z0-9/-]{1,15}$/.test(peer))){$("#create-error").textContent="Enter a different, valid station callsign.";$("#create-error").hidden=false;return;}
  $("#create-submit").disabled=true;try{await createTable(f.dataset.game,peer,hosted);}finally{$("#create-submit").disabled=false;}
});
$("#create-form").elements.mode.addEventListener("change",updateCreateDialog);
$("#create-form").elements.peer.addEventListener("input",updateCreateDialog);
$("#new-game").addEventListener("click",browseGames);
document.querySelectorAll("[data-close-dialog]").forEach(b=>b.addEventListener("click",()=>closeDialog(b.dataset.closeDialog)));
async function startPractice(){
  closeDialog("welcome-dialog");closeDialog("library-dialog");
  try{
    const snapshot=await request("/api/settings",{method:"POST",body:JSON.stringify({transport:"practice",observer_mode:false,callsign:state.snapshot?.callsign||"PLAYER"})});
    state.snapshot=snapshot;selectSession(null);state.selectedInitialized=false;state.librarySignature="";
    await refresh();
  }catch(error){notify(error.message);}
}
document.querySelectorAll("[data-onboard]").forEach(b=>b.addEventListener("click",()=>{
  closeDialog("welcome-dialog");if(b.dataset.onboard==="practice")startPractice();else openSettings(b.dataset.onboard);
}));

async function sendCommand(command, extra = {}) {
  const move=["legal_action","place_word","exchange_word","place_battleship_fleet"].includes(command)&&extra.session_id;
  if(move&&state.submitting.has(extra.session_id))return null;
  if(move){state.submitting.add(extra.session_id);if(state.session?.id===extra.session_id)setTurnBanner(state.session);}
  try {
    const result = await request("/api/command", { method: "POST", body: JSON.stringify({ command, ...extra }) });
    if ((command === "create_game" || command === "answer_cq") && result.session_id) {
      selectSession(result.session_id);
      switchPane("arena");
    }
    if (command === "delete_game" || command === "archive_game" && extra.archived !== false) {
      selectSession(null);
    }
    await refresh();
    return result;
  } catch (error) { notify(error.message); return null; }
  finally{if(move){state.submitting.delete(extra.session_id);if(state.session?.id===extra.session_id)setTurnBanner(state.session);}}
}
function submitAction(bytes) {
  if (!state.session) return;
  const action = bytes.map(value => Number(value).toString(16).padStart(2, "0")).join("");
  sendCommand("legal_action", { session_id: state.session.id, action });
}

function openSettings(mode) {
  const form = $("#settings-form");
  if (state.snapshot) {
    for (const key of ["callsign", "host", "transport", "group", "broadcast_mode", "station_id_mode", "cw_wpm", "command_port", "kiss_port", "color_scheme", "ai_difficulty", "dx_cluster_url"]) {
      if (form.elements[key]) form.elements[key].value = state.snapshot[key] ?? "";
    }
    form.elements.observer_mode.checked = Boolean(state.snapshot.observer_mode);
    form.elements.sound_enabled.checked = Boolean(state.snapshot.sound_enabled);
    form.elements.experimental_games.checked = Boolean(state.snapshot.experimental_games);
  }
  if(typeof mode==="string"){
    form.elements.transport.value=mode==="observer"?"broadcast":mode;
    form.elements.observer_mode.checked=mode==="observer";
  }
  $("#advanced-radio").setAttribute("aria-expanded","false");
  updateSettingsMode();
  $("#settings-error").hidden = true;
  $("#settings-dialog").showModal();
}

function updateSettingsMode(){
  const f=$("#settings-form"),practice=f.elements.transport.value==="practice",broadcast=f.elements.transport.value==="broadcast";
  const radio=!practice,advanced=$("#advanced-radio").getAttribute("aria-expanded")==="true"&&radio;
  const show=(name,visible)=>{f.elements[name].closest("label").hidden=!visible;};
  show("host",radio);show("observer_mode",broadcast);show("ai_difficulty",practice);
  for(const name of ["command_port","station_id_mode","cw_wpm","dx_cluster_url"])show(name,advanced);
  for(const name of ["group","broadcast_mode","kiss_port"])show(name,advanced&&broadcast);
  $("#advanced-radio").hidden=!radio;$("#advanced-radio").textContent=advanced?"Advanced radio settings ▾":"Advanced radio settings ▸";
  if(!broadcast)f.elements.observer_mode.checked=false;
  f.elements.callsign.required=radio;f.elements.callsign.placeholder=practice?"PLAYER (optional)":"N0CALL";
}
$("#settings-form").elements.transport.addEventListener("change",updateSettingsMode);
$("#advanced-radio").addEventListener("click",()=>{$("#advanced-radio").setAttribute("aria-expanded",String($("#advanced-radio").getAttribute("aria-expanded")!=="true"));updateSettingsMode();});
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
  data.cw_wpm = Number(data.cw_wpm);
  data.observer_mode = form.elements.observer_mode.checked;
  data.sound_enabled = form.elements.sound_enabled.checked;
  data.experimental_games = form.elements.experimental_games.checked;
  try {
    const snapshot = await request("/api/settings", { method: "POST", body: JSON.stringify(data) });
    if(snapshot.transport!==state.snapshot?.transport||snapshot.callsign!==state.snapshot?.callsign){selectSession(null);state.selectedInitialized=false;}
    state.snapshot = snapshot; state.sessionSignature = ""; state.chatSignature = "";state.librarySignature="";
    $("#settings-dialog").close();
    if (snapshot.error) notify(`Settings saved, but the station could not connect: ${snapshot.error}`);
    if (snapshot.identification_warning) notify(snapshot.identification_warning);
    await refresh();
  } catch (error) { const field = $("#settings-error"); field.textContent = error.message; field.hidden = false; }
});

$("#create-game").addEventListener("click", () => {
  if (!state.snapshot?.ready) { openSettings(); return; }
  createTable($("#game-select").value,isPractice()?"":$("#peer-input").value.trim(),!isPractice()&&$("#table-mode").value==="hosted");
});
$("#cq-send").addEventListener("click", () => sendCommand("call_cq", { game: $("#cq-game").value }));
$("#discovery-save").addEventListener("click",async()=>{
  const result=await sendCommand("configure_discovery",{
    auto_reply:$("#discovery-auto-reply").checked,
    status:$("#discovery-status").value,
    games:[...document.querySelectorAll("#discovery-games input:checked")].map(input=>input.value),
    legacy_cq:$("#discovery-legacy-cq").checked,
  });
  if(result){
    $("#discovery-prefs").open=false;
    notify("Discovery preferences saved");
  }
});
$("#discovery-nearby").addEventListener("click",event=>{
  const button=event.target.closest("[data-discovery-call]");
  if(!button||button.disabled)return;
  const game=$("#cq-game").value;
  if(!game){notify("Choose a game before inviting a station");return;}
  createTable(game,button.dataset.discoveryCall,false);
});
$("#cq-game").addEventListener("change",()=>{if(state.snapshot)renderDiscovery(state.snapshot);});
$("#cq-list").addEventListener("click", event => {
  const button = event.target.closest("[data-cq-call]");
  if (!button) return;
  const requested = Number(button.dataset.cqGame);
  const game = requested === 255 ? $("#cq-game").value :
    state.snapshot.catalog.find(entry => entry.game_id === requested)?.key;
  if (!game) { notify("Choose a game above to answer this CQ."); return; }
  sendCommand("answer_cq", { callsign: button.dataset.cqCall, game });
});
$("#table-mode").addEventListener("change", updateTableSetup);
$("#game-select").addEventListener("change", updateGamePreview);
$("#table-search").addEventListener("input", () => { state.listSignature = ""; if (state.snapshot) renderSessionList(state.snapshot.sessions || []); });
$("#table-filter").addEventListener("change", () => { state.listSignature = ""; if (state.snapshot) renderSessionList(state.snapshot.sessions || []); });
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
  selectSession(button.dataset.session);
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
