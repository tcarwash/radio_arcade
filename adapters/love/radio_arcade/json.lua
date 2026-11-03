-- Minimal dependency-free JSON codec for Radio Arcade's local HTTP bridge.
-- Handles nested dictionaries/arrays, UTF-8, JSON escaping and numbers.
local json = {}

local escapes = {['"']='\\"', ['\\']='\\\\', ['\b']='\\b', ['\f']='\\f',
                 ['\n']='\\n', ['\r']='\\r', ['\t']='\\t'}

local function quote(s)
  return '"' .. s:gsub('[%z\1-\31\\"]', function(c)
    return escapes[c] or string.format('\\u%04x', c:byte())
  end) .. '"'
end

local function encode(value)
  local t = type(value)
  if t == 'string' then return quote(value) end
  if t == 'boolean' then return tostring(value) end
  if t == 'number' then
    assert(value == value and value ~= math.huge and value ~= -math.huge, 'invalid JSON number')
    return tostring(value)
  end
  if t == 'nil' then return 'null' end
  assert(t == 'table', 'unsupported JSON value')
  local length = #value
  local array = length > 0
  for k in pairs(value) do
    if type(k) ~= 'number' or k < 1 or k > length or k % 1 ~= 0 then array = false end
  end
  local parts = {}
  if array then
    for i = 1, length do parts[#parts + 1] = encode(value[i]) end
    return '[' .. table.concat(parts, ',') .. ']'
  end
  for key, item in pairs(value) do
    assert(type(key) == 'string', 'JSON object keys must be strings')
    parts[#parts + 1] = quote(key) .. ':' .. encode(item)
  end
  return '{' .. table.concat(parts, ',') .. '}'
end
json.encode = encode

local function utf8(code)
  if code < 128 then return string.char(code) end
  if code < 2048 then return string.char(192 + math.floor(code / 64), 128 + code % 64) end
  if code < 65536 then
    return string.char(224 + math.floor(code / 4096),
      128 + math.floor(code / 64) % 64, 128 + code % 64)
  end
  return string.char(240 + math.floor(code / 262144),
    128 + math.floor(code / 4096) % 64, 128 + math.floor(code / 64) % 64,
    128 + code % 64)
end

function json.decode(source)
  assert(type(source) == 'string', 'JSON source must be a string')
  local pos, length = 1, #source
  local function whitespace()
    while pos <= length and source:sub(pos, pos):match('%s') do pos = pos + 1 end
  end
  local parse
  local function parse_string()
    assert(source:sub(pos, pos) == '"', 'expected JSON string')
    pos = pos + 1
    local out = {}
    while pos <= length do
      local c = source:sub(pos, pos)
      pos = pos + 1
      if c == '"' then return table.concat(out) end
      if c ~= '\\' then
        assert(c:byte() >= 32, 'control character in JSON string')
        out[#out + 1] = c
      else
        local escaped = source:sub(pos, pos)
        pos = pos + 1
        local map = {['"']='"', ['\\']='\\', ['/']='/', b='\b', f='\f',
                     n='\n', r='\r', t='\t'}
        if map[escaped] then out[#out + 1] = map[escaped]
        elseif escaped == 'u' then
          local digits = source:sub(pos, pos + 3)
          assert(digits:match('^%x%x%x%x$'), 'invalid unicode escape')
          pos = pos + 4
          local code = tonumber(digits, 16)
          if code >= 0xD800 and code <= 0xDBFF then
            assert(source:sub(pos, pos + 1) == '\\u', 'missing low surrogate')
            local low = tonumber(source:sub(pos + 2, pos + 5), 16)
            assert(low and low >= 0xDC00 and low <= 0xDFFF, 'invalid low surrogate')
            pos = pos + 6
            code = 0x10000 + (code - 0xD800) * 1024 + low - 0xDC00
          else
            assert(code < 0xDC00 or code > 0xDFFF, 'unexpected low surrogate')
          end
          out[#out + 1] = utf8(code)
        else error('invalid JSON escape') end
      end
    end
    error('unterminated JSON string')
  end
  function parse()
    whitespace()
    local c = source:sub(pos, pos)
    if c == '"' then return parse_string() end
    if c == '{' or c == '[' then
      local is_array = c == '['
      pos = pos + 1
      whitespace()
      local result = {}
      local close = is_array and ']' or '}'
      if source:sub(pos, pos) == close then pos = pos + 1; return result end
      while true do
        local key
        if not is_array then
          whitespace()
          key = parse_string()
          whitespace()
          assert(source:sub(pos, pos) == ':', 'expected colon')
          pos = pos + 1
        end
        local value = parse()
        if is_array then result[#result + 1] = value else result[key] = value end
        whitespace()
        local next_char = source:sub(pos, pos)
        pos = pos + 1
        if next_char == close then return result end
        assert(next_char == ',', 'expected comma')
      end
    end
    for literal, value in pairs({['true']=true, ['false']=false}) do
      if source:sub(pos, pos + #literal - 1) == literal then
        pos = pos + #literal
        return value
      end
    end
    if source:sub(pos, pos + 3) == 'null' then pos = pos + 4; return nil end
    local number = source:sub(pos):match('^-?%d+%.?%d*[eE]?[+%-]?%d*')
    local value = number and tonumber(number)
    assert(value, 'invalid JSON number')
    pos = pos + #number
    return value
  end
  local result = parse()
  whitespace()
  assert(pos > length, 'trailing JSON input')
  return result
end

return json
