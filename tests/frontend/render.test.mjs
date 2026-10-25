import test from 'node:test';
import assert from 'node:assert/strict';
import * as rendering from '../../frontend/render.mjs';
const {renderCard,renderDetail}=rendering;

class Node {
  constructor(tag){this.tagName=tag;this.children=[];this.attributes={};this.dataset={};this.listeners={};this.className='';this.open=false;this._text='';this.classList={add:(name)=>{this.className+=` ${name}`;}};}
  set textContent(text){this._text=String(text);this.children=[];}
  get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
  get childNodes(){return this.children;}
  append(...nodes){this.children.push(...nodes.map(n=>typeof n==='string'?Object.assign(new Node('#text'),{textContent:n}):n));}
  replaceChildren(...nodes){this.children=[];this._text='';this.append(...nodes);}
  addEventListener(name,fn){this.listeners[name]=fn;}
  setAttribute(name,value){this.attributes[name]=String(value);}
}
globalThis.document={createElement:tag=>new Node(tag),createTextNode:text=>Object.assign(new Node('#text'),{textContent:text})};
const walk=node=>[node,...node.children.flatMap(walk)];
const byClass=(node,name)=>walk(node).filter(n=>n.className.split(' ').includes(name));
const row={sku_id:'S1',display_name:'测试机',brand:'品牌',storage_raw:'256GB',price_cny:1000,condition:'new',score:0.8};
const reason={summary:'按你确认的预算与存储来选择。',matches:['记录价格1000元。'],tradeoffs:['不是本次最轻的一款。'],limitations:['没有真实续航测试。'],fact_ids:['F1']};

test('consumer prose is visible and technical explanation is collapsed separately',()=>{
  const card=renderCard(row,{reason,explanation:'TECHNICAL_JSON snapshot_sha256=abc'});
  const prose=byClass(card,'recommendation-reason')[0];
  assert.ok(prose,'missing consumer explanation section');
  assert.match(prose.textContent,/按你确认的预算/);assert.match(prose.textContent,/不是本次最轻/);
  assert.doesNotMatch(prose.textContent,/TECHNICAL_JSON|snapshot_sha256|TOPSIS|F1/);
  const technical=walk(card).find(n=>n.tagName==='details'&&n.children.some(c=>c.tagName==='summary'&&c.textContent==='查看数据与计算依据'));
  assert.ok(technical);assert.equal(technical.open,false);assert.match(technical.textContent,/snapshot_sha256/);
  assert.match(technical.textContent,/TOPSIS/);
});

test('category tags mean evidence availability not certified quality',()=>{
  const categories=[{id:'gaming',label:'游戏手机',status:'evidence_available',reason:'有王者荣耀评测资料'},
    {id:'senior',label:'长辈友好',status:'pending_review',reason:'尚未核实简易模式'}];
  const card=renderCard({...row,category_assessments:categories});
  assert.match(card.textContent,/游戏/);assert.match(card.textContent,/资料/);
  assert.doesNotMatch(card.textContent,/适合老人|游戏流畅|性能优秀/);
});

test('data remains text and no user-facing reason is invented without a bound reason',()=>{
  const card=renderCard({...row,display_name:'<img src=x onerror=alert(1)>'},{explanation:'RAW TECH'});
  assert.equal(walk(card).filter(n=>n.tagName==='img').length,0);
  assert.equal(byClass(card,'recommendation-reason').length,0);
});

test('detail shows pending category reasons even with no observations',()=>{
  const node=renderDetail({model:{display_name:'测试机'},sku:{...row,category_assessments:[
    {id:'senior',label:'长辈友好',status:'pending_review',reason:'尚未核实简易模式',evidence_refs:[],rule_version:'v1'}]},
    specifications:[],prices:[],benchmark_runs:[],observations:[],sources:[]});
  assert.match(node.textContent,/尚未核实简易模式/);assert.match(node.textContent,/待评估/);
});

test('hybrid card and decision use their bound score label without promising accuracy',()=>{
  const card=renderCard(row,{method:'hybrid_rrf',scoreLabel:'实验融合分'});
  assert.match(card.textContent,/实验融合分 0.8000/);assert.doesNotMatch(card.textContent,/TOPSIS 相对贴近度/);
  const decision=rendering.renderDecision({results:{status:'ready',ranked_total:3,reason:'可比',method:'hybrid_rrf',score_label:'实验融合分',algorithm_version:'hybrid-v1',effective_weights:{price_cny:1},unscored:[],backups:[]}});
  assert.match(decision.textContent,/实验/);assert.match(decision.textContent,/theta=1/);assert.match(decision.textContent,/未证明.*更准确/);assert.doesNotMatch(decision.textContent,/TOPSIS 分数/);
});

test('performance summary distinguishes user importance, inferred evidence and default coverage',()=>{
  assert.equal(typeof rendering.renderPerformance,'function');
  const capabilities={honor_of_kings_avg_fps:{label:'王者荣耀平均帧率',eligible_model_count:7,eligible_sku_count:9,scope:'仅王者荣耀',reason:'同条件'}};
  const intent={extraction:{game_use:{names:['和平精英'],game_scope:'unsupported'},preferences:[{criterion:'game',level:'重要',verdict:'inferred',evidence:'爱打和平精英',needs_confirmation:true}]}};
  const node=rendering.renderPerformance(capabilities,{honor_of_kings_avg_fps:2},intent,false);
  assert.match(node.textContent,/默认新机/);assert.match(node.textContent,/当前条件待验证/);assert.match(node.textContent,/7.*9/);assert.match(node.textContent,/推断/);assert.match(node.textContent,/爱打和平精英/);assert.match(node.textContent,/和平精英/);
  assert.doesNotMatch(node.textContent,/当前条件有 7/);
  const current=rendering.renderPerformance(capabilities,{honor_of_kings_avg_fps:0},intent,true);
  assert.match(current.textContent,/不参与排序/);assert.match(current.textContent,/本次已计算条件/);assert.doesNotMatch(current.textContent,/默认新机/);
});
