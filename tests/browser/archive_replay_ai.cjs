// Run against a disposable local practice room on port 8765 only.
const {chromium}=require("playwright");
(async()=>{
 const browser=await chromium.launch({headless:true,args:["--no-sandbox"]});
 const page=await browser.newPage({viewport:{width:1366,height:768}});
 const errors=[];
 page.on("pageerror",e=>errors.push(e.message));
 await page.goto("http://127.0.0.1:8765/");
 await page.waitForFunction(()=>state.snapshot?.ready && state.snapshot?.transport==="practice");
 await page.locator("#settings-open").click();
 await page.locator('select[name="ai_difficulty"]').selectOption("challenging");
 await page.locator('#settings-form button[type="submit"]').click();
 await page.waitForFunction(()=>state.snapshot?.ai_difficulty==="challenging");
 await page.locator("#new-game").click();
 await page.locator("#library-dialog[open]").waitFor();
 await page.locator("#library-dialog .game-library-card").filter({hasText:"Tic-tac-toe"})
   .getByRole("button",{name:"Play vs computer"}).click();
 await page.waitForFunction(()=>state.session?.status==="active");
 const sid=await page.evaluate(()=>state.session.id);
 await page.evaluate(sid=>sendCommand("legal_action",{session_id:sid,action:"00"}),sid);
 await page.waitForFunction(()=>state.session?.revision>=2);
 await page.locator("#session-actions summary").click();
 await page.getByRole("button",{name:"Visual replay",exact:true}).click();
 await page.locator(".replay-grid .replay-cell").first().waitFor();
 if(await page.locator(".replay-grid .replay-cell").count()!==9)throw Error("invalid 3x3 replay grid");
 await page.locator(".replay-slider").fill("1");
 if(!(await page.locator(".info-content").textContent()).includes("Revision 1"))throw Error("replay frame not selected");
 await page.getByRole("button",{name:"Play",exact:true}).click();
 await page.waitForTimeout(50);
 if(await page.getByRole("button",{name:"Pause"}).count()!==1)throw Error("replay did not start");
 await page.locator('#info-dialog button[data-close-dialog]').last().click();
 await page.evaluate(sid=>sendCommand("forfeit_game",{session_id:sid}),sid);
 await page.waitForFunction(()=>state.session?.status==="finished");
 if(await page.locator('#match-result').isVisible()){
   await page.locator('#match-result').getByRole("button",{name:"Back to board"}).click();
 }
 await page.locator("#session-actions summary").click();
 await page.getByRole("button",{name:"Archive completed table",exact:true}).click();
 await page.waitForFunction(sid=>!state.snapshot.sessions.some(s=>s.id===sid),sid);
 await page.locator("#history-open").click();
 await page.locator("#info-dialog[open]").waitFor();
 const card=page.locator("#info-content .history-card").filter({hasText:"Tic-tac-toe"}).first();
 if(!(await card.textContent()).includes("Archived"))throw Error("archived result not preserved");
 await card.getByRole("button",{name:"Restore to tables"}).click();
 await page.waitForFunction(sid=>state.snapshot.sessions.some(s=>s.id===sid),sid);
 if(errors.length)throw Error(errors.join("\n"));
 await browser.close();
 console.log("PASS: configurable practice AI, visual replay with playback, non-destructive archive/restore and preserved history");
})().catch(e=>{console.error(e);process.exit(1)});
