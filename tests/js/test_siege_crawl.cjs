// Headless gameplay checks: node tests/js/test_siege_crawl.cjs
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('radio_arcade/web_assets/siege-crawl.html', 'utf8').split('<script>')[1].split('</script>')[0];
let saved;
function boot(initial) {
  saved = initial;
  const element = () => ({append(){}, replaceChildren(){}, setAttribute(){}, classList:{add(){}}});
  const context = vm.createContext({assert, document:{getElementById:element, createElement:element}, localStorage:{getItem:()=>saved, setItem:(_, value)=>{saved=value}}});
  vm.runInContext(source, context);
  return code => vm.runInContext(code, context);
}
let run = boot();
run(`
assert.equal(catalog.length,60);
for(const [tier,count] of Object.entries({common:24,uncommon:18,rare:12,legendary:6}))assert.equal(catalog.filter(c=>c.rarity===tier).length,count);
assert.equal(new Set(catalog.map(c=>c.name)).size,60);
for(let id=0;id<60;id++){start();enter(false);s.run.deck[0]=id;play(0);assert.ok(Number.isFinite(s.run.hp));assert.ok(Number.isFinite(s.run.enemy));}
start();enter(false);s.run.deck[0]=10;play(0);assert.equal(s.run.drone,1);assert.equal(s.run.enemy,10);
s.run.phase='route';enter(false);assert.equal(s.run.drone,0);
s.run.deck[0]=16;play(0);assert.equal(s.run.scrap,3);
s.run.deck[0]=30;s.run.enemy=100;play(0);assert.equal(s.run.scrap,0);assert.equal(s.run.enemy,90);
start();s.run.floor=12;enter(false);s.run.phase='reward';
let choices=offers();assert.equal(new Set(choices).size,3);assert.equal(catalog[choices[2]].rarity,'legendary');assert.equal(offers(),choices);
reward(choices[2]);assert.equal(s.run.floor,13);assert.equal(s.run.power,1);assert.equal(s.bestDepth,12);
let depth=s.run.floor;reward(choices[2]);assert.equal(s.run.floor,depth);
`);
run = boot(JSON.stringify({version:1,collection:[0,1,2,7],wins:1,bestDepth:12,run:{floor:12,hp:20,shield:0,focus:0,deck:[0,0,1,1,2,7],phase:'won',enemy:0}}));
run(`assert.equal(s.run.floor,13);assert.equal(s.run.phase,'route');assert.equal(s.run.scrap,0);assert.ok(s.collection.includes(7));enter(false);s.run.phase='reward';offers();draw();`);
const snapshot = saved;
run = boot(snapshot);
run(`assert.equal(s.run.offers.length,3);assert.equal(s.run.phase,'reward');`);
assert.deepEqual(JSON.parse(saved).run.offers,JSON.parse(snapshot).run.offers);
console.log('60 cards, effects, reward persistence, migration and endless progression passed');
run(`
start();assert.equal(Math.max(...catalog.map(c=>s.run.deck.filter(id=>id===c.id).length)),2);
s.copies[3]=1;
for(let i=0;i<3;i++){enter(false);s.run.phase='reward';s.run.offers=[3,4,5];reward(3)}
assert.equal(s.copies[3],2);assert.equal(s.salvage,2);assert.equal(s.run.deck.filter(id=>id===3).length,2);
`);
console.log('Two-copy ownership, deck limits and overflow salvage passed');
