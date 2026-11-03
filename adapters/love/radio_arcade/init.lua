-- Radio Arcade's native LÖVE 11.x adapter. No HTTP calls block love.update().
-- Copy the entire radio_arcade directory into your LÖVE project's root.
local bit = require('bit') -- LuaJIT, supplied by LÖVE
local Arcade = {}
Arcade.__index = Arcade
local serial = 0

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

-- Side messages are public radio traffic, at most 200 UTF-8 bytes with the
-- RA: prefix. Apps may implement their own compressed checkpoint format.
function Arcade:send_side(target, message)
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
  if not self:connected() then return false end
  if self._transport == 'practice' then return true end
  local s = self._session
  local seated = false
  for _, player in ipairs(s.players) do
    if player == self._callsign then seated = true end
  end
  return seated and (self.mode == 'event' or s.current_player == self._callsign)
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
  for _, e in ipairs(value.events or {}) do
    if e.revision ~= self._revision + 1 then
      self._error = 'Missing committed actions: application checkpoint required'
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
    self._error = 'Action log incomplete: application checkpoint required'
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
      if item.sender ~= self._callsign then table.insert(self._side, item) end
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
  if self.elapsed >= self.interval or not self._session then
    self.elapsed = 0
    self:_request('events', 'GET', '/api/engine/events?session_id=' ..
      self.session_id .. '&after=' .. self._revision)
  elseif #self._actions > 0 and not self._waiting_commit and
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
