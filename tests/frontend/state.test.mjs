import test from 'node:test';
import assert from 'node:assert/strict';
import {createState,setPreferences,applyMeta,beginRequest,acceptResponse,addComparison,removeComparison} from '../../frontend/state.mjs';
const release={dataset_version:'v1',feature_rule_version:'r1',snapshot_sha256:'a',manifest_sha256:'b'};
test('old recommendation cannot overwrite changed preferences',()=>{
  const state=createState(); const old=beginRequest(state,'recommend');
  state.results={ready:true};state.confirmedRequest={mode:'new'};
  setPreferences(state,{mode:'used'});
  assert.equal(acceptResponse(state,'recommend',old,{status:'ready'}),false);
  assert.equal(state.results,null);assert.equal(state.confirmedRequest,null);assert.equal(state.preferenceRevision,1);
});
test('per-channel sequence rejects old page, not unrelated details',()=>{
  const state=createState();const a=beginRequest(state,'catalog'),d=beginRequest(state,'detail'),b=beginRequest(state,'catalog');
  assert.equal(acceptResponse(state,'catalog',b,{page:2}),true);
  assert.equal(acceptResponse(state,'catalog',a,{page:1}),false);
  assert.equal(acceptResponse(state,'detail',d,{sku_id:'S1'}),true);
  assert.equal(state.responses.catalog.page,2);
});
test('same release preserves confirmation, changed release invalidates all',()=>{
  const state=createState();applyMeta(state,{release});state.confirmedRequest={mode:'new'};state.results={ready:true};addComparison(state,'S1');
  const token=beginRequest(state,'recommend');applyMeta(state,{release:{...release}});
  assert.equal(state.confirmedRequest.mode,'new');assert.equal(state.results.ready,true);
  applyMeta(state,{release:{...release,manifest_sha256:'c'}});
  assert.equal(state.results,null);assert.equal(state.confirmedRequest,null);assert.deepEqual(state.comparisonIds,[]);
  assert.equal(acceptResponse(state,'recommend',token,{status:'ready'}),false);
});
test('comparison is unique capped at four, changes reject stale compare',()=>{
  const state=createState();assert.equal(addComparison(state,'S1'),true);assert.equal(addComparison(state,'S1'),false);
  ['S2','S3','S4'].forEach(id=>assert.equal(addComparison(state,id),true));
  assert.equal(addComparison(state,'S5'),false);const token=beginRequest(state,'compare');removeComparison(state,'S1');
  assert.equal(acceptResponse(state,'compare',token,{items:[]}),false);assert.equal(addComparison(state,'S5'),true);
});
test('states and preferences are isolated and late errors discarded',()=>{
  const a=createState(),b=createState();const fields={weights:{price_cny:5}};setPreferences(a,fields);fields.weights.price_cny=0;
  assert.equal(a.preferences.weights.price_cny,5);assert.equal(b.preferenceRevision,0);
  const old=beginRequest(a,'recommend'),fresh=beginRequest(a,'recommend');
  acceptResponse(a,'recommend',fresh,{status:'ready'});
  assert.equal(acceptResponse(a,'recommend',old,{error:{message:'old failure'}}),false);
  assert.equal(a.results.status,'ready');assert.equal(a.errors.recommend,null);
});
