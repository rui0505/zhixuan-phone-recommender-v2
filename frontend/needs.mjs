export function draftFields(payload,current){
  if(!payload?.draft)return null;
  const d=payload.draft;
  if(!['ok','degraded'].includes(payload.status)||d.confirmed!==false||!d.needs?.budget||!d.needs?.brands||!d.weights)
    throw new Error('需求草稿格式无效，请使用手动填写');
  const keys=['price_cny','weight_g','storage_gb','refresh_rate_hz','screen_size_inch','dxomark_photo','honor_of_kings_avg_fps'];
  if(keys.some(k=>typeof d.weights[k]!=='number'||!Number.isFinite(d.weights[k])||d.weights[k]<0||d.weights[k]>10))
    throw new Error('偏好草稿无效，请使用手动填写');
  return {...structuredClone(d),mode:current.mode};
}
