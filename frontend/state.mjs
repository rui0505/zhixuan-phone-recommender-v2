const channels=['recommend','catalog','detail','compare','download','explain','needs'];
const clone=value=>structuredClone(value);
const releaseTag=release=>JSON.stringify(['dataset_version','feature_rule_version','snapshot_sha256','manifest_sha256'].map(k=>release?.[k]??null));
const runtimeTag=meta=>JSON.stringify({algorithms:(meta?.algorithms??[]).map(a=>[a.method,a.algorithm_version,a.parameters??null]).sort((a,b)=>a[0].localeCompare(b[0])),referenceRuleVersion:meta?.reference_rule_version??null,evaluationMode:meta?.defaults?.evaluation_mode??null});
export function createState(){
  return {preferenceRevision:0,preferences:{},release:null,meta:null,confirmedRequest:null,results:null,
    comparisonIds:[],responses:{},sequence:Object.fromEntries(channels.map(c=>[c,0])),
    loading:Object.fromEntries(channels.map(c=>[c,false])),errors:Object.fromEntries(channels.map(c=>[c,null]))};
}
export function setPreferences(state,fields){
  state.preferenceRevision++;state.preferences=clone(fields);state.confirmedRequest=null;state.results=null;state.responses={};
  for(const channel of channels){state.loading[channel]=false;state.errors[channel]=null;}
}
export function applyMeta(state,meta){
  const changed=releaseTag(state.release)!==releaseTag(meta.release)||runtimeTag(state.meta)!==runtimeTag(meta);
  if(changed){setPreferences(state,state.preferences);state.comparisonIds=[];}
  state.meta=clone(meta);state.release=clone(meta.release);return changed;
}
export function beginRequest(state,channel){
  if(!channels.includes(channel))throw new Error('Unknown request channel');
  state.loading[channel]=true;state.errors[channel]=null;
  return {revision:state.preferenceRevision,sequence:++state.sequence[channel],release:releaseTag(state.release),runtime:runtimeTag(state.meta),method:state.preferences.method??'topsis',algorithmVersion:state.preferences.expected_algorithm_version,evaluationMode:state.preferences.evaluation_mode??state.meta?.defaults?.evaluation_mode??'spec_reference',referenceRuleVersion:state.preferences.expected_reference_rule_version};
}
export function acceptResponse(state,channel,token,value){
  if(token.revision!==state.preferenceRevision||token.sequence!==state.sequence[channel]||token.release!==releaseTag(state.release)||token.runtime!==runtimeTag(state.meta))return false;
  if(channel==='recommend'&&((value.method&&value.method!==token.method)||(token.algorithmVersion&&value.algorithm_version&&value.algorithm_version!==token.algorithmVersion)||(value.evaluation_mode&&value.evaluation_mode!==token.evaluationMode)||(token.referenceRuleVersion&&value.reference_rule_version&&value.reference_rule_version!==token.referenceRuleVersion))){state.loading[channel]=false;return false;}
  state.loading[channel]=false;
  if(value.error){state.errors[channel]=value.error;return true;}
  state.errors[channel]=null;state.responses[channel]=value;
  if(channel==='recommend')state.results=value;
  return true;
}
function comparisonChanged(state){state.sequence.compare++;state.loading.compare=false;delete state.responses.compare;}
export function addComparison(state,skuId){
  if(state.comparisonIds.includes(skuId)||state.comparisonIds.length>=4)return false;
  state.comparisonIds.push(skuId);comparisonChanged(state);return true;
}
export function removeComparison(state,skuId){
  state.comparisonIds=state.comparisonIds.filter(id=>id!==skuId);comparisonChanged(state);
}
