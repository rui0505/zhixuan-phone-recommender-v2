export function draftFields(payload,current){
  if(!payload?.draft)return null;
  const d=payload.draft;
  if(!['ok','degraded'].includes(payload.status)||d.confirmed!==false||!d.needs?.budget||!d.needs?.brands||!d.weights)
    throw new Error('需求草稿格式无效，请使用手动填写');
  const keys=['weight_g','storage_gb','screen_size_inch','battery_capacity_mah','chipset_tier','dxomark_photo','honor_of_kings_avg_fps'];
  const weights={...d.weights,battery_mixed_usage_duration_min:d.weights.battery_mixed_usage_duration_min??0};
  if(keys.some(k=>typeof weights[k]!=='number'||!Number.isFinite(weights[k])||weights[k]<0||weights[k]>10))
    throw new Error('偏好草稿无效，请使用手动填写');
  return {...structuredClone(d),weights,mode:current.mode,method:current.method??'topsis',evaluation_mode:current.evaluation_mode??'spec_reference',expected_reference_rule_version:current.expected_reference_rule_version};
}
