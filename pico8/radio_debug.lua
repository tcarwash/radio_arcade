-- PICO-8 developer estimate helper. No connection or transmission required.
-- Include from a cart using: #include pico8/radio_debug.lua
radio=radio or {}

-- radio.estimate(payload_bytes, options) -> result or nil,reason
-- options: source, destination, actor, baud, bits_per_symbol, code_rate,
--          rate_bps, modem_overhead_bytes, preamble_ms, transmissions, compact
function radio.estimate(payload_bytes, options)
 options=options or {}
 local src=options.source or radio.callsign
 local dst=options.destination or radio.destination
 local actor=options.actor or src

 if type(payload_bytes)!="number" or payload_bytes<0 or payload_bytes>200 or flr(payload_bytes)!=payload_bytes then
  return nil,"invalid payload byte count"
 end
 if type(src)!="string" or type(dst)!="string" or type(actor)!="string" or #src<1 or #dst<1 or #actor<1 or #src>15 or #dst>15 or #actor>15 then
  return nil,"source/destination/actor must be callsigns"
 end

 local extra=options.modem_overhead_bytes or 0
 local preamble=options.preamble_ms or 0
 local repeats=options.transmissions or 1
 if extra<0 or flr(extra)!=extra or preamble<0 or repeats<1 or flr(repeats)!=repeats then
  return nil,"invalid overhead or repetition count"
 end

 -- Radio Arcade v6: active turns use the 8-byte short-session header.
 -- Setup/fallback full frames have a 17-byte header and an actor wrapper.
 local frame=options.compact and (8+payload_bytes) or (17+#src+#dst+2+#actor+payload_bytes)
 if frame>240 then return nil,"radio arcade frame too large" end
 local result={payload_bytes=payload_bytes,
  radio_arcade_frame_bytes=frame,
  radio_arcade_overhead_bytes=frame-payload_bytes,
  transmissions=repeats}

 local baud=options.baud
 local bps=options.rate_bps
 if baud!=nil then
  if bps!=nil then return nil,"provide baud or rate_bps" end
  local bits=options.bits_per_symbol
  local rate=options.code_rate or 1
  if baud<=0 or bits==nil or bits<1 or flr(bits)!=bits or rate<=0 or rate>1 then
   return nil,"invalid symbol rate/modulation/FEC"
  end
  result.effective_rate_bps=baud*bits*rate
  result.estimated_symbols=ceil((frame+extra)*8/(bits*rate))
  -- Use seconds to avoid overflowing PICO-8's fixed-point integer range.
  result.estimated_airtime_s=repeats*(result.estimated_symbols/baud+preamble/1000)
 elseif bps!=nil then
  if bps<=0 or options.bits_per_symbol!=nil or options.code_rate!=nil then
   return nil,"invalid effective bitrate options"
  end
  result.effective_rate_bps=bps
  result.estimated_airtime_s=repeats*((frame+extra)*8/bps+preamble/1000)
 elseif options.bits_per_symbol!=nil or options.code_rate!=nil then
  return nil,"symbol parameters require baud"
 end

 return result
end
