-- Radio Arcade / PICO-8 networking library, v1.
-- Add #include pico8/radio.lua to your cart and call radio.poll() each frame.
-- Browser HTML export + radio_arcade/web.py bridge are required for RF play.

radio=radio or {}
radio._out={}
radio._in={}
radio._assembly=""
radio._snapshot=""
radio._ready=false
radio._status=0
radio._player=255
radio._turn=255
radio._mode=0
radio._revision=0
radio._error=""
radio._txseq=0

-- GPIO address space is 0x5f80..0x5fff. Two separate 64-byte slots:
-- TX seq/ack/kind/len/payload and RX seq/ack/kind/len/payload.
local tx=0x5f80
local rx=0x5fc0
local chunk=60

local function u16(n)
 return chr(n%256)..chr(flr(n/256)%256)
end

local function enqueue(kind,payload)
 payload=payload or ""
 if #payload>chunk then return false end
 add(radio._out,{kind=kind,data=payload})
 return true
end

local function enqueue_blob(part,last,payload)
 -- An END frame carries the final data, so there are no empty fragments.
 for i=1,#payload,chunk do
  local part_data=sub(payload,i,min(i+chunk-1,#payload))
  enqueue(i+chunk>#payload and last or part,part_data)
 end
 if #payload==0 then enqueue(last,"") end
end

-- Returns true when configured. Options: mode="turn" or "event".
function radio.init(cart_id,version,options)
 options=options or {}
 if type(cart_id)!="string" or #cart_id==0 or #cart_id>48 or
  type(version)!="number" or version<1 or version>32767 or version!=flr(version) then
  radio._error="invalid cart id/version"
  return false
 end
 for i=1,#cart_id do
  if ord(cart_id,i)<33 or ord(cart_id,i)>126 then
   radio._error="cart id must be printable ascii"
   return false
  end
 end
 radio._id=cart_id
 radio._version=version
 radio._mode=options.mode=="event" and 1 or 0
 radio._ready=false
 radio._status=0
 radio._revision=0
 radio._in={}
 radio._out={}
 radio._txseq=peek(tx)
 enqueue(1,cart_id..chr(0)..u16(version)..chr(radio._mode))
 return true
end

function radio.poll()
 -- Read and ACK at most one incoming message each frame.
 local seq=peek(rx)
 if seq!=peek(rx+1) then
  local kind=peek(rx+2)
  local n=min(peek(rx+3),chunk)
  local data=""
  for i=0,n-1 do data..=chr(peek(rx+4+i)) end
  if kind==16 and #data>=6 then
   radio._status=ord(data,1)
   radio._player=ord(data,2)
   radio._turn=ord(data,3)
   radio._mode=ord(data,4)
   radio._revision=ord(data,5)+ord(data,6)*256
   radio._ready=radio._status==2
  elseif kind==17 or kind==18 then
   radio._assembly..=data
   if kind==18 then
    local msg=radio._assembly
    radio._assembly=""
    if #msg>=3 then
     add(radio._in,{player=ord(msg,1),revision=ord(msg,2)+ord(msg,3)*256,
                    data=sub(msg,4)})
    end
   end
  elseif kind==19 then
   if type(radio._save)=="function" then
    local ok=radio._save()
    if type(ok)=="string" then enqueue_blob(4,5,ok) end
   else
    enqueue(8,"no snapshot save callback")
   end
  elseif kind==20 or kind==21 then
   radio._snapshot..=data
   if kind==21 then
    if type(radio._load)=="function" then radio._load(radio._snapshot) end
    radio._snapshot=""
   end
  elseif kind==22 then
   radio._error=data
  end
  poke(rx+1,seq)
 end

 -- Send a frame only when JavaScript acknowledged the previous frame.
 if #radio._out>0 and peek(tx)==peek(tx+1) then
  local packet=deli(radio._out,1)
  poke(tx+2,packet.kind)
  poke(tx+3,#packet.data)
  for i=1,#packet.data do poke(tx+3+i,ord(packet.data,i)) end
  radio._txseq=(radio._txseq+1)%256
  poke(tx,radio._txseq)
 end
end

-- Send raw cartridge bytes; use chr()/ord() to pack small binary events.
-- Maximum 189 bytes; protocol framing and RF delivery are handled elsewhere.
function radio.send(data)
 if type(data)!="string" or #data>189 then return false end
 enqueue_blob(2,3,data)
 return true
end

function radio.receive()
 if #radio._in==0 then return nil end
 return deli(radio._in,1)
end

function radio.my_turn()
 return radio._ready and radio._player!=255 and (radio._mode==1 or radio._player==radio._turn)
end

function radio.connected() return radio._ready end
function radio.player() return radio._player end -- zero-based roster index
function radio.revision() return radio._revision end
function radio.error() return radio._error end

-- User-supplied deterministic cartridge state codecs (binary strings).
-- Required when recovery cannot replay the entire event history.
function radio.snapshot(save_fn,load_fn)
 radio._save=save_fn
 radio._load=load_fn
end

function radio.request_sync() enqueue(6,"") end
function radio.finish() enqueue(7,"") end
