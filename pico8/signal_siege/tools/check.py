"""Exercise the cartridge in the real PICO-8 runtime without changing user saves."""
from pathlib import Path
import os, subprocess, tempfile
cart = Path(__file__).resolve().parents[1] / 'signal_siege.p8'
source = cart.read_text().replace('function _init()', 'function game_init()', 1)
checks = '''
function _init()
 local disk={} dget=function(i) return disk[i] or 0 end
 dset=function(i,v) disk[i]=v end cartdata=function() end
 game_init() _draw() assert(phase==0)
 start() assert(phase==1 and room==1) _draw()
 -- new expeditions show lore, resume safely, and continue on Z
 start(true) assert(phase==5) _draw() save() game_init() assert(phase==5)
 btnp=function(b) return b==4 end
 _update() assert(phase==1 and room==1)
 -- enemy mechanics: choose room/seed to exercise each type
 start() room=2 floorseed=1 enter(false)
 assert(enemy_kind()=="iron brute" and enemy_hit()==1)
 rounds=1 assert(enemy_hit()==5)
 floorseed=2 enter(false) assert(enemy_kind()=="null jammer")
 deck[1]=3 play(1) assert(focus==1)
 floorseed=3 enter(false) assert(enemy_kind()=="hollow leech")
 deck[1]=3 local before=enemy play(1) assert(enemy==before+2)
 -- shielding prevents lifesteal
 shield=24 deck[1]=3 before=enemy play(1) assert(enemy==before)
 save() game_init() assert(rounds==2 and enemy_kind()=="hollow leech")
 start()
 -- walk through the east doorway using actual update input
 btnp=function(b) return b==1 end
 px=13 py=5 _update() assert(room==2 and px==1)
 -- sealed room exits, tactical turns, and defeat penalty
 room=3 floorseed=3 px=1 py=5
 btnp=function(b) return b==0 end
 _update() assert(room==3 and px==1)
 enter(false) local size=#deck
 play(4) assert(#deck==size and focus==1)
 hp=20 play(5) assert(#deck==size and hp>20)
 salvage=9 hp=1 shield=0 rounds=1 deck[1]=3
 play(1) assert(phase==4 and salvage==4)
 save() game_init() assert(phase==4 and salvage==4)
 start() room=2
 -- wall collision
 px=1 py=1 btnp=function(b) return b==0 end
 _update() assert(px==1 and room==2)
 -- sword clears two minions before the room boss appears
 depth=1 floorseed=1 room=3 px=7 py=5 mobroom=0
 setup_mobs() mobs[1].x=7 mobs[1].y=4 mobs[1].hp=1
 mobs[2].x=7 mobs[2].y=6 mobs[2].hp=1
 face_x=0 face_y=-1 btnp=function(b) return b==4 end
 _update() assert(mobs[1].hp==0 and phase==1)
 face_y=1 _update() assert(hasbit(miniondone,3) and phase==1)
 save() game_init() assert(hasbit(miniondone,3))
 px=7 py=5 btnp=function(b) return b==2 end
 _update() assert(phase==2) _draw()
 enemy=1 deck[1]=1 play(1) assert(phase==3) _draw()
 take(offers[1]) assert(phase==1 and room==3 and hasbit(cleared,3))
 -- guardian victory and stair interaction
 room=9 px=7 py=5 enter(false) enemy=1 deck[1]=1
 play(1) assert(cards[offers[3]].tier==4)
 take(offers[3]) assert(power==1 and hasbit(cleared,9))
 px=7 py=5 btnp=function(b) return b==4 end
 _update() assert(depth==2 and room==1)
 -- every room is reachable through unobstructed centre lines
 for d=1,20 do depth=d for r=1,9 do room=r
  for x=1,13 do assert(not wall(x,5)) end
  for y=1,9 do assert(not wall(7,y)) end
 end end
 -- caches cannot be harvested twice, and healing is capped
 start() room=4 floorseed=2 px=7 py=5
 assert(room_kind()=="cache") cleared=bor(cleared,shl(1,room-1))
 btnp=function(b) return b==4 end
 _update() local found=salvage _update() assert(salvage==found)
 -- duplicate collection finds become salvage, and decks stay capped
 start() owned[4]=0
 for i=1,4 do take(4) end
 assert(owned[4]==2 and count(4)==2)
 for id=5,30 do take(id) end assert(#deck==12)
 -- save and reload exploration, combat and rewards
 depth=2 room=4 px=3 py=5 save()
 game_init() assert(room==4 and px==3 and depth==2)
 enter(false) save() game_init() assert(phase==2)
 enemy=1 deck[1]=1 play(1) save()
 local offer=offers[1] game_init()
 assert(phase==3 and offers[1]==offer)
 for id=1,60 do start() enter(false) deck[1]=id play(1) _draw() end
 start() archive=true page=60 _draw() archive=false
 printh("CRAWL CHECKS PASSED") shutdown()
end
'''
source=source.replace('\n__gfx__',checks+'\n__gfx__',1)
with tempfile.TemporaryDirectory() as folder:
    path=Path(folder)/'crawl_checks.p8'; path.write_text(source)
    env={**os.environ,'SDL_VIDEODRIVER':'dummy','SDL_AUDIODRIVER':'dummy'}
    result=subprocess.run(['/opt/pico-8/pico8','-x',str(path)],env=env,capture_output=True,text=True,timeout=20)
    print(result.stdout, result.stderr)
    if 'CRAWL CHECKS PASSED' not in result.stdout: raise SystemExit(1)
