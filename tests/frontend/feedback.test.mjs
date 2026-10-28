import test from 'node:test';
import assert from 'node:assert/strict';
import {FeedbackComposer,renderFeedbackList,mountFeedback,submissionId} from '../../frontend/feedback.mjs';
import {mountFeedbackAdmin,secureAdminLocation} from '../../frontend/feedback-admin.mjs';

class Node{
  constructor(tag='div'){this.tagName=tag;this.children=[];this.listeners={};this.value='';this.hidden=false;this.disabled=false;this.text='';this.dataset={};this.attributes={};}
  set textContent(v){this.text=String(v);this.children=[];}get textContent(){return this.text+this.children.map(n=>n.textContent).join('');}
  append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=[];this.text='';this.append(...nodes);}
  setAttribute(k,v){this.attributes[k]=v;}addEventListener(k,v){this.listeners[k]=v;}focus(){}
  async emit(type){return this.listeners[type]?.({preventDefault(){},target:this});}
}
function dom(ids){const nodes=Object.fromEntries(ids.map(id=>[id,new Node()]));return {nodes,root:{getElementById:id=>nodes[id],createElement:tag=>new Node(tag),querySelector:()=>({value:'4'})}};}
const fields={nickname:'测试',rating:4,category:'suggestion',page:'catalog',content:'希望列表可以更好用。'};
const receipt={id:'test-receipt',status:'pending',created_at:'2026-10-28T00:00:00Z'};
const comment={...fields,id:'one',created_at:receipt.created_at,published_at:receipt.created_at};

test('HTTP contexts without randomUUID still create secure standard version-4 submission ids',()=>{
  const api={getRandomValues:bytes=>{bytes.fill(17);return bytes;}};
  assert.match(submissionId(api),/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
});
test('management key input is allowed only over HTTPS or a loopback local preview',()=>{
  assert.equal(secureAdminLocation({protocol:'http:',hostname:'example.org'}),false);
  assert.equal(secureAdminLocation({protocol:'https:',hostname:'example.org'}),true);
  assert.equal(secureAdminLocation({protocol:'http:',hostname:'127.0.0.1'}),true);
});

test('lost-response retry reuses submission id, a changed draft uses a new one',async()=>{
  let ordinal=0,fail=true;const bodies=[];
  const composer=new FeedbackComposer({makeId:()=>String(++ordinal),post:async body=>{bodies.push(body);if(fail)throw new Error('网络错误');return receipt;}});
  await assert.rejects(composer.submit(fields));fail=false;await composer.submit(fields);
  assert.equal(bodies[0].submission_id,bodies[1].submission_id);
  fail=true;await assert.rejects(composer.submit({...fields,content:'另一份修改后的反馈。'}));
  assert.notEqual(bodies[1].submission_id,bodies[2].submission_id);
});
test('double click does not submit a second request',async()=>{
  let complete,calls=0;const composer=new FeedbackComposer({makeId:()=> 'id',post:body=>{calls++;return new Promise(resolve=>complete=resolve);}});
  const first=composer.submit(fields);await assert.rejects(composer.submit(fields));complete(receipt);await first;assert.equal(calls,1);
});
test('published comments render hostile markup as text and handle real empty state',()=>{
  const {root}=dom([]),list=new Node();
  renderFeedbackList(root,list,[{...comment,nickname:'<img onerror=x>',content:'<script>bad()</script>'}]);
  assert.match(list.textContent,/<script>bad\(\)<\/script>/);assert.match(list.textContent,/<img onerror=x>/);
  const tags=[];function visit(n){tags.push(n.tagName);n.children.forEach(visit);}visit(list);
  assert.equal(tags.includes('script'),false);assert.equal(tags.includes('img'),false);
  renderFeedbackList(root,list,[]);assert.match(list.textContent,/还没有通过审核/);
});
test('opposite moderation actions cannot run simultaneously for one comment',async()=>{
  const {root}=dom([]),list=new Node();let finish,calls=0;
  renderFeedbackList(root,list,[{...comment,status:'pending'}],{admin:true,moderate:()=>{calls++;return new Promise(resolve=>finish=resolve);}});
  const actions=list.children[0].children.at(-1),publish=actions.children[0],reject=actions.children[1];
  const first=publish.emit('click');assert.equal(reject.disabled,true);await reject.emit('click');assert.equal(calls,1);finish();await first;assert.equal(reject.disabled,false);
});
test('public form preserves failed draft, sends pending feedback and shows receipt',async()=>{
  const {root,nodes}=dom(['feedback-form','feedback-nickname','feedback-category','feedback-page','feedback-content','feedback-counter','feedback-fields','feedback-submit','feedback-message','feedback-list','feedback-summary','feedback-pagination','feedback-refresh']);
  for(const [key,value] of Object.entries(fields))if(nodes['feedback-'+key])nodes['feedback-'+key].value=value;
  nodes['feedback-form'].reset=()=>{nodes['feedback-content'].value='';};
  let fail=true;mountFeedback(root,{get:async()=>({items:[],total:0,page:1,page_size:20,average_rating:null}),composer:new FeedbackComposer({makeId:()=> 'id',post:async()=>{if(fail)throw new Error('暂不可用');return receipt;}})});
  await nodes['feedback-form'].emit('submit');assert.equal(nodes['feedback-content'].value,fields.content);assert.match(nodes['feedback-message'].textContent,/暂不可用/);
  fail=false;await nodes['feedback-form'].emit('submit');assert.equal(nodes['feedback-content'].value,'');assert.match(nodes['feedback-message'].textContent,/审核后公开/);assert.match(nodes['feedback-message'].textContent,/test-receipt/);
});
test('admin logout clears private data and ignores an in-flight response',async()=>{
  const {root,nodes}=dom(['admin-login','admin-key','admin-workspace','admin-login-panel','admin-message','admin-list','admin-summary','admin-filter','admin-refresh','admin-logout','admin-export','admin-pagination','admin-login-button']);
  nodes['admin-key'].value='offline-test-admin-key-0000000000000';nodes['admin-filter'].value='pending';
  let finish;mountFeedbackAdmin(root,{request:async()=>new Promise(resolve=>finish=resolve)});
  const login=nodes['admin-login'].emit('submit');assert.equal(nodes['admin-key'].value,'');
  await nodes['admin-logout'].emit('click');finish({items:[comment],total:1,page:1,page_size:20,counts:{pending:1,published:0,rejected:0}});await login;
  assert.equal(nodes['admin-workspace'].hidden,true);assert.equal(nodes['admin-list'].textContent,'');assert.equal(nodes['admin-summary'].textContent,'');
});
test('admin moderates selected comment using authorization and reloads the list',async()=>{
  const {root,nodes}=dom(['admin-login','admin-key','admin-workspace','admin-login-panel','admin-message','admin-list','admin-summary','admin-filter','admin-refresh','admin-logout','admin-export','admin-pagination','admin-login-button']);
  nodes['admin-key'].value='offline-test-admin-key-0000000000000';nodes['admin-filter'].value='pending';const calls=[];
  mountFeedbackAdmin(root,{request:async(path,options)=>{calls.push({path,options});return {items:[{...comment,status:'pending'}],total:1,page:1,page_size:20,counts:{pending:1,published:0,rejected:0}};}});
  await nodes['admin-login'].emit('submit');assert.equal(nodes['admin-workspace'].hidden,false);
  const buttons=[];function visit(n){if(n.tagName==='button')buttons.push(n);n.children.forEach(visit);}visit(nodes['admin-list']);
  await buttons.find(n=>n.textContent==='通过并发布').emit('click');
  const patch=calls.find(c=>c.options?.method==='PATCH');assert.equal(patch.options.body.status,'published');assert.match(patch.options.headers.Authorization,/^Bearer offline/);assert.match(patch.path,/\/one$/);
});
