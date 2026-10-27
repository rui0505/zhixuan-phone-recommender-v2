import test from 'node:test';
import assert from 'node:assert/strict';
import {createState,beginRequest,setPreferences,acceptResponse} from '../../frontend/state.mjs';
const needs=await import('../../frontend/needs.mjs').catch(()=>null);
function draftFields(...args){assert.ok(needs,'natural-language draft adapter is missing');return needs.draftFields(...args);}

test('draft preserves condition mode, never auto-confirms or promotes budget reference',()=>{
  const current={mode:'used',target_screen:6.5,weights:{price_cny:5}};
  const payload={status:'ok',extraction:{budget:{reference:2000}},draft:{
    confirmed:false,needs:{budget:{min:null,max:null},min_storage:256,brands:{include:[],exclude:[]}},
    target_screen:null,weights:{battery_capacity_mah:2,chipset_tier:2,weight_g:2,storage_gb:3,
      screen_size_inch:0,dxomark_photo:0,honor_of_kings_avg_fps:0}}};
  const value=draftFields(payload,current);
  assert.equal(value.mode,'used');assert.equal(value.needs.budget.max,null);
  assert.equal(value.target_screen,null);assert.equal(value.weights.dxomark_photo,0);
  assert.equal(value.confirmed,false);assert.equal(current.weights.price_cny,5);
});

test('failed or malformed extraction cannot partially replace manual fields',()=>{
  assert.equal(draftFields({status:'failed',draft:null},{mode:'new'}),null);
  assert.throws(()=>draftFields({status:'ok',draft:{confirmed:true}},{mode:'new'}));
});

test('late natural-language extraction cannot overwrite changed manual preferences',()=>{
  const state=createState();let token;
  assert.doesNotThrow(()=>{token=beginRequest(state,'needs');});
  setPreferences(state,{mode:'used'});
  assert.equal(acceptResponse(state,'needs',token,{status:'ok'}),false);
  assert.equal(state.responses.needs,undefined);
});

test('review gate requires each distinct issue to be acknowledged',()=>{
  assert.equal(typeof needs.pendingReview,'function');
  const report={issues:[{id:'budget',field:'budget.max'},{id:'photo',field:'preferences.photo'}]};
  assert.equal(needs.pendingReview(report,new Set(['unrelated'])).length,2);
  assert.deepEqual(needs.pendingReview(report,new Set(['budget'])),[{id:'photo',field:'preferences.photo'}]);
  assert.equal(needs.pendingReview(report,new Set(['budget','photo'])).length,0);
  assert.deepEqual(needs.pendingReview(undefined,new Set()),[]);
});
