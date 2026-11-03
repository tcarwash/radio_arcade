# Radio Arcade for LÖVE 11.x

Place the whole [`radio_arcade/`](radio_arcade/) directory at the root of a
LÖVE project. No external Lua package installation is necessary: LÖVE supplies
LuaJIT, LuaSocket and threading.

```lua
local Arcade = require('radio_arcade')
local radio

function love.load()
  radio = Arcade.new({game_id='my-radio-game', version=1,
    mode='turn', session_id=os.getenv('RADIO_ARCADE_SESSION')})
end

function love.update(dt)
  radio:update(dt) -- sends HTTP requests in a separate LÖVE thread
  while true do
    local event = radio:receive()
    if not event then break end
    -- event.data is the exact binary game payload, excluding its EVENT tag.
    apply_committed_event(event.player, event.data, event.revision)
  end
end

function love.keypressed(key)
  if key == 'space' and radio:my_turn() then
    radio:send(string.char(42, 7)) -- two application bytes
  end
end

function love.quit() radio:close() end
```

The actor index is zero-based. The adapter replays the event log at startup;
game logic must be deterministic, including random-number generation.
Use `radio:create(peer, hosted)` to create a new table asynchronously (then
read `radio.session_id`), or `radio:control('join_game')`,
`radio:control('accept_invite')`, `radio:control('start_game')` for existing ones.
`radio:session()` returns the current server session snapshot.

`radio:send_side(target, 'RA:...')` carries a plaintext 200-byte-max side
message. `radio:receive_side()` pops received side messages. `radio:estimate(2,
{baud=600,bits_per_symbol=3,code_rate=0.5})` queues a **local** debug estimate;
retrieve it with `radio:receive_estimate()` (nothing goes on air).

## Run the artillery example

1. Create an `external-game` table as described in [`../README.md`](../README.md).
2. Copy [`examples/artillery/main.lua`](examples/artillery/main.lua) to a game
   folder as `main.lua`, alongside the `radio_arcade/` directory.
3. Set `RADIO_ARCADE_SESSION` to the table ID and run `love your_game_folder`.
4. Do the same at the other station. Select angle/power with arrow keys, fire
   with Space, and press E for an estimated airtime readout.

Every shot sends exactly two game bytes. Flight paths, tank drawing and hit
calculation are performed locally using deterministic physics.
