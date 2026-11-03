-- Radio Arcade's native LÖVE 11.x adapter. No HTTP calls block love.update().
-- Copy the entire radio_arcade directory into your LÖVE project's root.
local bit = require('bit') -- LuaJIT, supplied by LÖVE
local Arcade = {}
Arcade.__index = Arcade
local serial = 0
local CHUNK, MAX_SNAPSHOT = 90, 16384

local function fnv1a(text)
  local n = 2166136261
  for i = 1, #text do
    n = bit.bxor(n, text:byte(i))
    -- Multiplication by the FNV prime using exact 32-bit shifts.
    n = bit.tobit(n + bit.lshift(n, 1) + bit.lshift(n, 4) +
      bit.lshift(n, 7) + bit.lshift(n, 8) + bit.lshift(n, 24))
  end
  if n < 0 then n = n + 4294967296 end
  return n
end

local function to_hex(bytes)
  return (bytes:gsub('.', function(c) return string.format('%02x', c:byte()) end))
end

local function from_hex(encoded)
  assert(#encoded % 2 == 0, 'invalid event hex')
  return (encoded:gsub('..', function(h) return string.char(tonumber(h, 16)) end))
end

local function config_frame(self)
  local n, v = self.hash, self.version
  return string.char(0, math.floor(n / 16777216) % 256,
    math.floor(n / 65536) % 256, math.floor(n / 256) % 256, n % 256,
    math.floor(v / 256), v % 256, self.mode == 'event' and 1 or 0)
end

local function snapshot_chunks(nonce, revision, data)
  local chunks = {}
  local count = math.max(1, math.ceil(#data / CHUNK))
  local hash = fnv1a(data)
  for i = 1, count do
    local payload = data:sub((i - 1) * CHUNK + 1, i * CHUNK)
    local encoded = love.data.encode('string', 'base64', payload)
    local msg = ('RA:S:%s:%d:%d:%d:%.0f:%s'):format(
      nonce, revision, i - 1, count, hash, encoded)
    assert(#msg <= 200, 'snapshot chunk exceeds RF limit')
    chunks[i] = msg
  end
  return chunks
end

function Arcade.new(options)
  options = options or {}
  local id = assert(options.game_id, 'game_id is required')
  local version = options.version or 1
  local mode = options.mode or 'turn'
  assert(type(id) == 'string' and #id > 0 and #id <= 48 and
    not id:find('[^!-~]'), 'game_id must be printable ASCII, 1-48 bytes')
  assert(type(version) == 'number' and version % 1 == 0 and
    version >= 1 and version <= 32767, 'version must be 1..32767')
  assert(mode == 'turn' or mode == 'event', 'mode must be turn or event')
  if options.session_id then
    assert(options.session_id:match('^%x%x%x%x%x%x%x%x%x%x%x%x%x%x%x%x$'),
      'session_id must be 16 hex digits')
  end
  serial = serial + 1
  local key = 'radio-arcade:' .. tostring(love.timer.getTime()) .. ':' .. serial
  local self = setmetatable({
    id=id, version=version, mode=mode, hash=fnv1a(id),
    session_id=options.session_id, url=options.url or 'http://127.0.0.1:8765',
    interval=options.poll_interval or 0.3, elapsed=0, opaque_elapsed=0,
    requests=love.thread.getChannel(key .. ':request'),
    responses=love.thread.getChannel(key .. ':response'),
    _events={}, _side={}, _actions={}, _commands={}, _estimates={},
    _busy=false, _revision=0, _opaque_cursor=0, _needs_identity=true,
    _session=nil, _callsign=nil, _transport=nil, _error=nil,
    _config_queued=false, _waiting_commit=false, _closed=false,
    _retry_cooldown=0,
    _snapshot_requests={}, _snapshot_out=nil, _snapshot_ack_timer=0,
    _recovery=nil, _checkpoint_status='idle',
  }, Arcade)
  self.thread = love.thread.newThread('radio_arcade/worker.lua')
  self.thread:start(key, self.url:gsub('/+$', ''))
  return self
end

function Arcade:_request(op, method, path, body)
  self._busy = true
  self.requests:push({op=op, method=method, path=path, body=body})
end

function Arcade:create(peer, hosted)
  table.insert(self._commands, {op='create', method='POST', path='/api/engine/create',
    body={peer=peer, hosted=hosted or false}})
end

function Arcade:control(command, peer)
  assert(self.session_id, 'connect or create a session first')
  table.insert(self._commands, {op='control', method='POST', path='/api/engine/control',
    body={session_id=self.session_id, command=command, peer=peer}})
end

function Arcade:send(payload)
  assert(type(payload) == 'string' and #payload <= 189,
    'action must be a binary string of 0..189 bytes')
  table.insert(self._actions, string.char(1) .. payload)
end

function Arcade:finish()
  table.insert(self._actions, string.char(2))
end

function Arcade:request_sync() self:control('request_sync') end

-- Serialization belongs to the game; return/apply complete binary state.
function Arcade:snapshot(save_fn, load_fn)
  assert(type(save_fn) == 'function' and type(load_fn) == 'function',
    'snapshot requires binary save/load callbacks')
  self._save, self._load = save_fn, load_fn
end

function Arcade:checkpoint_status() return self._checkpoint_status end

function Arcade:request_checkpoint()
  if self._recovery or not self._session or not self._callsign or
     self._session.coordinator == self._callsign then return false end
  if not self._load then
    self._error = 'Register snapshot save/load callbacks before recovery'
    return false
  end
  local nonce = ('%04x%04x'):format(love.math.random(0, 65535), love.math.random(0, 65535))
  self._recovery = {nonce=nonce, parts={}, key=nil, started=love.timer.getTime()}
  self._checkpoint_status = 'requested'
  self:request_sync() -- Repair Radio Arcade's control state as well as game state.
  self:send_side(self._session.coordinator, 'RA:REQ:' .. nonce)
  return true
end

-- Side messages are public radio traffic, at most 200 UTF-8 bytes with the
-- RA: prefix. Apps may implement their own compressed checkpoint format.
function Arcade:send_side(target, message)
  assert(self.session_id, 'connect or create a session first')
  assert(type(message) == 'string' and message:sub(1, 3) == 'RA:' and
    #message <= 200, 'side message needs RA: prefix and <=200 UTF-8 bytes')
  table.insert(self._commands, {op='side', method='POST', path='/api/engine/opaque',
    body={session_id=self.session_id, target=target, message=message}})
end

function Arcade:estimate(payload_bytes, options)
  options = options or {}
  local body = {session_id=self.session_id, payload_bytes=payload_bytes}
  for _, name in ipairs({'baud', 'bits_per_symbol', 'code_rate', 'rate_bps',
                         'modem_overhead_bytes', 'preamble_ms', 'transmissions',
                         'destination'}) do
    if options[name] ~= nil then body[name] = options[name] end
  end
  table.insert(self._commands, {op='estimate', method='POST',
    path='/api/engine/estimate', body=body})
end

function Arcade:receive() return table.remove(self._events, 1) end
function Arcade:receive_side() return table.remove(self._side, 1) end
function Arcade:receive_estimate() return table.remove(self._estimates, 1) end
function Arcade:session() return self._session end
function Arcade:revision() return self._revision end
function Arcade:error() return self._error end
function Arcade:connected()
  return self._session ~= nil and self._session.status == 'active' and
    self._session.view and self._session.view.cart_hash == self.hash and
    self._session.view.cart_version == self.version and
    self._session.view.mode == self.mode
end
function Arcade:my_turn()
  if not self:connected() or self._recovery then return false end
  if self._transport == 'practice' then return true end
  local s = self._session
  local seated = false
  for _, player in ipairs(s.players) do
    if player == self._callsign then seated = true end
  end
  return seated and (self.mode == 'event' or s.current_player == self._callsign)
end

function Arcade:_accept_checkpoint(sender, body)
  if not self._recovery or not self._load or
     sender ~= self._session.coordinator then return end
  local nonce, rev, idx, count, sum, encoded = body:match(
    '^RA:S:([0-9a-f]+):(%d+):(%d+):(%d+):(%d+):(.*)$')
  if not nonce or nonce ~= self._recovery.nonce then return end
  rev, idx, count, sum = tonumber(rev), tonumber(idx), tonumber(count), tonumber(sum)
  if rev > 65535 or count < 1 or count > 183 or idx >= count then return end
  local ok, data = pcall(love.data.decode, 'string', 'base64', encoded)
  if not ok or #data > CHUNK or
     love.data.encode('string', 'base64', data) ~= encoded then return end
  local key = rev .. ':' .. count .. ':' .. sum
  if self._recovery.key and self._recovery.key ~= key then return end
  self._recovery.key = key
  local parts = self._recovery.parts
  if parts[idx + 1] and parts[idx + 1] ~= data then return end
  parts[idx + 1] = data
  local total, complete = 0, true
  for i = 1, count do
    if not parts[i] then complete = false; break end
    total = total + #parts[i]
  end
  if not complete or total > MAX_SNAPSHOT then return end
  local joined = table.concat(parts, '', 1, count)
  if fnv1a(joined) ~= sum then self._error = 'Checkpoint checksum mismatch'; return end
  local loaded, err = pcall(self._load, joined, rev)
  if not loaded then self._error = 'Checkpoint load failed: ' .. tostring(err); return end
  self._events = {} -- Older queued actions must not apply to restored state.
  self._revision = rev
  self._waiting_commit = false
  self._recovery = nil
  self._checkpoint_status = 'restored'
  self._error = nil
end

function Arcade:_handle_side(item)
  if item.sender == self._callsign then return end
  local nonce = item.body:match('^RA:REQ:([0-9a-f]+)$')
  if nonce and #nonce == 8 and self._session and
     self._session.coordinator == self._callsign and self._save and
     (not item.created or os.time() - item.created <= 300) then
    table.insert(self._snapshot_requests, {nonce=nonce, target=item.sender})
  elseif item.body:sub(1, 5) == 'RA:S:' then
    self:_accept_checkpoint(item.sender, item.body)
  else
    table.insert(self._side, item)
  end
end

function Arcade:_prepare_checkpoint()
  if self._snapshot_out or not self._save or #self._events > 0 or
     #self._snapshot_requests == 0 then return end
  local request = table.remove(self._snapshot_requests, 1)
  local ok, data = pcall(self._save)
  if not ok or type(data) ~= 'string' or #data > MAX_SNAPSHOT then
    self._error = 'Snapshot save callback failed or exceeded 16 KiB'
    return
  end
  self._snapshot_out = {target=request.target,
    chunks=snapshot_chunks(request.nonce, self._revision, data),
    index=1, sent=false, sequence=nil, elapsed=0}
  self._checkpoint_status = 'sending'
end

function Arcade:_advance_checkpoint()
  local out = self._snapshot_out
  if not out then return end
  out.index = out.index + 1
  out.sequence, out.sent, out.elapsed = nil, false, 0
  if out.index > #out.chunks then
    self._snapshot_out = nil
    self._checkpoint_status = 'sent'
  end
end

function Arcade:_events_response(value)
  self._session = value.session
  local s, view = self._session, self._session.view or {}
  if view.cart_hash ~= 0 and
     (view.cart_hash ~= self.hash or view.cart_version ~= self.version or
      view.mode ~= self.mode) then
    self._error = 'Different game ID, version or mode at this table'
    return
  end
  if self._recovery then return end
  for _, e in ipairs(value.events or {}) do
    if e.revision ~= self._revision + 1 then
      self._error = 'Missing actions; requesting application checkpoint'
      self:request_checkpoint()
      return
    end
    self._revision = e.revision
    local raw = from_hex(e.hex)
    if raw:byte(1) == 1 then
      local player = nil
      for i, callsign in ipairs(s.players) do
        if callsign == e.actor then player = i - 1; break end
      end
      table.insert(self._events, {player=player, actor=e.actor,
        revision=e.revision, data=raw:sub(2)})
    end
    if self._waiting_commit and (e.actor == self._callsign or
      self._transport == 'practice') then self._waiting_commit = false end
  end
  if value.revision > self._revision and #value.events == 0 then
    self._error = 'Action log incomplete; requesting application checkpoint'
    self:request_checkpoint()
  end
  if view.cart_hash == 0 and s.status == 'active' and
     s.coordinator == self._callsign and not self._config_queued then
    table.insert(self._actions, 1, config_frame(self))
    self._config_queued = true
  end
end

function Arcade:_consume(result)
  self._busy = false
  if not result.ok then
    self._error = result.error
    self._retry_cooldown = 1
    if result.op == 'action' then self._waiting_commit = false end
    if result.op == 'snapshot_chunk' and self._snapshot_out then
      self._snapshot_out.sent = false
    end
    return
  end
  local value = result.value
  if result.op == 'identity' then
    self._callsign = value.callsign
    self._transport = value.transport
    self._needs_identity = false
  elseif result.op == 'create' then
    self.session_id = value.session_id
    self._revision, self._opaque_cursor = 0, 0
    self._session, self._config_queued = nil, false
  elseif result.op == 'events' then self:_events_response(value)
  elseif result.op == 'opaque' then
    for _, item in ipairs(value.messages or {}) do
      self._opaque_cursor = math.max(self._opaque_cursor, item.id)
      self:_handle_side(item)
    end
  elseif result.op == 'snapshot_chunk' then
    if self._snapshot_out then
      if self._transport == 'practice' then self:_advance_checkpoint()
      else self._snapshot_out.sequence = value.sequence end
    end
  elseif result.op == 'snapshot_ack' then
    if value.state == 'delivered' then self:_advance_checkpoint()
    elseif value.state == 'failed' then
      self._snapshot_out = nil
      self._error = 'Checkpoint delivery failed'
    end
  elseif result.op == 'action' then
    table.remove(self._actions, 1)
    self._waiting_commit = true
  elseif result.op == 'estimate' then table.insert(self._estimates, value)
  end
end

function Arcade:_can_send(packet)
  local s = self._session
  if not s or s.status ~= 'active' or s.has_pending_action then return false end
  local kind = packet:byte(1)
  if kind == 0 then return s.coordinator == self._callsign and s.view.cart_hash == 0 end
  if kind == 2 then return s.coordinator == self._callsign end
  return self:my_turn()
end

function Arcade:update(dt)
  if self._closed then return end
  local item = self.responses:pop()
  while item do self:_consume(item); item = self.responses:pop() end
  self:_prepare_checkpoint()
  if self._recovery and love.timer.getTime() - self._recovery.started > 300 then
    self._recovery = nil
    self._checkpoint_status = 'timed out'
    self._error = 'Checkpoint timed out; retry synchronization'
  end
  if self._snapshot_out and self._snapshot_out.sequence then
    self._snapshot_out.elapsed = self._snapshot_out.elapsed + (dt or 0)
    if self._snapshot_out.elapsed > 120 then
      self._snapshot_out = nil
      self._error = 'Checkpoint chunk acknowledgment timed out'
    end
  end
  if self._busy then return end
  self._retry_cooldown = math.max(0, self._retry_cooldown - (dt or 0))
  if self._retry_cooldown > 0 then return end
  if #self._commands > 0 then
    local job = table.remove(self._commands, 1)
    self:_request(job.op, job.method, job.path, job.body)
    return
  end
  if self._needs_identity then
    self:_request('identity', 'GET', '/api/engine/sessions')
    return
  end
  if not self.session_id then return end
  self.elapsed = self.elapsed + (dt or 0)
  self.opaque_elapsed = self.opaque_elapsed + (dt or 0)
  self._snapshot_ack_timer = self._snapshot_ack_timer + (dt or 0)
  if self.elapsed >= self.interval or not self._session then
    self.elapsed = 0
    self:_request('events', 'GET', '/api/engine/events?session_id=' ..
      self.session_id .. '&after=' .. self._revision)
  elseif self._snapshot_out and not self._snapshot_out.sent then
    local out = self._snapshot_out
    out.sent = true
    self:_request('snapshot_chunk', 'POST', '/api/engine/opaque',
      {session_id=self.session_id, target=out.target, message=out.chunks[out.index]})
  elseif self._snapshot_out and self._snapshot_out.sequence and
         self._snapshot_ack_timer >= 0.5 then
    self._snapshot_ack_timer = 0
    self:_request('snapshot_ack', 'GET', '/api/engine/delivery?session_id=' ..
      self.session_id .. '&sequence=' .. self._snapshot_out.sequence)
  elseif #self._actions > 0 and not self._waiting_commit and not self._recovery and
         self:_can_send(self._actions[1]) then
    self:_request('action', 'POST', '/api/engine/action',
      {session_id=self.session_id, hex=to_hex(self._actions[1])})
  elseif self.opaque_elapsed >= self.interval * 2 then
    self.opaque_elapsed = 0
    self:_request('opaque', 'GET', '/api/engine/opaque?session_id=' ..
      self.session_id .. '&after=' .. self._opaque_cursor)
  end
end

function Arcade:close()
  if self._closed then return end
  self._closed = true
  self.requests:push({op='stop'})
end

return Arcade
