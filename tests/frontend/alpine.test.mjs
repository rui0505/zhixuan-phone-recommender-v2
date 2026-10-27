import test from 'node:test';
import assert from 'node:assert/strict';
import {uniqueModels,diverseModels,sourceSegments,comparisonFacts} from '../../frontend/alpine.mjs';

test('unranked discovery shows different brands first without dropping any real model',()=>{
 const rows=[{model_id:'a1',brand:'A'},{model_id:'a2',brand:'A'},{model_id:'b',brand:'B'},{model_id:'c',brand:'C'}];
 assert.deepEqual(diverseModels(rows),[rows[0],rows[2],rows[3],rows[1]]);
 assert.equal(rows[1].model_id,'a2');
});

test('discovery keeps actual configurations and deduplicates models without inventing variants',()=>{
 const rows=[{model_id:'M1',sku_id:'S1',price_cny:1},{model_id:'M1',sku_id:'S2',price_cny:2},{model_id:'M2',sku_id:'S3',price_cny:3}];
 assert.deepEqual(uniqueModels(rows),[rows[0],rows[2]]);assert.equal(rows.length,3);
});
test('source chart derives its entire composition from real source summary',()=>{
 const rows=sourceSegments([{label:'规格资料',count:177},{label:'价格资料',count:39}]);
 assert.equal(rows.total,216);assert.equal(rows.items[0].percent,177/216*100);assert.equal(sourceSegments([]).total,0);
});
test('comparison extracts model specifications and snapshot condition and date',()=>{
 const item={model:{display_name:'测试机'},sku:{sku_id:'S1',storage_gb:256,ram_raw:'12GB',storage_raw:'256GB'},specifications:[{spec_key:'weight_g',value_num:201,unit:'g'},{spec_key:'chipset',value_text:'真实处理器'}],prices:[{amount:3000,condition:'used',observed_at:'2026-01-01'}]};
 const facts=comparisonFacts(item);assert.match(facts.weight,/201/);assert.match(facts.price,/3000.*二手.*2026-01-01/);assert.match(facts.storage,/256/);assert.equal(facts.screen,'资料未记载');
 item.specifications.push({spec_key:'battery_capacity_mah',value_num:null,raw_value:'官方未公布mAh',parse_status:'non_numeric'});
 assert.equal(comparisonFacts(item).battery,'资料未记载');
});
