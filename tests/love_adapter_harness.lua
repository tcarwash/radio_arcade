-- Runs with the system Lua 5.4 shared library, using tiny LÖVE/bit mocks.
-- This exercises Lua code paths and serialization without needing a GUI.
package.path = 'adapters/love/?.lua;adapters/love/?/init.lua;' .. package.path

local function signed(n)
  n = n & 0xffffffff
  return n >= 0x80000000 and n - 0x100000000 or n
end
package.preload.bit = function()
  return {
    bxor=function(a,b) return signed(a ~ b) end,
    lshift=function(a,b) return signed(a << b) end,
    tobit=signed,
  }
end

local alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
local function base64_encode(s)
  local out = {}
  for i = 1, #s, 3 do
    local a, b, c = s:byte(i, i + 2)
    local n = a * 65536 + (b or 0) * 256 + (c or 0)
    local p = {math.floor(n / 262144) % 64, math.floor(n / 4096) % 64,
               math.floor(n / 64) % 64, n % 64}
    out[#out+1] = alphabet:sub(p[1] + 1, p[1] + 1)
    out[#out+1] = alphabet:sub(p[2] + 1, p[2] + 1)
    out[#out+1] = b and alphabet:sub(p[3] + 1, p[3] + 1) or '='
    out[#out+1] = c and alphabet:sub(p[4] + 1, p[4] + 1) or '='
  end
  return table.concat(out)
end
local function base64_decode(s)
  assert(#s % 4 == 0, 'invalid base64')
  local out = {}
  for i = 1, #s, 4 do
    local digits = {}
    for j = 0, 3 do
      local c = s:sub(i+j, i+j)
      local p = alphabet:find(c, 1, true)
      assert(p or c == '=', 'invalid base64 character')
      digits[j+1] = (p or 1) - 1
    end
    local n = digits[1] * 262144 + digits[2] * 4096 + digits[3] * 64 + digits[4]
    out[#out+1] = string.char(math.floor(n / 65536) % 256)
    if s:sub(i+2, i+2) ~= '=' then out[#out+1] = string.char(math.floor(n / 256) % 256) end
    if s:sub(i+3, i+3) ~= '=' then out[#out+1] = string.char(n % 256) end
  end
  return table.concat(out)
end

local channels = {}
love = {
  timer = {getTime=function() return 1 end},
  math = {random=function() return 0x1234 end},
  data = {
    encode=function(container, format, data)
      assert(container == 'string' and format == 'base64')
      return base64_encode(data)
    end,
    decode=function(container, format, data)
      assert(container == 'string' and format == 'base64')
      return base64_decode(data)
    end,
  },
  thread = {
    getChannel=function(name)
      if not channels[name] then
        local queue = {}
        channels[name] = {
          push=function(_,item) queue[#queue+1] = item end,
          pop=function(_) return table.remove(queue, 1) end,
        }
      end
      return channels[name]
    end,
    newThread=function(_)
      return {start=function(...) end}
    end,
  },
}

local json = require('radio_arcade.json')
local object = json.decode('{"list":[true,false,"hi"],"size":42,"value":null}')
assert(object.list[1] == true and object.list[2] == false and object.list[3] == 'hi')
assert(object.size == 42)
assert(json.decode(json.encode({nested={label='hello'}, value=false})).nested.label == 'hello')

local Arcade = require('radio_arcade')
local host = Arcade.new({game_id='hello', session_id='0123456789abcdef'})
local guest = Arcade.new({game_id='hello', session_id='0123456789abcdef'})
assert(host.hash == 0x4f9f2cab, 'FNV32 hash does not match Python/JavaScript')
local shared = {players={'AG7SU','K7ABC'}, coordinator='AG7SU', status='active',
  current_player='AG7SU', has_pending_action=false,
  view={cart_hash=host.hash, cart_version=1, mode='turn'}}
host._session, guest._session = shared, shared
host._callsign, guest._callsign = 'AG7SU', 'K7ABC'
host._transport, guest._transport = 'broadcast', 'broadcast'
host._revision = 23

local data = (string.char(0, 1, 255) .. 'hello-world'):rep(20)
local loaded, loaded_revision
host:snapshot(function() return data end, function() end)
guest:snapshot(function() return '' end,
  function(bytes, revision) loaded, loaded_revision = bytes, revision end)
guest._recovery = {nonce='12341234', parts={}, started=1}
host:_handle_side({sender='K7ABC', body='RA:REQ:11111111', created=os.time()-600})
assert(#host._snapshot_requests == 0, 'stale RF request should be ignored')
host:_handle_side({sender='K7ABC', body='RA:REQ:12341234'})
host:_prepare_checkpoint()
local chunks = host._snapshot_out.chunks
assert(#chunks > 1, 'expected multipart checkpoint')
for i = #chunks, 1, -1 do
  guest:_accept_checkpoint('AG7SU', chunks[i])
end
assert(loaded == data and loaded_revision == 23, 'checkpoint did not round-trip')
assert(guest._revision == 23 and guest._recovery == nil)
assert(guest:checkpoint_status() == 'restored')
assert(not host:request_checkpoint(), 'coordinator must not request its own checkpoint')

host:close()
guest:close()
