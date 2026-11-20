// Run only against a disposable local practice server on port 8765.
const {chromium}=require("playwright");
(async()=>{
 const browser=await chromium.launch({headless:true,args:["--no-sandbox"]});
 const page=await browser.newPage({viewport:{width:1440,height:900}});
 const errors=[];page.on("pageerror",e=>errors.push(e.message));
 await page.goto("http://127.0.0.1:8765/");
 await page.waitForFunction(()=>state.snapshot?.ready&&state.snapshot.transport==="practice");
 async function create(game){
   await page.evaluate(game=>sendCommand("create_game",{game}),game);
   await page.waitForFunction(game=>state.session?.game?.toLowerCase().replace(/ /g,"-")===game||
     state.session?.id&&state.snapshot?.sessions.find(s=>s.id===state.session.id)?.game_id===state.snapshot.catalog.find(g=>g.key===game)?.game_id,game);
 }
 await create("tic-tac-toe");
 await page.locator(".ttt-cell").first().click();
 await page.waitForFunction(()=>state.session?.revision>=2);
 const cue=await page.evaluate(()=>state.session.last_opponent_cue);
 if(!cue||cue.actor!=="ARCADE-AI"||cue.indices.length!==1)throw Error("opponent cue missing: "+JSON.stringify(cue));
 await page.locator("#replay-move-button").click();
 const marked=page.locator(".ttt-cell.move-cue-target .move-wave");
 await marked.waitFor();
 if((await page.locator(".ttt-cell.move-cue-target").count())!==1)throw Error("generic move not targeted");
 const before=await page.evaluate(()=>state.session.revision);
 await page.locator("#replay-move-button").click();
 if(await page.evaluate(()=>state.session.revision)!==before)throw Error("replay mutated match");
 await create("connect-four");
 await page.getByRole("button",{name:"Drop into column 4"}).click();
 await page.waitForFunction(()=>state.session?.revision>=2);
 await page.locator("#replay-move-button").click();
 await page.locator(".four-cell.move-cue-target .move-wave").waitFor();
 await create("battleship-classic");
 await page.locator("#fleetRandomize").count().catch(()=>{});
 await page.locator(".bs-placement-grid").waitFor();
 await page.getByRole("button",{name:"Randomize",exact:true}).click();
 await page.getByRole("button",{name:"Deploy fleet"}).click();
 await page.waitForFunction(()=>state.session?.view?.phase==="call");
 const target=page.locator('.battleship-grid[data-move-board="enemy"] button:not(:disabled)').first();
 await target.click();
 await page.waitForFunction(()=>state.session?.last_opponent_cue?.board==="incoming");
 const navy=await page.evaluate(()=>state.session.last_opponent_cue);
 await page.locator("#replay-move-button").click();
 const idx=navy.indices[0];
 await page.locator('.battleship-grid[data-move-board="incoming"] [data-move-cell="'+idx+'"] .move-wave').waitFor();
 const label=await page.locator(".move-popover").textContent();
 if(!label.includes("ARCADE-AI"))throw Error("incoming shot label absent");
 if(!["miss","hit","sunk","incoming"].includes(navy.outcome))throw Error("unexpected shot result");
 if(errors.length)throw Error(errors.join("\n"));
 console.log("PASS: opponent TTT, Connect Four, and incoming Battleship ripples with manual replay and unchanged game revisions");
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
