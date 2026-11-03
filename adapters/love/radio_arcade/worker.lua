-- HTTP runs on a LÖVE worker thread, never on the game's render/update thread.
local key, base_url = ...
local requests = love.thread.getChannel(key .. ':request')
local responses = love.thread.getChannel(key .. ':response')
local http = require('socket.http')
local ltn12 = require('ltn12')
local json = require('radio_arcade.json')
http.TIMEOUT = 2

while true do
  local job = requests:demand()
  if job.op == 'stop' then break end
  local ok, result = pcall(function()
    local payload = job.body and json.encode(job.body) or nil
    local sink = {}
    local _, code = http.request({
      url = base_url .. job.path,
      method = job.method,
      headers = payload and {['Content-Type']='application/json',
        ['Content-Length']=tostring(#payload)} or {},
      source = payload and ltn12.source.string(payload) or nil,
      sink = ltn12.sink.table(sink),
    })
    local decoded = json.decode(table.concat(sink))
    if tonumber(code) == nil or tonumber(code) < 200 or tonumber(code) >= 300 then
      error(decoded.error or ('HTTP ' .. tostring(code)))
    end
    return decoded
  end)
  responses:push({op=job.op, ok=ok, value=ok and result or nil,
                  error=ok and nil or tostring(result)})
end
