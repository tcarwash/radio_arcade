// First-time-user walkthrough against a fresh isolated practice server, never a production room.
const { chromium } = require("playwright");
(async () => {
  const browser = await chromium.launch({headless:true,args:["--no-sandbox"]});
  const page = await browser.newPage({viewport:{width:1366,height:768}});
  const errors=[];page.on("pageerror",error=>errors.push(error.message));
  await page.goto("http://127.0.0.1:8765/");
  await page.locator("#welcome-dialog[open]").waitFor();
  await page.screenshot({path:"/tmp/review/ra-first-welcome.png"});
  if (!await page.getByRole("button",{name:/Play against the computer/}).isVisible())
    throw Error("No obvious offline practice entry point");
  await page.locator('[data-onboard="practice"]').click();
  await page.waitForFunction(()=>state.snapshot?.ready&&state.snapshot.transport==="practice");
  const library=page.locator("#arena-body .game-library");
  await library.waitFor();
  await page.screenshot({path:"/tmp/review/ra-first-library.png"});
  const card=page.locator("#arena-body .game-library-card").filter({hasText:"Tic-tac-toe"});
  await card.getByRole("button",{name:"Play vs computer"}).click();
  await page.waitForFunction(()=>state.session?.status==="active");
  await page.screenshot({path:"/tmp/review/ra-first-game.png"});
  if (await page.locator(".ttt-cell").count()!==9)throw Error("First game board wasn't visible");
  if (!await page.locator("#turn-banner").isVisible())throw Error("Turn feedback missing");
  await page.locator(".ttt-cell:not(:disabled)").first().click();
  await page.waitForFunction(()=>state.session?.revision>=2);
  const completed=await page.evaluate(()=>state.session.revision);
  if (completed<2)throw Error("First turn was not saved");
  await page.screenshot({path:"/tmp/review/ra-first-played.png"});
  await page.locator("#new-game").click();
  await page.locator("#library-dialog[open]").waitFor();
  await page.locator("#library-dialog .game-library-card").filter({hasText:"Open Word Tiles"})
    .getByRole("button",{name:"How to play"}).click();
  await page.locator(".game-guide li").first().waitFor();
  await page.screenshot({path:"/tmp/review/ra-first-rules.png"});
  if(errors.length)throw Error(errors.join("\n"));
  await browser.close();
  console.log("PASS: fresh onboarding, obvious practice choice, library, immediate playable game, saved first turn, rules discovery");
})().catch(error=>{console.error(error);process.exit(1)});
