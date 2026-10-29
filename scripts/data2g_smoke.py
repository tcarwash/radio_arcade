"""Exercise a complete game through two native Data2G audio-pipe hosts."""
import argparse, os, socket, subprocess, tempfile, time, contextlib, sys, random
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
parser = argparse.ArgumentParser()
parser.add_argument("--mode", choices=("broadcast", "connected"), default="broadcast")
parser.add_argument("--reconnect", action="store_true")
parser.add_argument("--cancel-first", action="store_true")
parser.add_argument("--host-binary", type=Path, default=Path(__file__).resolve().parents[2] / "Data2G/native/build/data2g-host")
args = parser.parse_args()
if args.reconnect and args.mode != "connected": parser.error("--reconnect requires --mode connected")

def wait(fn, seconds=75):
 end=time.monotonic()+seconds
 while time.monotonic()<end:
  if fn(): return
  time.sleep(.1)
 raise TimeoutError('game did not advance')
def ports():
 for base in random.SystemRandom().sample(range(22000, 30000, 6), 100):
  ss=[]
  try:
   for p in range(base,base+6):
    s=socket.socket(); ss.append(s); s.bind(('127.0.0.1',p))
   return base
  except OSError: pass
  finally:
   for s in ss: s.close()
with contextlib.nullcontext(tempfile.mkdtemp(prefix='arcade-data2g-')) as d:
 os.environ['RADIO_ARCADE_HOME']=d; print('Logs:',d,flush=True)
 p=Path(d); os.mkfifo(p/'ab'); os.mkfifo(p/'ba'); base=ports(); procs=[]; services=[]
 try:
  for i,(call,inp,out) in enumerate([('N0AAA','ba','ab'),('N0BBB','ab','ba')]):
   log=open(p/f'{i}.log','w'); procs.append(subprocess.Popen([str(args.host_binary),'--mycall',call,'--command-port',str(base+i*3),'--kiss-port',str(base+i*3+2),'--audio-io',f'pipe:{p/inp},{p/out}','--no-rig','--record-dir',str(p/f'rec{i}'),'--stats-interval','0','--no-decode-worker'],stdout=subprocess.DEVNULL,stderr=log))
  wait(lambda: all((p / f"{i}.log").exists() and "commands on" in (p / f"{i}.log").read_text() for i in range(2)), 30)
  from radio_arcade.store import SessionStore
  from radio_arcade.connected import ConnectedConnection
  from radio_arcade.transport_factory import BroadcastConnection
  from radio_arcade.service import ArcadeService
  for i,call in enumerate(('N0AAA','N0BBB')):
   port=base+i*3
   transport = ConnectedConnection('127.0.0.1',port,call) if args.mode == 'connected' else BroadcastConnection('127.0.0.1',port+2,port,'RAGAMES')
   services.append(ArcadeService(call,transport,SessionStore(p/f'{i}.db')))
  a,b=services
  if args.cancel_first:
   cancelled=a.create_game('tic-tac-toe','N0BBB'); wait(lambda: any(s['id']==cancelled for s in b.sessions()))
   a.cancel_invitation(cancelled,'N0BBB'); wait(lambda: b.session(cancelled)['status']=='cancelled')
   print('Invitation cancellation delivered',flush=True)
  sid=a.create_game('tic-tac-toe','N0BBB'); wait(lambda: any(s['id']==sid for s in b.sessions())); b.accept_invite(sid); wait(lambda: a.session(sid)['status']=='active' and b.session(sid)['status']=='active')
  for index,(engine,cell) in enumerate([(a,0),(b,3),(a,1),(b,4),(a,2)]):
   rev=engine.session(sid)['revision']; engine.legal_action(sid,bytes([cell])); wait(lambda: a.session(sid)['revision']>rev and b.session(sid)['revision']>rev)
   if args.reconnect and index == 1:
    a.disconnect(); wait(lambda: not a.transport.connected.is_set() and not b.transport.connected.is_set(),60); a.connect_peer('N0BBB'); wait(lambda: b.transport.connected.is_set(),60); print('Reconnected after revision 2',flush=True)
  assert all(e.session(sid)['status']=='finished' and e.session(sid)['view']['winner']=='N0AAA' for e in services); print(f'Data2G native {args.mode} game completed',flush=True)
 finally:
  for s in services: s.close()
  for proc in procs: proc.terminate()
  for proc in procs:
   try: proc.wait(5)
   except subprocess.TimeoutExpired: proc.kill(); proc.wait()
