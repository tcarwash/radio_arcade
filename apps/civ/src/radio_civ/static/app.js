"use strict";
// This browser client never implements game rules. All orders go through
// ArcadeService; radio-civ.rules validates and applies them on the Python side.
const $ = id => document.getElementById(id);
const NS = "http://www.w3.org/2000/svg";
const HEX = ["#64dfcc", "#f1c26e", "#9c8cf5", "#f07f9a"];
const BIOMES = {water:["#1c5274","≈"], plains:["#536c52","·"], forest:["#2f7862","♣"], hills:["#706961","▲"], desert:["#967952","✦"]};
const UNITS = ["Scout","Warrior","Settler"];
const UNIT_COSTS = [[2,3,0],[2,4,2],[6,6,0]];
const BUILDINGS = ["Granary","Sawmill","Forge","Library"];
const BUILD_COSTS = [[2,4,0],[0,5,1],[0,3,5],[0,4,3]];
const TECHS = ["Agriculture","Mining","Bronze Working","Writing","Navigation"];
const TECH_COSTS = [6,7,10,9,11];
const state = {config:null, sid:null, session:null, sessions:[], selected:null,
               moveOrigin:null, seenChats:new Set(), lastRevision:null, busy:false, refreshing:false};

const safe = s => String(s).replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[c]);

async function api(path, body) {
  const options = body === undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)};
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `${response.status} ${response.statusText}`);
  return data;
}

function note(text, bad=false) {
  $("notice").textContent = text;
  $("notice").style.color = bad ? "#f2a8a7" : "#79bcb3";
  const log = document.createElement("div");
  log.className = "logLine";
  log.textContent = text;
  $("log").appendChild(log);
  while ($("log").children.length > 60) $("log").firstChild.remove();
  $("log").scrollTop = $("log").scrollHeight;
}

async function send(path, body={}) {
  if (state.busy) return;
  state.busy = true;
  try {
    const result = await api(path, body);
    if (result.session) state.sid = result.session.id;
    await refresh(true);
    return result;
  } catch (error) {
    note(error.message, true);
    return null;
  } finally { state.busy = false; }
}

const endpoint = operation => `/api/sessions/${state.sid}/${operation}`;
function action(bytes) { return state.sid ? send(endpoint("action"), {bytes}) : null; }
function svgNode(name, attrs={}) {
  const element = document.createElementNS(NS, name);
  for (const [key,val] of Object.entries(attrs)) element.setAttribute(key, String(val));
  return element;
}
function tileCenter(id) {
  const x = id%8, y = Math.floor(id/8), step = 70.148;
  return [75 + x*step + (y&1)*step/2, 65+y*60.75];
}
function adjacent(tile) {
  const x=tile%8, y=Math.floor(tile/8);
  const offsets=y%2 ? [[-1,0],[1,0],[0,-1],[1,-1],[0,1],[1,1]]
                    : [[-1,0],[1,0],[-1,-1],[0,-1],[-1,1],[0,1]];
  return offsets.map(([dx,dy])=>[x+dx,y+dy]).filter(([cx,cy])=>cx>=0&&cy>=0&&cx<8&&cy<8).map(([cx,cy])=>cy*8+cx);
}
function perspective(view) {
  if (state.config?.local) return Math.max(0,view.players.findIndex(p=>p.call===view.turn));
  return view.players.findIndex(p=>p.call===state.config?.call);
}
function canAct(view) {
  return !!(view && !view.finished && view.ap>0 && !state.config.observer &&
           (state.config.local || state.config.call===view.turn) && !state.session?.pending_move);
}
function selected(view) {
  if (!view || state.selected===null) return {city:-1,unit:-1,cityAt:null,unitAt:null};
  const own=perspective(view);
  return {
    city:view.cities.findIndex(c=>c.tile===state.selected && c.owner===own),
    unit:view.units.findIndex(u=>u.tile===state.selected && u.owner===own),
    cityAt:view.cities.find(c=>c.tile===state.selected),
    unitAt:view.units.find(u=>u.tile===state.selected),
  };
}
async function clickTile(tile) {
  const view=state.session?.view;
  if (!view) return;
  if (state.moveOrigin!==null && adjacent(state.moveOrigin).includes(tile) && canAct(view)) {
    const index=view.units.findIndex(u=>u.tile===state.moveOrigin && u.owner===perspective(view));
    if (index>=0) {
      const ok=await action([77,index,tile]);
      if (ok) state.selected=tile;
    }
  } else {
    state.selected=tile;
  }
  state.moveOrigin=null;
  render();
}

function drawMap(view) {
  const svg=$("map"); svg.replaceChildren();
  $("empty").hidden=!!view;
  if (!view) return;
  const own=perspective(view);
  const seen=own>=0 ? BigInt("0x"+view.players[own].seen) : (1n<<64n)-1n;
  for (let id=0;id<64;id++) {
    const [cx,cy]=tileCenter(id);
    const known=!!(seen & (1n<<BigInt(id)));
    const biome=view.terrain[id], [color,icon]=known?BIOMES[biome]:["#142538","?"];
    const points=Array.from({length:6},(_,i)=>{
      const angle=Math.PI/180*(60*i-90);
      return `${(cx+40*Math.cos(angle)).toFixed(2)},${(cy+40*Math.sin(angle)).toFixed(2)}`;
    }).join(" ");
    const tile=svgNode("polygon",{points,fill:color,class:"hex"+
      (state.selected===id?" selected":state.moveOrigin!==null&&adjacent(state.moveOrigin).includes(id)?" reachable":"")});
    tile.addEventListener("click",()=>clickTile(id));
    tile.appendChild(svgNode("title")).textContent=`${String.fromCharCode(65+id%8)}${Math.floor(id/8)+1} · ${known?biome:"unexplored"}`;
    svg.appendChild(tile);
    const text=svgNode("text",{x:cx,y:cy+2,class:"terrainIcon"+(!known?" fogIcon":"")});
    text.textContent=icon; svg.appendChild(text);
  }
  for (const city of view.cities) {
    if (!(seen&(1n<<BigInt(city.tile)))) continue;
    const [cx,cy]=tileCenter(city.tile), color=HEX[city.owner];
    svg.appendChild(svgNode("circle",{cx,cy:cy+1,r:20,fill:"#162638",stroke:color,"stroke-width":3.5,"pointer-events":"none"}));
    const glyph=svgNode("text",{x:cx,y:cy+4,class:"cityText",fill:color});
    glyph.textContent="⌂"; svg.appendChild(glyph);
    const pop=svgNode("text",{x:cx+22,y:cy+24,class:"pieceText",fill:"#fff"});
    pop.textContent=city.pop; svg.appendChild(pop);
  }
  for (const unit of view.units) {
    if (!(seen&(1n<<BigInt(unit.tile)))) continue;
    const [cx,cy]=tileCenter(unit.tile), color=HEX[unit.owner];
    svg.appendChild(svgNode("circle",{cx:cx+23,cy:cy-19,r:14,fill:color,class:"unitDisk"}));
    const letter=svgNode("text",{x:cx+23,y:cy-19,class:"pieceText"});
    letter.textContent="SWT"[unit.kind]; svg.appendChild(letter);
  }
}

function renderSessions() {
  const root=$("sessions"); root.replaceChildren();
  for (const item of state.sessions) {
    const button=document.createElement("button");
    button.className="session"+(item.id===state.sid?" active":"");
    const name=document.createElement("strong"); name.textContent=item.coordinator;
    const detail=document.createElement("small"); detail.textContent=`${item.status.toUpperCase()}  ·  ${item.players.length} nations  ·  ${item.id.slice(-6)}`;
    button.append(name,detail);
    button.addEventListener("click",()=>{state.sid=item.id;state.selected=null;state.moveOrigin=null;state.seenChats.clear();refresh(true);});
    root.appendChild(button);
  }
  if (!state.sessions.length) {
    const p=document.createElement("div"); p.className="explain";
    p.textContent="No campaigns yet. Create one to begin.";root.appendChild(p);
  }
}

function render() {
  const current=state.session, view=current?.view, call=state.config?.call||"?";
  renderSessions();
  $("connection").textContent=state.config ? `● ${state.config.observer?"RX ONLY":state.config.local?"LOCAL HOTSEAT":"ON AIR"}` : "● CONNECTING";
  $("station").textContent=call;
  $("stationInfo").textContent=state.config?.local?"Two-seat local game, no radio needed.":
    state.config?.observer?"Observer mode: receive only. Manual sync transmits.":"Connected to Data2G broadcast session.";
  $("create").disabled=!state.config||state.config.observer;
  $("join").disabled=!current||state.config?.observer||state.config?.local||
    current.coordinator===call||!["lobby","joining"].includes(current.status);
  $("start").disabled=!current||state.config?.observer||state.config?.local||current.status!=="lobby"||current.coordinator!==call;
  $("setOrder").disabled=!current||state.config?.observer||state.config?.local||current.status!=="lobby"||current.coordinator!==call;
  $("announce").disabled=$("setOrder").disabled;
  $("retry").disabled=!current||!current.pending_move||state.config?.observer;
  $("sync").disabled=!current;
  $("campaignTitle").textContent=current?`Campaign · ${current.id.slice(-6)}`:"The uncharted world";
  $("round").textContent=view?`ROUND ${view.round} / ${view.max_rounds} · ${view.ap} ORDERS LEFT`:"NO CAMPAIGN";
  $("turn").textContent=view?(view.finished?`★ VICTORY · ${view.winner}`:`◈ ${view.turn}'S TURN${state.config.local?" · HOTSEAT":""}`):
    current?"WAITING FOR START":"NO CAMPAIGN SELECTED";
  const own=view?perspective(view):-1, player=own>=0?view.players[own]:null;
  $("resources").innerHTML=player?
    [["FOOD",player.food],["TIMBER",player.wood],["ORE",player.ore],["SCIENCE",player.science]]
      .map(([name,value])=>`<div>${name}<br><b>${value}</b></div>`).join(""):
    "<div>Observer · public information</div>";
  $("standings").replaceChildren();
  if (view) view.players.forEach((nation,i)=>{
    const item=document.createElement("div"); item.className="nation";
    const top=document.createElement("div"); top.className="nationTop";
    const callLabel=document.createElement("span");callLabel.textContent=`● ${nation.call}${nation.call===view.turn?" ◂":""}`;
    callLabel.style.color=HEX[i];
    const points=document.createElement("span");points.className="score";points.textContent=`${nation.score} VP`;
    top.append(callLabel,points);
    const detail=document.createElement("span");detail.className="nationDetail";
    detail.textContent=`${view.cities.filter(c=>c.owner===i).length} cities · ${view.units.filter(u=>u.owner===i).length} units · ${Number(nation.tech).toString(2).replace(/0/g,"").length} techs`;
    item.append(top,detail);$("standings").appendChild(item);
  });
  if (!view) $("standings").textContent="—";
  const found=selected(view);
  if (view && state.selected!==null) {
    const known=own<0||!!(BigInt("0x"+player.seen)&(1n<<BigInt(state.selected)));
    const lines=[`${String.fromCharCode(65+state.selected%8)}${Math.floor(state.selected/8)+1} · ${known?view.terrain[state.selected]:"Unexplored"}`];
    if (known && found.cityAt) lines.push(`⌂ ${view.players[found.cityAt.owner].call} city · pop ${found.cityAt.pop} · defense ${found.cityAt.hp}`);
    if (known && found.unitAt) lines.push(`◈ ${view.players[found.unitAt.owner].call} ${UNITS[found.unitAt.kind]} · HP ${found.unitAt.hp}`);
    if (state.moveOrigin!==null) lines.push("Choose an adjacent hex to move / attack");
    $("inspector").textContent=lines.join("\n");
  } else $("inspector").textContent="Select a tile on the map.";
  $("move").disabled=!canAct(view)||found.unit<0;
  $("found").disabled=!canAct(view)||found.unit<0||view.units[found.unit].kind!==2;
  $("end").disabled=!view||view.finished||state.config?.observer||
    !(state.config?.local||view.turn===call)||current?.pending_move;
  const production=$("production"); production.replaceChildren();
  UNITS.forEach((name,i)=>{
    const b=document.createElement("button");b.textContent=`＋ ${name}`;
    b.disabled=!canAct(view)||found.city<0;
    b.title=`Costs ${UNIT_COSTS[i].join(" / ")} food/timber/ore`;
    b.onclick=()=>action([82,found.city,i]); production.appendChild(b);
  });
  BUILDINGS.forEach((name,i)=>{
    const b=document.createElement("button");b.textContent=name;
    b.disabled=!canAct(view)||found.city<0;
    b.title=`Costs ${BUILD_COSTS[i].join(" / ")} food/timber/ore`;
    b.onclick=()=>action([66,found.city,i]); production.appendChild(b);
  });
  const grow=document.createElement("button");grow.textContent="↑ Grow city";
  grow.disabled=!canAct(view)||found.city<0;
  grow.onclick=()=>action([71,found.city]);production.appendChild(grow);
  const techs=$("technology");techs.replaceChildren();
  TECHS.forEach((name,i)=>{
    const known=player&&!!(player.tech&(1<<i));
    const b=document.createElement("button");b.textContent=`${known?"✓ ":""}${name} · ${TECH_COSTS[i]}`;
    b.disabled=!canAct(view)||known||!player;
    b.onclick=()=>action([84,i]);techs.appendChild(b);
  });
  drawMap(view);
}

async function refresh(force=false) {
  if (state.refreshing) return;
  state.refreshing=true;
  try {
    if (!state.config||force) state.config=await api("/api/config");
    state.sessions=await api("/api/sessions");
    if (!state.sessions.some(s=>s.id===state.sid)) {
      state.sid=state.sessions[0]?.id||null;
      state.selected=null;state.moveOrigin=null;state.seenChats.clear();state.lastRevision=null;
    }
    state.session=state.sid?await api(`/api/sessions/${state.sid}`):null;
    const row=state.session;
    if (row?.last_move && row.revision!==state.lastRevision && state.lastRevision!==null)
      note(`${row.last_move.actor} · ${row.last_move.description}`);
    state.lastRevision=row?.revision??null;
    if (row && row.status!=="lobby") {
      const messages=await api(`/api/sessions/${row.id}/chat`);
      for (const message of messages) {
        const key=`${row.id}:${message.sender}:${message.sequence}`;
        if (!state.seenChats.has(key)) {
          state.seenChats.add(key);
          note(`${message.sender}: ${message.body}`);
        }
      }
    }
    render();
  } catch (error) { note(error.message,true); }
  finally { state.refreshing=false; }
}

$("create").addEventListener("click",async()=>{
  const result=await send("/api/campaigns",{});
  if (result) {state.sid=result.session.id;state.selected=null;state.moveOrigin=null;refresh(true);}
});
for (const [button,op] of [["join","join"],["start","start"],["sync","sync"],
                           ["retry","retry"],["announce","announce"]])
  $(button).addEventListener("click",()=>state.sid&&send(endpoint(op)));
$("setOrder").addEventListener("click",()=>state.sid&&send(endpoint("order"),{callsigns:$("roster").value}));
$("move").addEventListener("click",()=>{
  if (!state.session?.view||state.selected===null) return;
  state.moveOrigin=state.selected;
  render();
});
$("found").addEventListener("click",()=>{
  const unit=selected(state.session?.view).unit;
  if (unit>=0) action([70,unit]);
});
$("end").addEventListener("click",()=>action([69]));
$("chatForm").addEventListener("submit",async event=>{
  event.preventDefault();
  if (!state.sid||!$("chatText").value.trim()) return;
  const result=await send(endpoint("chat"),{text:$("chatText").value});
  if (result) $("chatText").value="";
});

refresh(true);
setInterval(()=>refresh(false),1200);
