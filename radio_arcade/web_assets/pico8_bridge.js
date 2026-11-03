/* PICO-8 HTML GPIO <-> local Radio Arcade API. No third-party dependencies. */
(() => {
  "use strict";
  const root = typeof window === "undefined" ? globalThis : window;
  const TX = 0, RX = 64, CHUNK = 60, SNAP_CHUNK = 90, MAX_SNAPSHOT = 16384;
  const bytes = data => Array.from(data, x => x & 255);
  const hex = data => bytes(data).map(x => x.toString(16).padStart(2, "0")).join("");
  const fromHex = str => (str.match(/../g) || []).map(x => parseInt(x, 16));
  const fnv = values => values.reduce((h, c) => Math.imul(h ^ c, 16777619) >>> 0, 2166136261);
  const ascii = str => Array.from(str, c => c.charCodeAt(0) & 255);
  const errText = message => ascii(String(message).slice(0, CHUNK));

  class Pico8Bridge {
    constructor({ sessionId, api = fetch.bind(root), gpio = null, intervalMs = 300 } = {}) {
      if (!sessionId || !/^[0-9a-f]{16}$/i.test(sessionId)) throw new Error("PICO-8 needs a 16-digit session ID");
      this.sessionId = sessionId;
      this.api = api;
      this.gpio = gpio;
      this.intervalMs = intervalMs;
      this.out = [];
      this.actions = [];
      this.inflight = false;
      this.refreshing = false;
      this.lastRevision = 0;
      this.opaqueCursor = 0;
      this.rxSequence = 0;
      this.actionBuffer = [];
      this.snapshotBuffer = [];
      this.snapshots = new Map();
      this.saveRequests = [];
      this.activeSave = null;
      this.hello = null;
      this.statusCode = 0;
      this.session = null;
      this.callsign = null;
      this.error = "";
      this.recoveryRequested = false;
    }

    async request(path, data) {
      const response = await this.api(path, data === undefined ? undefined : {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data)
      });
      const result = await response.json();
      if (!response.ok) throw Error(result.error || `HTTP ${response.status}`);
      return result;
    }

    report(error) {
      this.error = String(error?.message || error);
      this.out.push([22, errText(this.error)]);
    }

    queue(kind, payload = []) {
      const b = bytes(payload);
      if (b.length <= CHUNK) this.out.push([kind, b]);
      else throw Error("GPIO frame too large");
    }

    queueBlob(fragmentKind, endKind, data) {
      for (let i = 0; i < data.length; i += CHUNK)
        this.queue(i + CHUNK >= data.length ? endKind : fragmentKind, data.slice(i, i + CHUNK));
      if (!data.length) this.queue(endKind);
    }

    tick() {
      const g = this.gpio || root.pico8_gpio;
      if (!g || g.length < 128) return;
      if ((g[TX] || 0) !== (g[TX + 1] || 0)) {
        const kind = g[TX + 2] || 0;
        const n = Math.min(g[TX + 3] || 0, CHUNK);
        const payload = Array.from({ length: n }, (_, i) => g[TX + 4 + i] || 0);
        g[TX + 1] = g[TX];
        this.handleCart(kind, payload);
      }
      if (this.out.length && (g[RX] || 0) === (g[RX + 1] || 0)) {
        const [kind, data] = this.out.shift();
        g[RX + 2] = kind;
        g[RX + 3] = data.length;
        data.forEach((byte, i) => { g[RX + 4 + i] = byte; });
        this.rxSequence = (this.rxSequence + 1) & 255;
        g[RX] = this.rxSequence;
      }
    }

    handleCart(kind, raw) {
      if (kind === 1) {
        const separator = raw.indexOf(0);
        if (separator < 1 || separator + 3 >= raw.length) return this.report("Invalid cartridge hello");
        const id = String.fromCharCode(...raw.slice(0, separator));
        const version = raw[separator + 1] + 256 * raw[separator + 2];
        const mode = raw[separator + 3];
        if (!version || mode > 1) return this.report("Unsupported cartridge version/mode");
        this.hello = { id, version, mode, hash: fnv(ascii(id)) };
        this.refresh().catch(e => this.report(e));
      } else if (kind === 2 || kind === 3) {
        this.actionBuffer.push(...raw);
        if (kind === 3) {
          if (this.actionBuffer.length > 189) this.report("Action exceeds 189 bytes");
          else this.actions.push([1, ...this.actionBuffer]);
          this.actionBuffer = [];
          this.pumpActions().catch(e => this.report(e));
        }
      } else if (kind === 4 || kind === 5) {
        this.snapshotBuffer.push(...raw);
        if (this.snapshotBuffer.length > MAX_SNAPSHOT) {
          this.snapshotBuffer = [];
          this.report("Snapshot exceeds 16 KiB");
        } else if (kind === 5) {
          const data = this.snapshotBuffer;
          this.snapshotBuffer = [];
          const request = this.activeSave;
          this.activeSave = null;
          if (request) {
            this.sendingSnapshot = this.sendSnapshot(request, data)
              .catch(e => this.report(e))
              .finally(() => { this.sendingSnapshot = null; this.askNextSave(); });
          } else this.askNextSave();
        }
      } else if (kind === 6) {
        this.requestSnapshot(true).catch(e => this.report(e));
      } else if (kind === 7) {
        this.actions.push([2]);
        this.pumpActions().catch(e => this.report(e));
      } else if (kind === 8) {
        this.activeSave = null;
        this.report(`Cartridge checkpoint unavailable: ${String.fromCharCode(...raw)}`);
        this.askNextSave();
      }
    }

    status() {
      if (!this.session) return;
      const s = this.session;
      const view = s.view || {};
      const me = s.players.indexOf(this.practice ? s.current_player : this.callsign);
      const current = s.players.indexOf(s.current_player);
      let code = s.status === "finished" ? 3 : s.status === "active" ? 2 : 1;
      if (!this.hello || !view.cart_hash) code = 1;
      if (this.hello && view.cart_hash &&
          (view.cart_hash !== this.hello.hash || view.cart_version !== this.hello.version ||
           view.mode !== (this.hello.mode ? "event" : "turn"))) {
        code = 4;
        if (this.statusCode !== code) this.report("Different cartridge or version at this table");
      }
      const status = [code, me < 0 ? 255 : me, current < 0 ? 255 : current,
        this.hello?.mode || 0, s.revision & 255, (s.revision >>> 8) & 255];
      const key = status.join(",");
      if (key !== this.lastStatus) {
        this.queue(16, status);
        this.lastStatus = key;
      }
      this.statusCode = code;
    }

    async refresh() {
      if (this.refreshing) return;
      this.refreshing = true;
      try {
        const qs = new URLSearchParams({ session_id: this.sessionId, after: String(this.lastRevision) });
        const result = await this.request(`/api/pico8/events?${qs}`);
        this.session = result.session;
        if (this.inflight && this.session.error && !this.session.has_pending_action) {
          this.inflight = false;
          this.report(`Cartridge action rejected: ${this.session.error}`);
        }
        if (!this.callsign) {
          const names = await this.request("/api/pico8/sessions");
          this.callsign = names.callsign;
          this.practice = names.transport === "practice";
        }
        const view = this.session.view || {};
        if (!this.hello || (view.cart_hash &&
            (view.cart_hash !== this.hello.hash || view.cart_version !== this.hello.version ||
             view.mode !== (this.hello.mode ? "event" : "turn")))) {
          this.status();
          return; // Never feed actions to an unknown or mismatched cartridge.
        }
        for (const event of result.events) {
          if (event.revision !== this.lastRevision + 1) {
            await this.requestSnapshot();
            break;
          }
          this.lastRevision = event.revision;
          const action = fromHex(event.hex);
          if (action[0] === 1) {
            const actor = this.session.players.indexOf(event.actor);
            if (actor >= 0) this.queueBlob(17, 18,
              [actor, event.revision & 255, (event.revision >>> 8) & 255, ...action.slice(1)]);
          }
          if (this.inflight && (this.practice || event.actor === this.callsign)) this.inflight = false;
        }
        if (this.lastRevision < result.revision && result.events.length === 0)
          await this.requestSnapshot();
        if (this.hello && this.session.coordinator === this.callsign &&
            this.session.status === "active" && !this.session.view.cart_hash && !this.configPending) {
          this.configPending = true;
          const h = this.hello;
          this.actions.unshift([0, (h.hash >>> 24) & 255, (h.hash >>> 16) & 255,
            (h.hash >>> 8) & 255, h.hash & 255, (h.version >>> 8) & 255,
            h.version & 255, h.mode]);
        }
        await this.pollOpaque();
        this.status();
        await this.pumpActions();
      } finally {
        this.refreshing = false;
      }
    }

    async pumpActions() {
      if (this.inflight || !this.actions.length || !this.session || this.session.status !== "active") return;
      const pending = this.actions[0];
      const view = this.session.view || {};
      if (!this.hello || (view.cart_hash &&
          (view.cart_hash !== this.hello.hash || view.cart_version !== this.hello.version ||
           view.mode !== (this.hello.mode ? "event" : "turn")))) return;
      if (!this.practice && !this.session.players.includes(this.callsign)) return;
      if (pending[0] !== 0 && !view.cart_hash) return;
      if (pending[0] === 2 && this.session.coordinator !== this.callsign) {
        this.actions.shift();
        this.report("Only the dealer can finish this cartridge table");
        return;
      }
      if (pending[0] === 1 && !this.practice && view.mode !== "event" &&
          this.session.current_player !== this.callsign) return;
      if (this.session.has_pending_action) return;
      this.inflight = true;
      this.sentRevision = this.session.revision;
      try {
        await this.request("/api/pico8/action", { session_id: this.sessionId, hex: hex(pending) });
        this.actions.shift();
      } catch (e) {
        this.inflight = false;
        if (!/waiting for the previous|not your turn|TX is disabled/i.test(String(e)))
          this.report(e);
      }
    }

    async pollOpaque() {
      const qs = new URLSearchParams({ session_id: this.sessionId, after: String(this.opaqueCursor) });
      const result = await this.request(`/api/pico8/opaque?${qs}`);
      for (const msg of result.messages) {
        this.opaqueCursor = Math.max(this.opaqueCursor, msg.id);
        if (msg.sender === this.callsign) continue;
        const p = msg.body.split(":");
        if (p[0] !== "P8") continue;
        if (p[1] === "REQ" && this.session.coordinator === this.callsign) {
          this.saveRequests.push({ target: msg.sender, nonce: p[2], revision: this.session.revision });
          this.askNextSave();
        } else if (p[1] === "S" && p.length === 8) {
          this.acceptSnapshot(p, msg.sender);
        }
      }
    }

    askNextSave() {
      if (!this.activeSave && !this.sendingSnapshot && this.saveRequests.length) {
        this.activeSave = this.saveRequests.shift();
        this.queue(19);
      }
    }

    async requestSnapshot(force = false) {
      if ((!force && this.recoveryRequested) || !this.session || !this.callsign) return;
      if (this.session.coordinator === this.callsign) return;
      this.recoveryRequested = true;
      const nonce = Math.random().toString(16).slice(2, 10);
      this.expectedSnapshot = nonce;
      try {
        await this.request("/api/pico8/opaque", { session_id: this.sessionId,
          target: this.session.coordinator, message: `P8:REQ:${nonce}` });
      } catch (e) {
        this.recoveryRequested = false;
        this.report(e);
      }
    }

    async waitForAck(sequence) {
      const until = Date.now() + 120000;
      while (Date.now() < until) {
        const qs = new URLSearchParams({ session_id: this.sessionId, sequence: String(sequence) });
        const result = await this.request(`/api/pico8/delivery?${qs}`);
        if (result.state === "delivered") return;
        await new Promise(resolve => root.setTimeout(resolve, 400));
      }
      throw Error("Snapshot chunk was not acknowledged within 120 seconds");
    }

    async sendSnapshot(request, data) {
      const checksum = fnv(data).toString(16);
      const chunks = [];
      for (let i = 0; i < data.length; i += SNAP_CHUNK)
        chunks.push(data.slice(i, i + SNAP_CHUNK));
      if (!chunks.length) chunks.push([]);
      for (let i = 0; i < chunks.length; i++) {
        const encoded = root.btoa(String.fromCharCode(...chunks[i]));
        const message = `P8:S:${request.nonce}:${request.revision}:${i}:${chunks.length}:${checksum}:${encoded}`;
        if (message.length > 200) throw Error("Snapshot chunk exceeds radio payload");
        const sent = await this.request("/api/pico8/opaque", { session_id: this.sessionId,
          target: request.target, message });
        if (!this.practice) await this.waitForAck(sent.sequence);
      }
    }

    acceptSnapshot(p, sender) {
      const [, , nonce, revisionText, indexText, countText, checksum, content] = p;
      if (nonce !== this.expectedSnapshot || sender !== this.session.coordinator) return;
      const revision = Number(revisionText), index = Number(indexText), count = Number(countText);
      if (!Number.isInteger(revision) || !Number.isInteger(index) || !Number.isInteger(count) ||
          count < 1 || count > 184 || index < 0 || index >= count) return;
      const key = `${nonce}:${revision}:${checksum}`;
      if (!this.snapshots.has(key)) this.snapshots.set(key, new Array(count));
      const parts = this.snapshots.get(key);
      if (parts.length !== count) return;
      parts[index] = bytes(ascii(root.atob(content)));
      if (!parts.every(Boolean)) return;
      const data = parts.flat();
      if (data.length > MAX_SNAPSHOT || fnv(data).toString(16) !== checksum) {
        this.report("Snapshot integrity check failed");
        return;
      }
      this.snapshots.clear();
      this.out = this.out.filter(([type]) => type !== 17 && type !== 18);
      this.queueBlob(20, 21, data);
      this.lastRevision = revision;
      this.inflight = false;
      this.recoveryRequested = false;
    }

    start() {
      if (this.timer) return this;
      this.timer = root.setInterval(() => this.tick(), 16);
      this.networkTimer = root.setInterval(() => this.refresh().catch(e => this.report(e)), this.intervalMs);
      this.refresh().catch(e => this.report(e));
      return this;
    }

    stop() {
      root.clearInterval(this.timer);
      root.clearInterval(this.networkTimer);
      this.timer = this.networkTimer = null;
    }
  }

  root.RadioArcadePico8Bridge = Pico8Bridge;
  if (root.document && root.location) {
    const sessionId = new URLSearchParams(root.location.search).get("session");
    if (sessionId) {
      try { root.pico8_radio_bridge = new Pico8Bridge({ sessionId }).start(); }
      catch (e) { root.console?.error("PICO-8 bridge:", e); }
    }
  }
})();
