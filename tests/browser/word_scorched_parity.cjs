// Run against a disposable, local practice server on port 8765 only (creates two test tables).
const {chromium}=require('playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1366,height:768}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8765/');await page.waitForFunction(()=>state.snapshot?.ready);
 if(!await page.evaluate(()=>state.snapshot.transport==='practice'))throw Error('requires an isolated practice server');
 const create=async(game)=>{await page.evaluate(game=>sendCommand('create_game',{game}),game);};
 await create('open-word-tiles');
 await page.locator('.word-rack-interactive button:not(:disabled)').first().click();
 await page.waitForTimeout(300);
 if(!await page.locator('#word-text').inputValue())throw Error('rack click did not compose');
 await page.getByRole('button',{name:'Clear',exact:true}).click();
 if(await page.locator('#word-text').inputValue())throw Error('clear did not clear');
 await page.getByRole('button',{name:'Shuffle rack'}).click();
 await page.evaluate(async()=>{
   const rack=state.session.view.racks[state.snapshot.callsign].filter(t=>t!=='?'&&t!=='·');
   for(let i=0;i<rack.length;i++)for(let j=0;j<rack.length;j++){
     if(i===j)continue;
     const word=rack[i]+rack[j];
     try{await request('/api/command',{method:'POST',body:JSON.stringify({command:'preview_word',session_id:state.session.id,revision:state.session.revision,start:'H8',direction:'H',word})});
     document.querySelector('#word-text').value=word;document.querySelector('#word-text').dispatchEvent(new Event('input'));return;}catch(e){}
   }
   throw Error('no two-letter opening');
 });
 await page.waitForFunction(()=>document.querySelector('.word-live-score').textContent.includes('Legal word'));
 await page.screenshot({path:'/tmp/word-parity.png'});
 const expected=Number((await page.locator('.word-live-score').textContent()).match(/Legal word · (\d+) points/)[1]);
 const actor=await page.evaluate(()=>state.snapshot.callsign);
 const revision=await page.evaluate(()=>state.session.revision);
 if(!(await page.locator('#word-draft-state').textContent()).includes('DRAFT — NOT SUBMITTED'))throw Error('draft not clearly labeled');
 const draftCell=page.locator('.word-cell.draft-tile').first();
 if(!(await draftCell.evaluate(el=>getComputedStyle(el).borderTopStyle)).includes('dashed'))throw Error('draft tile not dashed');
 if(await page.evaluate(()=>state.session.revision)!==revision)throw Error('draft unexpectedly committed');
 await page.getByRole('button',{name:/Submit word/}).click();
 await page.waitForFunction(revision=>state.session?.revision>revision,revision);
 const actual=await page.evaluate(actor=>state.session.view.scores[actor],actor);
 if(!await page.locator('.word-cell.filled').count())throw Error('submitted tiles did not become permanent');
 if((await page.locator('#word-draft-state').textContent()).includes('DRAFT — NOT SUBMITTED'))throw Error('committed state still appears as draft');
 if(actual!==expected)throw Error('submitted score differs from preview');
 await create('scorched-earth');
 await page.getByRole('button',{name:'Opponent',exact:true}).click();
 if(await page.locator('.tank-follow input').isChecked())throw Error('manual camera should disable follow');
 await page.getByRole('button',{name:'My tank',exact:true}).click();
 await page.getByLabel('Angle in degrees').fill('37');await page.getByLabel('Angle in degrees').press('Tab');
 if(await page.locator('.tank-angle').first().inputValue()!=='37')throw Error('angle not synchronized');
 await page.getByRole('button',{name:/Armory ·/}).click();
 await page.locator('.tank-armory-dialog[open]').waitFor();
 if(await page.locator('.tank-armory-card').count()!==8)throw Error('weapon count mismatch');
 await page.getByRole('button',{name:'Close',exact:true}).last().click();
 await page.screenshot({path:'/tmp/scorched-parity.png'});
 if(errors.length)throw Error(errors.join('\n'));
 console.log('PASS browser: rack composition, clear, shuffle, authoritative score, camera, precise aim, armory');
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
