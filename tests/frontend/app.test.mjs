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
  showModal(){this.open=true;}
  focus(){}
  remove(){}
}
const release={dataset_version:'d',feature_rule_version:'r',snapshot_sha256:'s',manifest_sha256:'m'};
const algorithms=[{method:'topsis',algorithm_version:'topsis-1.0.0'},{method:'hybrid_rrf',algorithm_version:'hybrid-rrf-1.0.0-experimental'}];
const weights={weight_g:2,storage_gb:3,screen_size_inch:1,battery_capacity_mah:2,chipset_tier:2,dxomark_photo:0,honor_of_kings_avg_fps:0};
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const discoveryRows=Array.from({length:6},(_,index)=>({sku_id:`S${index}`,model_id:`M${index}`,display_name:`真实目录机型${index}`,brand:'测试品牌',ram_raw:'12GB',storage_raw:'256GB',storage_gb:256,price_cny:2000+index*100,condition:'new',observed_at:'2026-01-01',image:{url:`/media/model_${index}.webp`,alt:`真实目录机型${index}展示图`}}));
let ordinal=0;
async function harness({offline=false,catalogTotal=6,catalogModels=6,holdCatalog=false}={}){
  const html=await readFile(new URL('../../frontend/index.html',import.meta.url),'utf8'),ids=new Map(),groups={};
  for(const match of html.matchAll(/<(\w+)\b[^>]*>/g)){
    const tag=match[0],node=new Node(match[1]),id=tag.match(/\bid="([^"]+)"/)?.[1];
    if(id){node.id=id;ids.set(id,node);}node.value=tag.match(/\bvalue="([^"]*)"/)?.[1]??'';
    for(const attr of ['view','category','download','not-count']){
      const value=tag.match(new RegExp(`data-${attr}="([^"]+)"`))?.[1];
      if(value){const key=attr.replace(/-([a-z])/g,(_,c)=>c.toUpperCase());node.dataset[key]=value;(groups[attr]??=[]).push(node);}
    }
  }
  for(const [id,values] of Object.entries({mode:['new','used','all'],'catalog-mode':['all','new','used'],method:['topsis','hybrid_rrf'],'evaluation-mode':['spec_reference','measured'],'game-scope':['honor_of_kings','unconfirmed','unsupported'],'category-status':['evidence_available','pending_review']})){
    const node=ids.get(id);if(!node)continue;node.options=values.map(value=>Object.assign(new Node('option'),{value}));
    const selectBody=html.match(new RegExp(`<select id="${id}"[^>]*>(.*?)</select>`,'s'))?.[1];node.value=selectBody?.match(/value="([^"]+)"/)?.[1]??values[0];
  }
  const outputs=new Map();globalThis.document={getElementById:id=>ids.get(id),createElement:tag=>new Node(tag),createTextNode:text=>Object.assign(new Node(),{textContent:text}),body:new Node(),querySelector:selector=>{if(!outputs.has(selector))outputs.set(selector,new Node('output'));return outputs.get(selector);},querySelectorAll:selector=>groups[selector.match(/data-([\w-]+)/)?.[1]]??[]};
  const calls=[],pendingCatalog=[];let needsPayload;
  const meta={release,algorithms,reference_rule_version:'spec-reference-v1',metrics:Object.keys(weights),defaults:{weights,mode:'new',target_screen:6.5,evaluation_mode:'spec_reference'},brands:['华为','苹果'],features:{needs_extraction:true,llm:false,offline_fixture:offline},counts:{phone_models:10,skus:20,sources:7,metric_observations:30},source_summary:[{label:'规格资料',count:5},{label:'价格资料',count:2}],acceptance:{},performance_capabilities:{}};
  globalThis.fetch=async(path,options)=>{
    calls.push({path,body:options.body?JSON.parse(options.body):undefined});
    let payload=meta;
    if(path.startsWith('/api/v1/recommend')){const body=JSON.parse(options.body);payload={release,status:'ready',method:body.method,evaluation_mode:body.evaluation_mode,reference_rule_version:body.expected_reference_rule_version,algorithm_version:body.expected_algorithm_version,score_label:body.method==='hybrid_rrf'?'实验融合分':'TOPSIS 相对贴近度',ranked:[],ranked_total:3,page:1,page_size:12,effective_weights:body.weights,unscored:[],backups:[],performance_capabilities:{}};}
    if(path==='/api/v1/needs')payload=needsPayload;
    if(path.startsWith('/api/v1/catalog')){
      const q=new URL(path,'http://localhost').searchParams;
      payload={release,items:discoveryRows,total:catalogTotal,page:Number(q.get('page')??1),page_size:Number(q.get('page_size')??24),
        category:q.get('category')??'all',category_status:q.get('category_status')??'evidence_available',
        category_counts:[{id:'gaming',model_count:catalogModels,evidence_available:3,pending_review:3},
                         {id:'photography',model_count:catalogModels,evidence_available:1,pending_review:5},
                         {id:'senior',model_count:catalogModels,evidence_available:0,pending_review:6}]};
      if(holdCatalog&&q.get('page_size')==='24')return await new Promise(resolve=>pendingCatalog.push({path,payload,respond(){resolve({ok:true,json:async()=>this.payload});}}));
    }
    if(path.startsWith('/api/v1/skus/')){const row=discoveryRows.find(row=>path.endsWith(row.sku_id))??discoveryRows[0];payload={release,model:{display_name:row.display_name,brand:row.brand},sku:row,specifications:[],prices:[{amount:row.price_cny,condition:row.condition,observed_at:row.observed_at}],benchmark_runs:[],observations:[],sources:[]};}
    if(path.includes('/reviews?')){const q=new URL(path,'http://localhost').searchParams,grade=q.get('grade'),page=Number(q.get('page'));payload={release,model:{display_name:discoveryRows[0].display_name},available:true,sample_total:21,total:grade==='bad'?1:21,grade,page,page_size:20,grade_counts:{good:20,bad:1},items:[{username:'淘**友',grade_label:grade==='bad'?'差评':'好评',content:'采集的买家原文',purchase_spec:'256GB'}]};}
    return {ok:true,json:async()=>payload};
  };
  await import(`../../frontend/app.mjs?test=${++ordinal}`);await settle();
  return {ids,groups,calls,pendingCatalog,setNeeds:payload=>{needsPayload=payload;},async input(id,value){ids.get(id).value=String(value);await ids.get('preferences').emit('input',ids.get(id));},async generate(){ids.get('confirmed').checked=true;await ids.get('preferences').emit('input',ids.get('confirmed'));await ids.get('preferences').emit('submit');await settle();}};
}

const catalogCalls=h=>h.calls.filter(c=>c.path.startsWith('/api/v1/catalog?')&&new URL(c.path,'http://localhost').searchParams.get('page_size')==='24');
async function catalogView(h){await h.groups.view.find(n=>n.dataset.view==='catalog').emit('click');await settle();}

test('catalog opens the complete library independently from recommendation budget, mode and brands',async()=>{
  const h=await harness({catalogTotal:484,catalogModels:147});await h.input('max-price',3000);await h.input('mode','new');
  h.ids.get('exclude-brands').options.find(o=>o.value==='苹果').selected=true;
  await h.ids.get('preferences').emit('input',h.ids.get('exclude-brands'));await catalogView(h);
  const query=new URL(catalogCalls(h).at(-1).path,'http://localhost').searchParams;
  assert.equal(query.get('mode'),'all');assert.equal(query.has('max_price'),false);assert.equal(query.has('exclude'),false);
  assert.match(h.ids.get('catalog-status').textContent,/147.*机型.*484.*配置/);
  assert.equal(h.ids.get('max-price').value,'3000');assert.equal(h.ids.get('mode').value,'new');
});

test('catalog reset and own filters preserve confirmed recommendation, export and form',async()=>{
  const h=await harness();await h.input('max-price',3000);await h.generate();
  assert.equal(h.ids.get('confirmed').checked,true);await catalogView(h);
  assert.ok(h.ids.has('catalog-filters'),'catalog-local filter form is missing');
  h.ids.get('catalog-mode').value='used';h.ids.get('catalog-max-price').value='1800';h.ids.get('catalog-min-storage').value='256';
  h.ids.get('catalog-include-brands').options.find(o=>o.value==='华为').selected=true;
  await h.ids.get('catalog-filters').emit('submit');await settle();
  let query=new URL(catalogCalls(h).at(-1).path,'http://localhost').searchParams;
  assert.equal(query.get('mode'),'used');assert.equal(query.get('max_price'),'1800');assert.equal(query.get('min_storage'),'256');
  assert.deepEqual(query.getAll('include'),['华为']);assert.equal(h.ids.get('confirmed').checked,true);
  assert.equal(h.ids.get('downloads').hidden,false);assert.equal(h.ids.get('max-price').value,'3000');
  await h.ids.get('clear-catalog-filters').emit('click');await settle();
  query=new URL(catalogCalls(h).at(-1).path,'http://localhost').searchParams;
  assert.equal(query.get('mode'),'all');assert.equal(query.has('max_price'),false);assert.equal(query.has('include'),false);
  assert.equal(h.ids.get('confirmed').checked,true);assert.equal(h.ids.get('max-price').value,'3000');assert.equal(h.ids.get('mode').value,'new');
});

test('catalog filters survive recommendation edits and returning to the catalog',async()=>{
  const h=await harness();await catalogView(h);assert.ok(h.ids.has('catalog-filters'));
  h.ids.get('catalog-max-price').value='1800';await h.ids.get('catalog-filters').emit('submit');await settle();
  const old=h.ids.get('catalog-results').textContent;
  await h.groups.view.find(n=>n.dataset.view==='recommend').emit('click');await h.input('max-price',6000);
  assert.equal(h.ids.get('catalog-results').textContent,old);
  await catalogView(h);const query=new URL(catalogCalls(h).at(-1).path,'http://localhost').searchParams;
  assert.equal(query.get('max_price'),'1800');assert.equal(query.get('mode'),'all');assert.equal(h.ids.get('max-price').value,'6000');
});

test('catalog pagination uses applied filters while a pending form edit stays unapplied',async()=>{
  const h=await harness({catalogTotal:48});await catalogView(h);assert.ok(h.ids.has('catalog-max-price'));
  h.ids.get('catalog-max-price').value='1800';
  await h.ids.get('catalog-pagination').children.find(n=>n.textContent==='下一页').emit('click');await settle();
  let query=new URL(catalogCalls(h).at(-1).path,'http://localhost').searchParams;
  assert.equal(query.get('page'),'2');assert.equal(query.has('max_price'),false);
  h.ids.get('search').value='华为';await h.ids.get('catalog-search').emit('submit');await settle();
  query=new URL(catalogCalls(h).at(-1).path,'http://localhost').searchParams;
  assert.equal(query.get('page'),'1');assert.equal(query.get('q'),'华为');assert.equal(query.has('max_price'),false);
});

test('recommendation revision does not cancel or reject a pending catalog response',async()=>{
  const h=await harness({holdCatalog:true});await catalogView(h);assert.equal(h.pendingCatalog.length,1);
  await h.input('max-price',3000);h.pendingCatalog[0].respond();await settle();
  assert.match(h.ids.get('catalog-status').textContent,/6.*配置/);assert.ok(h.ids.get('catalog-featured').children.length);
});

test('an older catalog response cannot overwrite a newly filtered response',async()=>{
  const h=await harness({holdCatalog:true});await catalogView(h);assert.ok(h.ids.has('catalog-filters'));
  h.ids.get('catalog-max-price').value='1800';await h.ids.get('catalog-filters').emit('submit');await settle();
  assert.equal(h.pendingCatalog.length,2);
  h.pendingCatalog[1].payload.total=1;h.pendingCatalog[1].payload.category_counts[0].model_count=1;
  h.pendingCatalog[1].respond();await settle();const status=h.ids.get('catalog-status').textContent;
  h.pendingCatalog[0].respond();await settle();assert.equal(h.ids.get('catalog-status').textContent,status);
  assert.match(status,/1.*配置/);
});

test('detail entry loads actual review endpoint, filters, paginates and returns to cached specifications',async()=>{
  const h=await harness(),walk=node=>[node,...node.children.flatMap(walk)];
  const entry=walk(h.ids.get('results')).find(node=>node.tagName==='button'&&node.textContent.includes('详情'));
  assert.ok(entry);await entry.emit('click');await settle();
  assert.equal(h.ids.get('detail-dialog').open,true);assert.equal(h.ids.get('view-buyer-reviews').disabled,false);
  await h.ids.get('view-buyer-reviews').emit('click');await settle();
  assert.match(h.calls.at(-1).path,/\/skus\/S0\/reviews\?page=1&page_size=20&grade=all$/);
  assert.match(h.ids.get('detail-content').textContent,/淘\*\*友/);
  await walk(h.ids.get('detail-content')).find(n=>n.tagName==='button'&&n.textContent==='下一页').emit('click');await settle();
  assert.match(h.calls.at(-1).path,/page=2&page_size=20&grade=all$/);
  await walk(h.ids.get('detail-content')).find(n=>n.tagName==='button'&&n.textContent==='差评 1').emit('click');await settle();
  assert.match(h.calls.at(-1).path,/page=1&page_size=20&grade=bad$/);
  const count=h.calls.length;await h.ids.get('view-buyer-reviews').emit('click');
  assert.equal(h.calls.length,count);assert.equal(h.ids.get('detail-title').textContent,'配置详情与来源');
  assert.match(h.ids.get('detail-content').textContent,/真实目录机型0/);
  await h.ids.get('detail-dialog').emit('close');
});

test('real form submits the sole hybrid algorithm with bound versions',async()=>{
  const h=await harness();await h.generate();let body=h.calls.find(c=>c.path.startsWith('/api/v1/recommend')).body;
  assert.equal(body.method,'hybrid_rrf');assert.equal(body.expected_algorithm_version,'hybrid-rrf-1.0.0-experimental');assert.deepEqual(body.expected_release,release);
  assert.equal(body.evaluation_mode,'spec_reference');assert.equal(body.expected_reference_rule_version,'spec-reference-v1');
  assert.equal(body.weights.dxomark_photo,1);assert.equal(body.weights.honor_of_kings_avg_fps,1);
  assert.ok(body.defaulted_metrics.includes('dxomark_photo'));assert.ok(body.defaulted_metrics.includes('honor_of_kings_avg_fps'));
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

test('specification mode permits game estimates and measured mode blocks unsupported explicit scope',async()=>{
  const h=await harness();await h.input('honor_of_kings_avg_fps',2);await h.generate();
  let body=h.calls.filter(c=>c.path.startsWith('/api/v1/recommend')).at(-1).body;
  assert.equal(body.game_scope,'unconfirmed');
  assert.equal(body.evaluation_mode,'spec_reference');assert.equal(h.ids.get('performance-blocker').textContent,'');
  assert.equal(body.defaulted_metrics.includes('honor_of_kings_avg_fps'),false);
  await h.input('evaluation-mode','measured');assert.equal(h.ids.get('downloads').hidden,true);assert.equal(h.ids.get('confirmed').checked,false);
  const before=h.calls.filter(c=>c.path.startsWith('/api/v1/recommend')).length;await h.generate();
  assert.equal(h.calls.filter(c=>c.path.startsWith('/api/v1/recommend')).length,before);
  assert.match(h.ids.get('performance-blocker').textContent,/游戏范围/);
  await h.input('game-scope','honor_of_kings');await h.generate();body=h.calls.filter(c=>c.path.startsWith('/api/v1/recommend')).at(-1).body;
  assert.equal(body.game_scope,'honor_of_kings');assert.equal(body.weights.honor_of_kings_avg_fps,3);assert.equal(body.evaluation_mode,'measured');
});

test('measured mode retains default defer choice and survives an extraction draft',async()=>{
  const h=await harness();await h.input('evaluation-mode','measured');await h.generate();
  let body=h.calls.filter(c=>c.path.startsWith('/api/v1/recommend')).at(-1).body;
  assert.equal(body.evaluation_mode,'measured');assert.equal(body.weights.honor_of_kings_avg_fps,1);
  assert.ok(body.defaulted_metrics.includes('honor_of_kings_avg_fps'));
  h.ids.get('needs-text').value='预算三千，256GB';
  h.setNeeds({status:'ok',notice:'离线测试',extraction:{budget:{reference:null},preferences:[],clarifications:[],unsupported_needs:[]},draft:{confirmed:false,evaluation_mode:'spec_reference',needs:{budget:{min:null,max:3000},min_storage:256,brands:{include:[],exclude:[]}},weights,game_scope:'honor_of_kings',target_screen:6.5}});
  await h.ids.get('natural-needs').emit('submit');await settle();await h.generate();
  body=h.calls.filter(c=>c.path.startsWith('/api/v1/recommend')).at(-1).body;
  assert.equal(h.ids.get('evaluation-mode').value,'measured');assert.equal(body.evaluation_mode,'measured');
  assert.equal(body.needs.budget.max,3000);assert.equal(body.expected_reference_rule_version,'spec-reference-v1');
});

test('offline extraction preview discloses fixture instead of claiming paid provider output',async()=>{
  const h=await harness({offline:true});
  assert.match(h.ids.get('needs-service-notice').textContent,/离线测试/);
  assert.match(h.ids.get('needs-status').textContent,/离线测试/);
  assert.doesNotMatch(h.ids.get('needs-service-notice').textContent,/发送给 DeepSeek/);
});

test('AISM structure is inspectable without per-issue confirmations or preference propagation',async()=>{
  const h=await harness(),walk=node=>[node,...node.children.flatMap(walk)];
  h.ids.get('needs-text').value='最多3000元，游戏最重要';
  const structure={model_version:'aism-seven-dimensions-v1',model_sha256:'a'.repeat(64),basis_type:'project_scoring_dependencies',empirically_validated:false,
    nodes:[{id:'weight',label:'重量'},{id:'storage',label:'存储'},{id:'screen',label:'屏幕尺寸'},
           {id:'photo',label:'拍照'},{id:'game',label:'游戏'},{id:'battery',label:'电池容量'},{id:'chipset',label:'处理器档位'}],
    relations:[{source:'chipset',target:'game'},{source:'battery',target:'game'}],
    up_layers:[['weight','storage','screen','photo','game'],['battery','chipset']],
    down_layers:[['weight','storage','screen','photo','battery','chipset'],['game']]};
  h.setNeeds({status:'ok',notice:'按AISM结构抽取',extraction:{budget:{reference:null},
    preferences:[{criterion:'game',level:'非常重要',evidence:'游戏最重要',verdict:'extracted'}],
    clarifications:[],unsupported_needs:[],semantic_structure:structure},
    draft:{confirmed:false,needs:{budget:{min:null,max:3000},min_storage:null,brands:{include:[],exclude:[]}},
      weights:{...weights,honor_of_kings_avg_fps:9},target_screen:6.5}});
  await h.ids.get('natural-needs').emit('submit');await settle();
  const panel=h.ids.get('needs-result'),nodes=walk(panel);
  assert.match(panel.textContent,/处理器档位 → 游戏/);assert.match(panel.textContent,/电池容量 → 游戏/);
  assert.match(panel.textContent,/UP/);assert.match(panel.textContent,/DOWN/);
  assert.match(panel.textContent,/尚未|未经/);
  assert.equal(nodes.filter(n=>n.tagName==='input'&&n.type==='checkbox').length,0);
  const detail=nodes.find(n=>n.tagName==='details'&&n.textContent.includes('查看维度关系（AISM）'));
  assert.ok(detail);assert.equal(detail.open,false);
  await h.generate();const body=h.calls.filter(c=>c.path.startsWith('/api/v1/recommend')).at(-1).body;
  assert.equal(body.weights.honor_of_kings_avg_fps,9);assert.equal(body.weights.chipset_tier,2);
  assert.equal(body.weights.battery_capacity_mah,2);assert.equal(body.needs.budget.max,3000);
});

 test('each real slider submits its mapped weight and any change revokes confirmation',async()=>{
  const h=await harness();
  for(const [key,want] of Object.entries({storage_gb:9,weight_g:9,battery_capacity_mah:9,chipset_tier:9,screen_size_inch:8,dxomark_photo:9,honor_of_kings_avg_fps:9})){
    await h.input(key,4);await h.generate();const body=h.calls.filter(c=>c.path.startsWith('/api/v1/recommend')).at(-1).body;assert.equal(body.weights[key],want);assert.equal(body.weights.price_cny,undefined);assert.equal(body.weights.refresh_rate_hz,undefined);
    await h.input(key,1);assert.equal(h.ids.get('confirmed').checked,false);
  }
 });

test('first load shows catalog-backed discovery and all four views without confirming or ranking',async()=>{
  const h=await harness();assert.equal(h.ids.get('results-title').textContent,'先看看这些机型');
  assert.equal(h.ids.get('results').children.length,3);assert.match(h.ids.get('results').textContent,/真实目录机型0/);
  assert.equal(h.ids.get('confirmed').checked,false);assert.equal(h.calls.some(call=>/\/recommend|\/needs$/.test(call.path)),false);
  assert.equal(h.ids.get('hero-phones').children[0].children.length,5);
  await h.groups.view.find(node=>node.dataset.view==='data').emit('click');
  assert.equal(h.ids.get('data-view').hidden,false);assert.equal(h.ids.get('recommend-workspace').hidden,true);
  assert.match(h.ids.get('data-content').textContent,/规格资料.*5 条/);assert.match(h.ids.get('data-content').textContent,/价格资料.*2 条/);
  await h.groups.view.find(node=>node.dataset.view==='catalog').emit('click');await settle();
  assert.equal(h.ids.get('catalog-featured').children[0].dataset.skuId,'S0');assert.equal(h.ids.get('catalog-results').children.length,5);
  assert.match(h.ids.get('catalog-results').textContent,/真实目录机型4/);
});

test('seven five-position controls keep exactly five initially collapsed and sidebar stays visible after ranking',async()=>{
  const html=await readFile(new URL('../../frontend/index.html',import.meta.url),'utf8');
  const collapsed=html.match(/<details id="more-preferences"([^>]*)>(.*?)<\/details>/s);
  assert.ok(collapsed);assert.doesNotMatch(collapsed[1],/\bopen\b/);
  assert.equal([...collapsed[2].matchAll(/type="range"/g)].length,5);
  assert.equal([...html.matchAll(/type="range"/g)].length,7);
  for(const id of ['storage_gb','weight_g'])assert.ok(html.indexOf(`id="${id}"`)<html.indexOf('id="more-preferences"'));
  const h=await harness();await h.generate();assert.equal(h.ids.get('preferences-panel').open,true);assert.equal(h.ids.get('more-preferences').open,false);
});
