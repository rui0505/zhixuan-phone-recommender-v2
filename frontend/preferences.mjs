import {setPreferences} from './state.mjs';

export const importanceLevels=['不在意','未提及','一般','重要','非常重要'];
export const importanceTable={storage_gb:[0,3,5,7,9],weight_g:[0,2,4,7,9],battery_capacity_mah:[0,2,4,7,9],chipset_tier:[0,2,4,7,9],screen_size_inch:[0,1,4,6,8],dxomark_photo:[0,0,4,7,9],honor_of_kings_avg_fps:[0,0,3,6,9]};
export const preferenceMetrics=Object.keys(importanceTable);
export function importanceWeight(key,position){
  if(!importanceTable[key]||!Number.isInteger(Number(position))||position<0||position>4)throw new Error('偏好等级无效');
  return importanceTable[key][position];
}
export function importancePosition(key,weight,level){
  const named=importanceLevels.indexOf(level);
  if(named>=0&&importanceTable[key][named]===weight)return named;
  const exact=importanceTable[key].indexOf(weight);
  if(exact>=0)return exact;
  throw new Error('偏好权重不在等级表中，请重新选择');
}

// Evidence and desired preferences stay in the extraction record, outside strict API requests.
export function requestFields(fields,meta){
  const method=fields.method??'topsis';
  const algorithm=meta?.algorithms?.find(item=>item.method===method);
  const value={needs:structuredClone(fields.needs),weights:Object.fromEntries(Object.entries(fields.weights).filter(([key])=>preferenceMetrics.includes(key)||key==='battery_mixed_usage_duration_min')),
    target_screen:fields.target_screen,mode:fields.mode,method,game_scope:fields.game_scope??'honor_of_kings'};
  if(algorithm)value.expected_algorithm_version=algorithm.algorithm_version;
  return value;
}
export function withoutCriterion(fields,key){
  return {...structuredClone(fields),weights:{...fields.weights,[key]:0}};
}
export function reviseNeedsText(state,fields){
  state.needsIntent=null;setPreferences(state,fields);
}
