import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {mountIntakeAdmin} from '../../frontend/admin.mjs';

class Node{
  constructor(tag='div'){this.tagName=tag;this.children=[];this.listeners={};this.value='';this.hidden=false;this.disabled=false;this.checked=false;this.classList={add:()=>{}};this._text='';if(tag==='select')Object.defineProperty(this,'type',{get:()=> 'select-one'});}
  set textContent(value){this._text=String(value??'');this.children=[];}get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
  append(...children){for(const child of children){child.parentElement=this;this.children.push(child);}}
  replaceChildren(...children){this.children=[];this._text='';this.append(...children);}
  addEventListener(name,fn){this.listeners[name]=fn;}
  async emit(name){return this.listeners[name]?.({target:this,preventDefault(){}});}
  remove(){if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(child=>child!==this);}
}
const release={dataset_version:'phones-1',feature_rule_version:'features-1',snapshot_sha256:'snapshot-1',manifest_sha256:'manifest-1'};
const payload={model:{brand:'测试品牌',display_name:'保存的机型',release_month:'2026-10',source_url:'https://example.org/spec'},variant:{variant_name:'标准版',region:'中国大陆',satellite_support:false,is_default:true},skus:[{ram_gb:12,storage_gb:256,memory_type:null,price:{amount:2999,price_kind:'official_current_price',condition:'new',observed_at:'2026-10-28',source_url:'https://example.org/price',source_note:'官方'}}],specifications:[{key:'weight_g',value_num:200,unit:'g',raw_value:'200g'}],source_note:''};
const record={record_id:'one',revision:3,status:'submitted',created_at:'2026-10-28T00:00:00Z',updated_at:'2026-10-28T00:00:00Z',payload,warnings:[]};
async function harness({location={protocol:'https:',hostname:'example.org'},request:customRequest}={}){
  const html=await readFile(new URL('../../frontend/admin.html',import.meta.url),'utf8'),nodes={};
  for(const match of html.matchAll(/<(\w+)\b[^>]*\bid="([^"]+)"[^>]*>/g))nodes[match[2]]=new Node(match[1]);
  const root={location,defaultView:{confirm:()=>true},getElementById:id=>nodes[id],createElement:tag=>new Node(tag)},calls=[];
  const request=customRequest??(async(path,options={})=>{calls.push({path,options});if(path==='/api/v1/meta')return {release};if(path.includes('/queue'))return {records:[structuredClone(record)],total:1,page:1,page_size:20};return {ok:true,record:structuredClone(record),warnings:[]};});
  const admin=mountIntakeAdmin(root,{request});
  return {root,nodes,admin,calls,async login(){nodes['intake-key'].value='offline-test-maintenance-key-000000';await nodes['intake-login'].emit('submit');},async load(){const button=nodes.queue.children[0].children.at(-1);await button.emit('click');}};
}
function descend(node,predicate){if(predicate(node))return node;for(const child of node.children){const found=descend(child,predicate);if(found)return found;}}
test('page refuses remote HTTP, leaves private workspace hidden and never sends a key',async()=>{
  let calls=0;const {nodes}=await harness({location:{protocol:'http:',hostname:'example.org'},request:async()=>{calls++;}});
  assert.equal(nodes['intake-key'].disabled,true);assert.match(nodes['intake-message'].textContent,/HTTPS/);await nodes['intake-login'].emit('submit');assert.equal(calls,0);
});
test('page clears key input after login and removes queue and form data on logout',async()=>{
  const h=await harness();await h.login();assert.equal(h.nodes['intake-key'].value,'');assert.equal(h.nodes['intake-workspace'].hidden,false);await h.load();assert.equal(h.nodes['display-name'].value,'保存的机型');
  await h.nodes['intake-logout'].emit('click');assert.equal(h.nodes['intake-workspace'].hidden,true);assert.equal(h.nodes.queue.textContent.includes('保存的机型'),false);assert.equal(h.nodes['display-name'].value,'');assert.equal(h.nodes['record-json'].textContent.includes('保存的机型'),false);
});
test('reloading the same revision replaces unsaved fields with the saved record',async()=>{
  const h=await harness();await h.login();await h.load();h.nodes['display-name'].value='未保存修改';await h.nodes['display-name'].emit('input');await h.nodes['reload-record'].emit('click');assert.equal(h.nodes['display-name'].value,'保存的机型');
});
test('unsaved edits revoke source verification and prevent publishing',async()=>{
  const h=await harness();await h.login();await h.load();h.nodes['sources-verified'].checked=true;await h.nodes['sources-verified'].emit('change');assert.equal(h.nodes['publish-record'].disabled,false);
  h.nodes['display-name'].value='修改的机型';await h.nodes['display-name'].emit('input');assert.equal(h.nodes['sources-verified'].checked,false);assert.equal(h.nodes['publish-record'].disabled,true);assert.equal(h.nodes['sources-verified'].disabled,true);
});
test('real form serializes each price source independently and retains unknown numeric blanks',async()=>{
  const h=await harness();await h.login();h.nodes.brand.value='品牌';h.nodes['display-name'].value='新机型';h.nodes['model-source'].value='https://example.org/spec';
  const amount=descend(h.nodes['sku-rows'],node=>node.name==='amount');amount.value='';const source=descend(h.nodes['sku-rows'],node=>node.name==='source_url');source.value='https://example.org/price';
  await h.nodes['save-draft'].emit('click');const saved=h.calls.find(call=>call.path.endsWith('/draft'));
  assert.equal(saved.options.body.model.source_url,'https://example.org/spec');assert.equal(saved.options.body.skus[0].price.source_url,'https://example.org/price');assert.equal(saved.options.body.skus[0].price.amount,null);assert.equal(saved.options.body.skus[0].ram_gb,null);
});
test('invalid numeric input is reported instead of silently changing it to unknown',async()=>{
  const h=await harness();await h.login();const amount=descend(h.nodes['sku-rows'],node=>node.name==='amount');amount.value='';amount.validity={badInput:true};
  await h.nodes['save-draft'].emit('click');assert.equal(h.calls.some(call=>call.path.endsWith('/draft')),false);assert.match(h.nodes['intake-message'].textContent,/数值/);
});
test('queue markup and dangerous source URLs render as inert text',async()=>{
  const hostile=structuredClone(record);hostile.payload.model.display_name='<img src=x onerror=alert(1)>';hostile.payload.model.source_url='javascript:alert(1)';
  const h=await harness({request:async path=>path==='/api/v1/meta'?{release}:path.includes('/queue')?{records:[hostile],total:1,page:1,page_size:20}:{record:hostile}});await h.login();await h.load();
  assert.match(h.nodes.queue.textContent,/<img/);assert.equal(descend(h.nodes.queue,node=>node.tagName==='img'),undefined);assert.equal(descend(h.nodes['record-sources'],node=>node.href?.startsWith('javascript:')),undefined);
});
