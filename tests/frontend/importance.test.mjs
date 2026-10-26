import test from 'node:test';
import assert from 'node:assert/strict';
import * as controls from '../../frontend/preferences.mjs';

test('five slider positions map to each dimension table, including distinct zero meanings',()=>{
  assert.equal(typeof controls.importanceWeight,'function');
  for(const [key,expected] of Object.entries({storage_gb:[0,3,5,7,9],weight_g:[0,2,4,7,9],battery_capacity_mah:[0,2,4,7,9],chipset_tier:[0,2,4,7,9],screen_size_inch:[0,1,4,6,8],dxomark_photo:[0,0,4,7,9],honor_of_kings_avg_fps:[0,0,3,6,9]})){
    for(let position=0;position<5;position++)assert.equal(controls.importanceWeight(key,position),expected[position]);
  }
});

test('request serialization excludes price and refresh scores and preserves hard constraints',()=>{
  const needs={budget:{min:2000,max:5000},min_storage:256,brands:{include:['华为'],exclude:['苹果']}};
  const body=controls.requestFields({needs,method:'hybrid_rrf',weights:{price_cny:9,refresh_rate_hz:9,weight_g:7,battery_capacity_mah:4,chipset_tier:2}});
  assert.deepEqual(body.needs,needs);
  assert.deepEqual(body.weights,{weight_g:7,battery_capacity_mah:4,chipset_tier:2});
});
