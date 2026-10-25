import {setPreferences} from './state.mjs';

// Evidence and desired preferences stay in the extraction record, outside strict API requests.
export function requestFields(fields,meta){
  const method=fields.method??'topsis';
  const algorithm=meta?.algorithms?.find(item=>item.method===method);
  const value={needs:structuredClone(fields.needs),weights:structuredClone(fields.weights),
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
