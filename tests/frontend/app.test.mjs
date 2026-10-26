import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

// Minimal DOM boundary; execute the real app, event handlers, serializer and renderers.
class Node {
  constructor(tag='div'){this.tagName=tag;this.children=[];this.listeners={};this.dataset={};this.attributes={};this.value='';this.checked=false;this.open=false;this.hidden=false;this.disabled=false;this._text='';this.options=[];this.className='';this.classList={add:()=>{}};}
  get selectedOptions(){return this.options.filter(o=>o.selected||o.value===this.value);}
  set value(value){this._value=String(value??'');}
  get value(){return this._value;}
  get childElementCount(){return this.children.length;}
  get childNodes(){return this.children;}
  set textContent(value){this._text=String(value??'');this.children=[];}
  get textContent(){return this._text+this.children.map(n=>n.textContent).join('');}
  append(...nodes){this.children.push(...nodes.map(n=>typeof n==='string'?Object.assign(new Node(),{textContent:n}):n));}
  replaceChildren(...nodes){this.children=[];this._text='';this.append(...nodes);if(this.tagName==='select')this.options=this.children;}
  setAttribute(k,v){this.attributes[k]=v;}
  addEventListener(k,fn){this.listeners[k]=fn;}
  async emit(type,target=this){return this.listeners[type]?.({target,preventDefault(){}});}
  querySelectorAll(){return [];}
  close(){this.open=false;}
  focus(){}
  remove(){}
}
const release={dataset_version:'d',feature_rule_version:'r',snapshot_sha256:'s',manifest_sha256:'m'};
const algorithms=[{method:'topsis',algorithm_version:'topsis-1.0.0'},{method:'hybrid_rrf',algorithm_version:'hybrid-rrf-1.0.0-experimental'}];
const weights={weight_g:2,storage_gb:3,screen_size_inch:1,battery_capacity_mah:2,chipset_tier:2,dxomark_photo:0,honor_of_kings_avg_fps:0};
const settle=()=>new Promise(resolve=>setImmediate(resolve));
let ordinal=0;
async function harness({offline=false}={}){
  const html=await readFile(new URL('../../frontend/index.html',import.meta.url),'utf8'),ids=new Map(),groups={};
  for(const match of html.matchAll(/<(\w+)\b[^>]*>/g)){
    const tag=match[0],node=new Node(match[1]),id=tag.match(/\bid="([^"]+)"/)?.[1];
    if(id){node.id=id;ids.set(id,node);}node.value=tag.match(/\bvalue="([^"]*)"/)?.[1]??'';
    for(const attr of ['view','category','download','not-count']){
      const value=tag.match(new RegExp(`data-${attr}="([^"]+)"`))?.[1];
      if(value){const key=attr.replace(/-([a-z])/g,(_,c)=>c.toUpperCase());node.dataset[key]=value;(groups[attr]??=[]).push(node);}
    }
  }
  for(const [id,values] of Object.entries({mode:['new','used','all'],method:['topsis','hybrid_rrf'],'game-scope':['honor_of_kings','unconfirmed','unsupported']})){
    const node=ids.get(id);if(!node)continue;node.options=values.map(value=>Object.assign(new Node('option'),{value}));
    const selectBody=html.match(new RegExp(`<select id="${id}"[^>]*>(.*?)</select>`,'s'))?.[1];node.value=selectBody?.match(/value="([^"]+)"/)?.[1]??values[0];
  }
  const outputs=new Map();globalThis.document={getElementById:id=>ids.get(id),createElement:tag=>new Node(tag),createTextNode:text=>Object.assign(new Node(),{textContent:text}),body:new Node(),querySelector:selector=>{if(!outputs.has(selector))outputs.set(selector,new Node('output'));return outputs.get(selector);},querySelectorAll:selector=>groups[selector.match(/data-([\w-]+)/)?.[1]]??[]};
  const calls=[];let needsPayload;
  const meta={release,algorithms,metrics:Object.keys(weights),defaults:{weights,mode:'new',target_screen:6.5},brands:['华为','苹果'],features:{needs_extraction:true,llm:false,offline_fixture:offline},counts:{phone_models:10,skus:20},acceptance:{},performance_capabilities:{}};
  globalThis.fetch=async(path,options)=>{
    calls.push({path,body:options.body?JSON.parse(options.body):undefined});
    let payload=meta;
    if(path.startsWith('/api/v1/recommend')){const body=JSON.parse(options.body);payload={release,status:'ready',method:body.method,algorithm_version:body.expected_algorithm_version,score_label:body.method==='hybrid_rrf'?'实验融合分':'TOPSIS 相对贴近度',ranked:[],ranked_total:3,page:1,page_size:12,effective_weights:body.weights,unscored:[],backups:[],performance_capabilities:{}};}
    if(path==='/api/v1/needs')payload=needsPayload;
    return {ok:true,json:async()=>payload};
  };
  await import(`../../frontend/app.mjs?test=${++ordinal}`);await settle();
  return {ids,groups,calls,setNeeds:payload=>{needsPayload=payload;},async input(id,value){ids.get(id).value=String(value);await ids.get('preferences').emit('input',ids.get(id));},async generate(){ids.get('confirmed').checked=true;await ids.get('preferences').emit('input',ids.get('confirmed'));await ids.get('preferences').emit('submit');await settle();}};
}

test('real form submits default then explicit hybrid with bound version and original release fields',async()=>{
  const h=await harness();await h.generate();let body=h.calls.find(c=>c.path.startsWith('/api/v1/recommend')).body;
  assert.equal(body.method,'topsis');assert.equal(body.expected_algorithm_version,'topsis-1.0.0');assert.deepEqual(body.expected_release,release);
  await h.input('method','hybrid_rrf');assert.equal(h.ids.get('downloads').hidden,true);await h.generate();body=h.calls.filter(c=>c.path.startsWith('/api/v1/recommend')).at(-1).body;
  assert.equal(body.method,'hybrid_rrf');assert.equal(body.expected_algorithm_version,'hybrid-rrf-1.0.0-experimental');assert.equal(body.weights.battery_mixed_usage_duration_min,undefined);
  h.ids.get('needs-text').value='新的需求';await h.ids.get('needs-text').emit('input');assert.equal(h.ids.get('downloads').hidden,true);assert.equal(h.ids.get('confirmed').checked,false);
  const before=h.calls.length;await h.groups.download[0].emit('click');assert.equal(h.calls.length,before,'text edit must revoke export of the old confirmed request');
});

test('real extraction and explicit not-count action retain unsupported intent and hard filters',async()=>{
  const h=await harness();h.ids.get('needs-text').value='预算3000以内，爱打和平精英，续航最重要';
  h.setNeeds({status:'ok',notice:'离线测试',extraction:{budget:{reference:null},preferences:[{criterion:'game',level:'重要',evidence:'爱打和平精英',verdict:'inferred'}],game_use:{names:['和平精英'],game_scope:'unsupported'},clarifications:[],unsupported_needs:[]},draft:{confirmed:false,needs:{budget:{min:null,max:3000},min_storage:256,brands:{include:['华为'],exclude:[]}},weights:{...weights,honor_of_kings_avg_fps:6},desired_weights:{...weights,honor_of_kings_avg_fps:6},game_scope:'unsupported',target_screen:null}});
  await h.ids.get('natural-needs').emit('submit');await settle();
  await h.generate();let body=h.calls.filter(c=>c.path.startsWith('/api/v1/recommend')).at(-1).body;
  assert.equal(body.game_scope,'unsupported');assert.equal(body.weights.honor_of_kings_avg_fps,6);assert.equal(body.weights.battery_capacity_mah,2);assert.equal(body.desired_weights,undefined);
  await h.groups['not-count'].find(n=>n.dataset.notCount==='honor_of_kings_avg_fps').emit('click');
  assert.match(h.ids.get('needs-result').textContent,/爱打和平精英/);assert.equal(h.ids.get('confirmed').checked,false);
  await h.generate();body=h.calls.filter(c=>c.path.startsWith('/api/v1/recommend')).at(-1).body;
  assert.equal(body.weights.honor_of_kings_avg_fps,0);assert.equal(body.weights.battery_mixed_usage_duration_min,undefined);assert.equal(body.game_scope,'unsupported');assert.equal(body.needs.budget.max,3000);assert.equal(body.needs.min_storage,256);assert.deepEqual(body.needs.brands.include,['华为']);
});

test('manual game importance requires an explicit measured-game scope choice',async()=>{
  const h=await harness();await h.input('honor_of_kings_avg_fps',2);await h.generate();
  let body=h.calls.filter(c=>c.path.startsWith('/api/v1/recommend')).at(-1).body;
  assert.equal(body.game_scope,'unconfirmed');
  await h.input('game-scope','honor_of_kings');await h.generate();body=h.calls.filter(c=>c.path.startsWith('/api/v1/recommend')).at(-1).body;
  assert.equal(body.game_scope,'honor_of_kings');assert.equal(body.weights.honor_of_kings_avg_fps,3);
});

test('offline extraction preview discloses fixture instead of claiming paid provider output',async()=>{
  const h=await harness({offline:true});
  assert.match(h.ids.get('needs-service-notice').textContent,/离线测试/);
  assert.match(h.ids.get('needs-status').textContent,/离线测试/);
  assert.doesNotMatch(h.ids.get('needs-service-notice').textContent,/发送给 DeepSeek/);
});

 test('each real slider submits its mapped weight and any change revokes confirmation',async()=>{
  const h=await harness();
  for(const [key,want] of Object.entries({storage_gb:9,weight_g:9,battery_capacity_mah:9,chipset_tier:9,screen_size_inch:8,dxomark_photo:9,honor_of_kings_avg_fps:9})){
    await h.input(key,4);await h.generate();const body=h.calls.filter(c=>c.path.startsWith('/api/v1/recommend')).at(-1).body;assert.equal(body.weights[key],want);assert.equal(body.weights.price_cny,undefined);assert.equal(body.weights.refresh_rate_hz,undefined);
    await h.input(key,1);assert.equal(h.ids.get('confirmed').checked,false);
  }
 });
