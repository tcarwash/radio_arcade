(() => {
  "use strict";
  const el = id => document.getElementById(id);
  let self = "";
  async function api(path, body) {
    const response = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) } : undefined);
    const data = await response.json();
    if (!response.ok) throw Error(data.error || `HTTP ${response.status}`);
    return data;
  }
  function button(name, fn) {
    const b = document.createElement("button");
    b.textContent = name;
    b.onclick = () => Promise.resolve(fn()).then(refresh).catch(showError);
    return b;
  }
  function showError(error) { el("error").textContent = String(error.message || error); }
  function openCart(sid) {
    const cart = el("cart").value;
    if (!cart) throw Error("Select an exported cartridge first");
    window.location.href = `/pico8/carts/${encodeURIComponent(cart)}?session=${encodeURIComponent(sid)}`;
  }
  async function refresh() {
    const [data, carts] = await Promise.all([api("/api/pico8/sessions"), api("/api/pico8/carts")]);
    self = data.callsign;
    el("status").textContent = `Station ${self} · ${data.sessions.length} table(s)`;
    const cartEl = el("cart"), previous = cartEl.value;
    cartEl.replaceChildren();
    for (const cart of carts.carts) {
      const option = document.createElement("option");
      option.value = option.textContent = cart;
      cartEl.append(option);
    }
    if (carts.carts.includes(previous)) cartEl.value = previous;
    const host = el("games");
    host.replaceChildren();
    if (!data.sessions.length) host.textContent = "No sessions yet.";
    for (const s of data.sessions) {
      const div = document.createElement("div");
      div.className = "game";
      const title = document.createElement("strong");
      title.textContent = `${s.id} · ${s.status}`;
      const info = document.createElement("p");
      info.textContent = `Dealer: ${s.coordinator} · Players: ${s.players.join(", ") || "none"} · ${s.hosted ? "Hosted" : "Directed"}`;
      div.append(title, info);
      const row = document.createElement("div");
      row.className = "row";
      if (s.status === "active" || s.status === "finished") row.append(button("Open cartridge", () => openCart(s.id)));
      if (s.status === "invited" || (s.hosted && s.coordinator !== self && ["lobby", "joining"].includes(s.status)))
        row.append(button(s.hosted ? "Join" : "Accept", () => api("/api/command", {
          command: s.hosted ? "join_game" : "accept_invite", session_id: s.id })));
      if (s.status === "lobby" && s.coordinator === self)
        row.append(button("Start", () => api("/api/command", { command: "start_game", session_id: s.id })));
      div.append(row); host.append(div);
    }
  }
  el("create").onclick = async () => {
    try {
      el("error").textContent = "";
      const peer = el("peer").value.trim().toUpperCase();
      await api("/api/pico8/create", { hosted: el("hosted").checked, peer: peer || undefined });
      await refresh();
    } catch (e) { showError(e); }
  };
  refresh().catch(showError);
  setInterval(() => refresh().catch(showError), 3000);
})();
