// Exercise the Hand player-discovery flow without RF against the synthetic host.
const {chromium}=require("playwright");
(async()=>{
  const browser=await chromium.launch({headless:true,args:["--no-sandbox"]});
  const page=await browser.newPage({viewport:{width:1366,height:900}});
  const errors=[];page.on("pageerror",e=>errors.push(e.message));
  await page.goto("http://127.0.0.1:8765/");
  await page.waitForFunction(()=>state.snapshot?.ready && state.snapshot.supports_hands);
  const cq=page.locator("#cq-panel");
  await cq.locator("summary").first().click();
  await page.locator("#discovery-prefs > summary").click();
  if(await page.locator("#discovery-auto-reply").isChecked())throw Error("Automatic TX must default off");
  await page.locator("#discovery-auto-reply").check();
  await page.locator("#discovery-status").selectOption("looking");
  await page.locator('#discovery-games input[value="nim"]').check();
  await page.locator('#discovery-games input[value="battleship-classic"]').check();
  await page.locator("#discovery-legacy-cq").uncheck();
  await page.locator("#discovery-save").click();
  await page.waitForFunction(()=>state.snapshot?.discovery_auto_reply===true &&
    state.snapshot?.discovery_legacy_cq===false &&
    state.snapshot?.discovery_games?.includes("battleship-classic"),null,{timeout:7000});
  await page.locator("#cq-game").selectOption("battleship-classic");
  await page.locator("#cq-send").click();
  await page.waitForFunction(()=>state.snapshot?.nearby_players?.some(s=>s.callsign==="N0AAA"));
  const row=page.locator("#discovery-nearby .discovery-result").filter({hasText:"N0AAA"});
  if(!(await row.textContent()).includes("Interested"))throw Error("Interest badge missing");
  if(!(await row.textContent()).includes("-8.5 dB"))throw Error("Reception report missing");
  if(!(await row.getAttribute("class")).includes("discovery-bounce-in"))
    throw Error("Newly decoded available player must bounce into the list");
  // 1.25-second refresh must not replace or replay the arrival.
  const arrivalCount=await page.evaluate(()=>state.discoverySeenHands.size);
  await page.waitForTimeout(1600);
  if(await row.evaluate(el=>el.classList.contains("discovery-bounce-in")))
    throw Error("Poll replayed a previously seen Hand animation");
  if(await page.evaluate(()=>state.discoverySeenHands.size)!==arrivalCount)
    throw Error("Poll duplicated the decoded Hand");
  await page.screenshot({path:"/tmp/review/discovery-wide.png"});
  await row.locator("button").click();
  await page.waitForFunction(()=>state.snapshot?.sessions?.some(s=>s.invited?.includes("N0AAA")));
  await page.setViewportSize({width:410,height:760});
  await page.locator('.mobile-tab[data-pane="lobby"]').click();
  await page.locator("#cq-panel").scrollIntoViewIfNeeded();
  await page.screenshot({path:"/tmp/review/discovery-narrow.png"});
  if(errors.length)throw Error(errors.join("\n"));
  await browser.close();
  console.log("PASS: opt-in game matrix, CQ Hand decode, bounce only on arrival, SNR, invite, responsive UI");
})().catch(e=>{console.error(e);process.exit(1)});
