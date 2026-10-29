import test from 'node:test';
import assert from 'node:assert/strict';
import {createState,applyMeta,setPreferences,beginRequest,acceptResponse} from '../../frontend/state.mjs';
import {draftFields} from '../../frontend/needs.mjs';
const preferences=await import('../../frontend/preferences.mjs').catch(()=>({}));
const invoke=(name,...args)=>{assert.equal(typeof preferences[name],'function',`${name} behavior missing`);return preferences[name](...args);};
const release={dataset_version:'d',feature_rule_version:'f',snapshot_sha256:'s',manifest_sha256:'m'};
const algorithms=[{method:'topsis',algorithm_version:'topsis-1.0.0'},{method:'hybrid_rrf',algorithm_version:'hybrid-rrf-1.0.0-experimental'}];
const base={needs:{budget:{min:100,max:3000},min_storage:256,brands:{include:['华为'],exclude:[]}},mode:'new',target_screen:6.5,weights:{battery_capacity_mah:2,chipset_tier:2,weight_g:2,storage_gb:3,screen_size_inch:1,dxomark_photo:0,honor_of_kings_avg_fps:0,battery_mixed_usage_duration_min:0}};

test('strict request serialization binds default or explicit method to available version',()=>{
  const input={...base,desired_weights:{battery_mixed_usage_duration_min:3},confirmed:false,source_fragments:['原话']};
  const regular=invoke('requestFields',input,{algorithms});
  assert.equal(regular.method,'hybrid_rrf');assert.equal(regular.expected_algorithm_version,'hybrid-rrf-1.0.0-experimental');
  assert.equal(regular.desired_weights,undefined);assert.equal(regular.source_fragments,undefined);assert.equal(regular.confirmed,undefined);
  const hybrid=invoke('requestFields',{...input,method:'hybrid_rrf',game_scope:'unsupported'},{algorithms});
  assert.equal(hybrid.method,'hybrid_rrf');assert.equal(hybrid.expected_algorithm_version,'hybrid-rrf-1.0.0-experimental');assert.equal(hybrid.game_scope,'unsupported');
  assert.deepEqual(hybrid.needs,base.needs);
});

test('request serialization binds specification mode and rule version while preserving measured choice',()=>{
  const meta={algorithms,defaults:{evaluation_mode:'spec_reference'},reference_rule_version:'spec-reference-v1'};
  const reference=invoke('requestFields',base,meta);
  assert.equal(reference.evaluation_mode,'spec_reference');assert.equal(reference.expected_reference_rule_version,'spec-reference-v1');
  const measured=invoke('requestFields',{...base,evaluation_mode:'measured'},meta);
  assert.equal(measured.evaluation_mode,'measured');
  assert.deepEqual(measured.needs,base.needs);assert.deepEqual(measured.weights,base.weights);
});

test('reference rule change invalidates results and mode mismatch rejects a recommendation',()=>{
  const state=createState();applyMeta(state,{release,algorithms,reference_rule_version:'ref-1'});
  setPreferences(state,{...base,evaluation_mode:'spec_reference',expected_reference_rule_version:'ref-1'});
  let token=beginRequest(state,'recommend');
  assert.equal(acceptResponse(state,'recommend',token,{status:'ready',evaluation_mode:'measured',reference_rule_version:'ref-1'}),false);
  token=beginRequest(state,'recommend');
  assert.equal(acceptResponse(state,'recommend',token,{status:'ready',evaluation_mode:'spec_reference',reference_rule_version:'ref-old'}),false);
  state.confirmedRequest={confirmed:true};state.results={status:'ready'};
  assert.equal(applyMeta(state,{release,algorithms,reference_rule_version:'ref-2'}),true);
  assert.equal(state.confirmedRequest,null);assert.equal(state.results,null);
});

test('runtime change invalidates confirmed result even when release remains identical',()=>{
  const state=createState();applyMeta(state,{release,algorithms});setPreferences(state,{...base,method:'hybrid_rrf'});
  state.confirmedRequest={method:'hybrid_rrf'};state.results={status:'ready'};const token=beginRequest(state,'recommend');
  assert.equal(applyMeta(state,{release,algorithms:algorithms.map(a=>({...a}))}),false);
  assert.ok(state.confirmedRequest);
  assert.equal(applyMeta(state,{release,algorithms:[algorithms[0],{...algorithms[1],algorithm_version:'new'}]}),true);
  assert.equal(state.confirmedRequest,null);assert.equal(state.results,null);assert.equal(acceptResponse(state,'recommend',token,{status:'ready'}),false);
});

test('wrong method or version response cannot be accepted into current recommendation',()=>{
  const state=createState();applyMeta(state,{release,algorithms});setPreferences(state,{...base,method:'hybrid_rrf',expected_algorithm_version:algorithms[1].algorithm_version});
  let token=beginRequest(state,'recommend');
  assert.equal(acceptResponse(state,'recommend',token,{status:'ready',method:'topsis',algorithm_version:'topsis-1.0.0'}),false);
  token=beginRequest(state,'recommend');
  assert.equal(acceptResponse(state,'recommend',token,{status:'ready',method:'hybrid_rrf',algorithm_version:'old'}),false);
});

test('extracted unsupported preference retains evidence intent while reset preserves hard filters',()=>{
  const payload={status:'ok',extraction:{game_use:{names:['和平精英'],game_scope:'unsupported'},preferences:[{criterion:'game',level:'重要',evidence:'爱打和平精英',verdict:'inferred',needs_confirmation:true}]},performance_preferences:{},draft:{...base,confirmed:false,game_scope:'unsupported',desired_weights:{...base.weights,honor_of_kings_avg_fps:2},weights:{...base.weights,honor_of_kings_avg_fps:2,battery_mixed_usage_duration_min:3}}};
  const draft=draftFields(payload,{mode:'used',method:'hybrid_rrf',evaluation_mode:'measured',expected_reference_rule_version:'ref-1'});
  assert.equal(draft.method,'hybrid_rrf');assert.equal(draft.game_scope,'unsupported');assert.equal(draft.weights.honor_of_kings_avg_fps,2);
  assert.equal(draft.evaluation_mode,'measured');assert.equal(draft.expected_reference_rule_version,'ref-1');
  const reset=invoke('withoutCriterion',draft,'honor_of_kings_avg_fps');
  assert.equal(reset.weights.honor_of_kings_avg_fps,0);assert.equal(reset.weights.battery_mixed_usage_duration_min,3);assert.deepEqual(reset.needs,base.needs);assert.equal(reset.game_scope,'unsupported');
  assert.equal(reset.desired_weights.honor_of_kings_avg_fps,2);assert.equal(payload.extraction.preferences[0].evidence,'爱打和平精英');
});

test('old extraction defaults only absent performance preferences to not counted',()=>{
  const weights={...base.weights};delete weights.battery_mixed_usage_duration_min;
  const draft=draftFields({status:'ok',draft:{...base,weights,confirmed:false}},{mode:'new',method:'topsis'});
  assert.equal(draft.weights.battery_mixed_usage_duration_min,0);
});

test('needs text revision clears confirmations, all result channels and pending extraction',()=>{
  const state=createState();state.confirmedRequest={confirmed:true};state.results={status:'ready'};
  const pending=beginRequest(state,'needs'),download=beginRequest(state,'download');
  invoke('reviseNeedsText',state,base);
  assert.equal(state.confirmedRequest,null);assert.equal(state.results,null);
  assert.equal(acceptResponse(state,'needs',pending,{status:'ok'}),false);assert.equal(acceptResponse(state,'download',download,{}),false);
});
