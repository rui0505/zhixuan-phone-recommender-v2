import {element} from './render.mjs';

export function draftFields(payload,current){
  if(!payload?.draft)return null;
  const d=payload.draft;
  if(!['ok','degraded'].includes(payload.status)||d.confirmed!==false||!d.needs?.budget||!d.needs?.brands||!d.weights)
    throw new Error('需求草稿格式无效，请使用手动填写');
  const keys=['weight_g','storage_gb','screen_size_inch','battery_capacity_mah','chipset_tier','dxomark_photo','honor_of_kings_avg_fps'];
  const weights={...d.weights,battery_mixed_usage_duration_min:d.weights.battery_mixed_usage_duration_min??0};
  if(keys.some(k=>typeof weights[k]!=='number'||!Number.isFinite(weights[k])||weights[k]<0||weights[k]>10))
    throw new Error('偏好草稿无效，请使用手动填写');
  return {...structuredClone(d),weights,mode:current.mode,method:current.method??'hybrid_rrf',evaluation_mode:current.evaluation_mode??'spec_reference',expected_reference_rule_version:current.expected_reference_rule_version};
}

export function renderAismStructure(structure){
  if(!structure?.nodes||!structure?.up_layers||!structure?.down_layers)return null;
  const detail=element('details');detail.append(element('summary','查看维度关系（AISM）'));
  detail.append(element('p','AISM 组织七个需求维度及其关系；大模型按维度理解你的原话。关系层级不等于重要程度，也不会自动提高其他维度的偏好。','small'));
  detail.append(element('p',structure.empirically_validated?'结构验证状态请以模型来源说明为准。':'初始结构来自项目已有定义与规格评分依赖，尚未经过语义效果实验验证。','small'));
  const labels=Object.fromEntries(structure.nodes.map(node=>[node.id,node.label]));
  const relations=element('ul');for(const edge of structure.relations??[])relations.append(element('li',`${labels[edge.source]??edge.source} → ${labels[edge.target]??edge.target}`));
  detail.append(element('p','当前关系仅用于规格参考分的计算依赖：','small'),relations);
  for(const [key,title] of [['up_layers','UP · 从结果端分层'],['down_layers','DOWN · 从输入端分层']]){
    detail.append(element('h4',title));const list=element('ol');
    for(const layer of structure[key])list.append(element('li',layer.map(id=>labels[id]??id).join('、')));detail.append(list);
  }
  detail.append(element('p',`结构 ${structure.model_version} · 摘要 ${structure.model_sha256?.slice(0,12)??'未提供'}；结构不能单独判断原话是否支持抽取结果，请检查后整体确认。`,'small'));
  return detail;
}
