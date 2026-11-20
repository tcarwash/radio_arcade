// Run against tests/browser/naval_fixture_server.py only.
const {chromium}=require("playwright");
(async()=>{
 const browser=await chromium.launch({headless:true,args:["--no-sandbox"]});
 const page=await browser.newPage({viewport:{width:1440,height:900}});
 const errors=[];page.on("pageerror",e=>errors.push(e.message));
 await page.goto("http://127.0.0.1:8765/");
 await page.waitForFunction(()=>state.snapshot?.ready&&state.snapshot.sessions.some(s=>s.game==="Battleship Classic"));
 async function select(game){
   await page.evaluate(game=>{
     const id=state.snapshot.sessions.find(s=>s.game===game)?.id;
     if(!id)throw Error("no "+game);
     selectSession(id);refresh();
   },game);
   await page.waitForFunction(game=>state.session?.game===game,game);
   await page.locator('.bs-board-pair .bs-board-panel').first().waitFor();
   const panels=page.locator('.bs-board-pair .bs-board-panel');
   if(await panels.count()!==2)throw Error("expected two naval board panels");
   const [a,b]=await Promise.all([panels.nth(0).boundingBox(),panels.nth(1).boundingBox()]);
   if(Math.abs(a.y-b.y)>22||b.x-a.x<200)throw Error("wide display is not side by side "+JSON.stringify([a,b]));
   return panels;
 }
 let panels=await select("Battleship Classic");
 const classic=await page.evaluate(()=>state.session.revealed_enemy_ships);
 if(classic.length!==1||classic[0].id!==4||classic[0].cells.join(",")!=="90,91")
   throw Error("Classic ship not disclosed "+JSON.stringify(classic));
 if(await panels.nth(0).locator('.bs-fleet-ship').count()!==5)throw Error("own classic fleet missing");
 if(await panels.nth(1).locator('.bs-fleet-ship').count()!==1)throw Error("sunk classic sprite missing");
 if(await panels.nth(1).locator('.bs-own-sunk').count()!==2)throw Error("sunk classic markers missing");
 const sprite=panels.nth(1).locator('.bs-fleet-ship').first();
 const imageURL=await sprite.evaluate(el=>getComputedStyle(el).backgroundImage);
 if(!imageURL.includes("battleship-patrol-horizontal.png"))throw Error("wrong hull artwork: "+imageURL);
 const art=await page.request.get("http://127.0.0.1:8765/battleship-patrol-horizontal.png");
 if(!art.ok()||(art.headers()["content-type"]||"").indexOf("png")<0)throw Error("naval asset not being served");
 await page.screenshot({path:"/tmp/naval-classic-wide.png"});
 panels=await select("Battleship: Sea Warfare");
 const sea=await page.evaluate(()=>state.session.revealed_enemy_ships);
 if(sea.length!==1||sea[0].id!==5)throw Error("Sea Warfare ship not disclosed "+JSON.stringify(sea));
 if(await panels.nth(0).locator('.bs-fleet-ship').count()!==6)throw Error("own sea fleet missing");
 if(await panels.nth(1).locator('.bs-fleet-ship').count()!==1)throw Error("sunk sea sprite missing");
 if(await panels.nth(1).locator('.bs-own-sunk').count()!==2)throw Error("sunk sea markers missing");
 await page.screenshot({path:"/tmp/naval-sea-wide.png"});
 await page.setViewportSize({width:760,height:830});
 await page.locator(".bs-board-pair").waitFor();
 const small=page.locator('.bs-board-pair .bs-board-panel');
 const [a,b]=await Promise.all([small.nth(0).boundingBox(),small.nth(1).boundingBox()]);
 if(b.y<a.y+a.height-3)throw Error("narrow view did not stack boards "+JSON.stringify([a,b]));
 await page.screenshot({path:"/tmp/naval-sea-narrow.png"});
 if(errors.length)throw Error(errors.join("\n"));
 console.log("PASS: Classic and Sea Warfare side-by-side wide, stacked narrow, sunk ship sprites, visible hit marks, secure naval art serving");
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
