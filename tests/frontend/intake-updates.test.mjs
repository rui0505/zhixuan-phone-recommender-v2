import test from 'node:test';
import assert from 'node:assert/strict';
const module=await import('../../frontend/catalog-updates.mjs').catch(()=>({}));
const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
function environment(){const events=new Map(),timers=new Map();let channel,id=0,clock=0;return {events,timers,get channel(){return channel;},advance(ms){clock+=ms;for(const [key,fn] of [...timers]){timers.delete(key);fn();}},deps:{windowTarget:{addEventListener:(name,fn)=>events.set(name,fn),removeEventListener:name=>events.delete(name)},BroadcastChannelClass:class {constructor(name){this.name=name;channel=this;}addEventListener(name,fn){this.listener=fn;}close(){this.closed=true;}},now:()=>clock,setTimer:fn=>{timers.set(++id,fn);return id;},clearTimer:id=>timers.delete(id),throttleMs:1000}};}
test('catalog update watcher is safe without a browser window',()=>{assert.equal(typeof module.watchCatalogUpdates,'function');assert.equal(typeof module.watchCatalogUpdates(()=>{}, {windowTarget:null}),'function');});
test('publish messages and focus refresh data while coalescing concurrent refreshes',async()=>{
  assert.equal(typeof module.watchCatalogUpdates,'function');const env=environment();let count=0,finish;
  const cleanup=module.watchCatalogUpdates(()=>{count++;return new Promise(resolve=>finish=resolve);},env.deps);
  assert.equal(env.channel.name,'zhixuan-catalog');env.channel.listener({data:{type:'catalog-published'}});await flush();assert.equal(count,1);
  env.events.get('focus')();env.channel.listener({data:{type:'catalog-published'}});await flush();assert.equal(count,1);finish();await flush();assert.equal(env.timers.size,1);env.advance(1000);await flush();assert.equal(count,2);finish();await flush();cleanup();assert.equal(env.channel.closed,true);assert.equal(env.events.has('focus'),false);
});
test('cleanup cancels queued work after a failed refresh',async()=>{
  assert.equal(typeof module.watchCatalogUpdates,'function');const env=environment();let count=0;
  const cleanup=module.watchCatalogUpdates(async()=>{count++;throw new Error('offline');},env.deps);
  env.events.get('focus')();await flush();env.events.get('focus')();assert.equal(env.timers.size,1);cleanup();env.advance(1000);await flush();assert.equal(count,1);
});
test('a failed refresh still permits a later focus refresh',async()=>{
  const env=environment();let count=0;const cleanup=module.watchCatalogUpdates(async()=>{count++;if(count===1)throw new Error('offline');},env.deps);
  env.events.get('focus')();await flush();env.advance(1000);env.events.get('focus')();await flush();assert.equal(count,2);cleanup();
});
