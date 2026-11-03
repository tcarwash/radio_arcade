pico-8 cartridge // http://www.pico-8.com
version 42
__lua__
#include ../radio.lua
-- tiny example of a cartridge owning all its own rules and graphics
local score={0,0}

function _init()
 radio.init("radio-dots",1,{mode="turn"})
 radio.snapshot(
  function() return chr(score[1]%256)..chr(score[2]%256) end,
  function(data)
   if #data>=2 then
    score[1]=ord(data,1)
    score[2]=ord(data,2)
   end
  end)
end

function _update()
 radio.poll()
 if radio.my_turn() and btnp(4) then radio.send(chr(1)) end
 while true do
  local event=radio.receive()
  if not event then break end
  if #event.data>=1 and ord(event.data,1)==1 then
   local n=event.player+1
   if n==1 or n==2 then score[n]=score[n]+1 end
  end
 end
 if btnp(5) then radio.request_sync() end
end

function _draw()
 cls(1)
 print("radio dots",43,12,10)
 print("station 1",13,34,7)
 print(score[1],90,34,11)
 print("station 2",13,48,7)
 print(score[2],90,48,12)
 print("o: take a turn",22,76,6)
 print("x: request sync",22,86,6)
 if radio.connected() then
  print("radio ready",34,108,11)
 else
  print("waiting for session",20,108,8)
 end
end
