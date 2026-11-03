-- Copy this main.lua to a LÖVE project next to radio_arcade/.
-- Set RADIO_ARCADE_SESSION to a running external-game session before launch.
local Arcade = require('radio_arcade')
local radio
local angle, power = 45, 65
local ground = 405
local guns = {110, 690}
local health = {3, 3}
local trails = {}
local moves, winner = 0, nil
local estimate = nil
local note = 'Press SPACE to fire, arrows to adjust angle/power'

local function shoot(player, a, p)
  local x = guns[player + 1]
  local theta = math.rad(player == 0 and a or 180 - a)
  local vx = math.cos(theta) * p * 6.2
  local vy = math.sin(theta) * p * 6.2
  local result = {}
  for step = 0, 1200 do
    local t = step / 120
    local px = x + vx * t
    local py = ground - 28 - vy * t + 0.5 * 420 * t * t
    result[#result + 1] = {px, py}
    if step > 0 and (py >= ground or px < 0 or px > 800) then
      if math.abs(px - guns[2 - player]) < 28 then
        health[2 - player] = math.max(0, health[2 - player] - 1)
        if health[2 - player] == 0 then winner = player end
      end
      break
    end
  end
  trails[#trails + 1] = {player=player, points=result}
end

function love.load()
  love.window.setMode(800, 500, {resizable=false})
  love.window.setTitle('Radio Arcade / LÖVE Artillery Demo')
  local sid = os.getenv('RADIO_ARCADE_SESSION')
  radio = Arcade.new({game_id='radio-artillery-demo', version=1,
    mode='turn', session_id=sid, poll_interval=0.3})
  if not sid then note = 'Set RADIO_ARCADE_SESSION to a running external-game session' end
end

function love.update(dt)
  radio:update(dt)
  while true do
    local event = radio:receive()
    if not event then break end
    if #event.data == 2 and event.player ~= nil then
      shoot(event.player, event.data:byte(1), event.data:byte(2))
      moves = moves + 1
      note = 'Shot committed at revision ' .. event.revision
    end
  end
  estimate = radio:receive_estimate() or estimate
end

function love.keypressed(key)
  if key == 'up' then power = math.min(100, power + 1) end
  if key == 'down' then power = math.max(20, power - 1) end
  if key == 'left' then angle = math.max(5, angle - 1) end
  if key == 'right' then angle = math.min(85, angle + 1) end
  if key == 'space' and radio:my_turn() and not winner then
    -- Exactly two application bytes, irrespective of terrain or resolution.
    radio:send(string.char(angle, power))
    note = 'Shot queued; awaiting radio commit'
  end
  if key == 'e' and radio.session_id then
    radio:estimate(2, {baud=600, bits_per_symbol=3, code_rate=0.5})
  end
end

function love.draw()
  love.graphics.clear(0.045, 0.075, 0.12)
  love.graphics.setColor(0.25, 0.43, 0.59)
  love.graphics.rectangle('fill', 0, ground, 800, 95)
  love.graphics.setColor(0.14, 0.27, 0.38)
  love.graphics.rectangle('fill', 0, ground + 10, 800, 9)
  for i, t in ipairs(trails) do
    love.graphics.setColor(t.player == 0 and 0.3 or 1, 0.8, 0.7, 0.8)
    for j = 2, #t.points do
      love.graphics.line(t.points[j-1][1], t.points[j-1][2], t.points[j][1], t.points[j][2])
    end
  end
  for player = 0, 1 do
    love.graphics.setColor(player == 0 and 0.17 or 0.96, player == 0 and 0.82 or 0.62, 0.65)
    love.graphics.rectangle('fill', guns[player + 1] - 22, ground - 19, 44, 19, 6, 6)
    love.graphics.setColor(1, 1, 1)
    love.graphics.print('HP: ' .. health[player + 1], guns[player + 1] - 20, ground - 45)
  end
  love.graphics.setColor(1, 1, 1)
  love.graphics.print('RADIO ARCADE / LÖVE ARTILLERY', 24, 20, 0, 1.5)
  love.graphics.print('Angle: ' .. angle .. '°   Power: ' .. power, 24, 62)
  love.graphics.print('Local turn: ' .. (radio:my_turn() and 'READY' or 'WAIT') ..
    '    Committed shots: ' .. moves .. '    Rev: ' .. radio:revision(), 24, 88)
  love.graphics.print(note, 24, 117)
  if estimate then
    love.graphics.print(string.format('2-byte shot: %d B RA frame / %.2f s @ 600 baud 8-FSK 1/2 FEC',
      estimate.radio_arcade_frame_bytes, estimate.estimated_airtime_ms / 1000), 24, 143)
  else
    love.graphics.print('Press E for a theoretical airtime estimate', 24, 143)
  end
  if radio:error() then
    love.graphics.setColor(1, 0.45, 0.45)
    love.graphics.print(radio:error(), 24, 172)
  end
  if winner then love.graphics.print('Player ' .. (winner + 1) .. ' wins!', 300, 200, 0, 2) end
end

function love.quit()
  radio:close()
end
