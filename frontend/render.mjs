export const labels={price_cny:'记录价格',weight_g:'重量',storage_gb:'存储',refresh_rate_hz:'刷新率',screen_size_inch:'屏幕尺寸',dxomark_photo:'DXOMARK 照片子分',honor_of_kings_avg_fps:'王者荣耀平均帧率'};
export const conditionLabel=value=>({new:'新机',used:'二手',unknown:'成色未知'}[value]??'成色未记载');
export const valueText=(value,unit='')=>value===null||value===undefined||value===''?'未记载':`${value}${unit}`;
export function element(tag,text,className){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;}
export function button(text,action){const node=element('button',text);node.type='button';node.addEventListener('click',action);return node;}
export function renderSources(sources){
  const list=element('ul');
  for(const source of sources){
    const row=element('li');row.append(element('span',`${source.publisher||'来源未注明'} · ${source.source_id} `));
    for(const raw of source.urls??(source.url?[source.url]:[])){
      try{const url=new URL(raw);if(!['http:','https:'].includes(url.protocol)||url.username||url.password||/\s/.test(raw)||/https?:\/\/[^/]*@/.test(raw))continue;
        const link=element('a','查看原始来源');link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';row.append(link,document.createTextNode(' '));}catch{}
    }list.append(row);
  }if(!list.childNodes.length)list.append(element('li','暂无可公开链接；不代表该指标已实测。'));return list;
}
export function renderCard(record,context={}){
  const card=element('article',undefined,'card');card.dataset.skuId=record.sku_id;
  const panel=renderImage(record);
  const body=element('div',undefined,'card-body');body.append(element('h3',record.display_name),element('p',`${record.variant_name||'版本未记载'} · ${record.ram_raw||'内存未记载'} / ${record.storage_raw||'存储未记载'}`,'small'));
  body.append(element('p',record.price_cny==null?'价格未记载':`¥ ${record.price_cny}`,'price'));
  body.append(element('p',`${conditionLabel(record.condition)}${record.is_estimated?' · 估计价':''} · ${record.observed_at||'日期未记载'}`,'small'));
  body.append(element('p',record.sku_id,'small'));
  if(record.score!=null)body.append(element('div',`TOPSIS 相对贴近度 ${Number(record.score).toFixed(4)}`,'score'));
  if(context.explanation){const detail=element('details');detail.append(element('summary','为什么推荐 · 证据与限制'),element('p',context.explanation,'explanation'));if(context.onExplain){const ai=button('DeepSeek 辅助解释',()=>context.onExplain(record.sku_id,detail));ai.disabled=!context.llmEnabled;detail.append(ai,element('p',context.llmEnabled?'仅向服务方发送本配置推荐证据；不改变分数与排名。':'DeepSeek 尚未启用；当前使用可回查的模板解释。','small'));}body.append(detail);}
  const actions=element('div',undefined,'card-actions');actions.append(button('详情与来源',()=>context.onDetails?.(record.sku_id)),button(context.selected?'已加入对比':'加入对比',()=>context.onCompare?.(record.sku_id)));body.append(actions);card.append(panel,body);return card;
}
function renderImage(record){
  const panel=element('div',undefined,'brand-panel');
  const placeholder=()=>panel.replaceChildren(element('strong',record.brand||'手机配置'),element('span','该机型图片暂不可用'));
  const info=record.image;
  if(!info||!/^\/media\/[A-Za-z0-9_-]+\.webp$/.test(info.url)){placeholder();return panel;}
  panel.classList.add('phone-panel');const img=element('img');img.src=info.url;img.alt=info.alt||`${record.display_name} 机型展示图`;img.loading='lazy';img.decoding='async';img.addEventListener('error',placeholder,{once:true});
  panel.append(img,element('span','机型展示图 · 颜色与成色以配置记录为准'));return panel;
}
export function renderDecision(state){
  const result=state.results;const note=element('div',undefined,'decision-note');
  if(!result){note.append(element('p','设置需求、检查权重并确认后，生成你的推荐。'));return note;}
  note.append(element('h3',result.status==='ready'?`${result.ranked_total} 个可比配置 · 当前候选集内相对排序`:'当前条件暂不生成成绩'));
  note.append(element('p',result.reason,result.status==='ready'?'small':'warning'));
  note.append(element('p','TOPSIS 分数不是绝对性能、购买概率；不同条件下的分数不能直接比较。','small'));
  const more=element('details');more.append(element('summary','查看权重、版本与未评分说明'));
  more.append(element('p',`数据 ${result.dataset_version} / 规则 ${result.feature_rule_version} / ${result.algorithm_version}`,'small'));
  more.append(element('p',`有效权重：${Object.entries(result.effective_weights).map(([k,v])=>`${labels[k]??k} ${v}`).join(' · ')}`,'small'));
  more.append(element('p',`未评分 ${result.unscored.length} · 备选 ${result.backups.length} · 决策 ${result.decision_sha256}`,'small'));
  for(const row of [...result.unscored,...result.backups]){
    const missing=row.criteria.filter(c=>c.missing_reason).map(c=>`${labels[c.metric_key]??c.metric_key}: ${c.missing_reason}`).join('；');
    more.append(element('p',`${row.display_name} · ${row.sku_id} · ${missing||'独立机型不足，未评分'}`,'small'));
  }note.append(more);return note;
}
function table(headers,rows){const wrap=element('div',undefined,'table-scroll');const t=element('table');const head=element('thead'),h=element('tr');for(const value of headers)h.append(element('th',value));head.append(h);t.append(head);const body=element('tbody');for(const values of rows){const row=element('tr');for(const v of values)row.append(element('td',valueText(v)));body.append(row);}t.append(body);wrap.append(t);return wrap;}
export function renderDetail(item){
  const content=element('div');content.append(element('h3',`${item.model.display_name} · ${item.sku.variant_name} · ${item.sku.ram_raw} / ${item.sku.storage_raw}`),element('p',item.sku.sku_id,'small'));
  if(item.sku.image)content.append(renderImage({...item.sku,...item.model}));
  content.append(element('h3','规格（按机型 / 版本 / 配置准确关联）'),table(['指标','数值 / 原文','作用域'],item.specifications.map(r=>[labels[r.spec_key]??r.spec_key,valueText(r.value_num??r.value_text??r.raw_value,r.unit?` ${r.unit}`:''),`${r.subject_type} · ${r.subject_id}`])));
  content.append(element('h3','价格快照（非实时售价）'),table(['记录价格','成色','记录日期','价格 ID'],item.prices.map(r=>[valueText(r.amount,' CNY'),conditionLabel(r.condition),r.observed_at,r.price_id])));
  content.append(element('h3','评测与测试条件'),element('p',item.evaluation_note,'small'));
  for(const run of item.benchmark_runs){const block=element('details',undefined,'detail-block');block.append(element('summary',`${run.benchmark} · ${run.institution} · ${run.protocol} · ${run.run_id}`));block.append(element('pre',`范围：${run.subject_type} / ${run.subject_id}\n日期：${valueText(run.run_date)}\n条件：${valueText(run.conditions_json)}`));block.append(table(['实测指标','数值','缺失原因'],item.observations.filter(o=>o.run_id===run.run_id).map(o=>[labels[o.metric_key]??o.metric_key,valueText(o.value_num,o.unit?` ${o.unit}`:''),o.missing_reason])));content.append(block);}
  if(!item.benchmark_runs.length)content.append(element('p','没有关联评测；不以处理器名称或像素推算体验。','small'));
  content.append(element('h3','可回查来源'),renderSources(item.sources));return content;
}
export function renderComparison(items){
  if(!items.length)return element('p','从推荐或手机库加入最多四个准确配置，查看它们的差别。','empty');
  const headers=['对比项',...items.map(i=>`${i.model.display_name} · ${i.sku.ram_raw} / ${i.sku.storage_raw}`)];
  const keys=['weight_g','storage_gb','refresh_rate_hz','screen_size_inch'];
  const rows=[['配置 ID',...items.map(i=>i.sku.sku_id)],['版本',...items.map(i=>i.sku.variant_name)],['价格记录',...items.map(i=>i.prices.map(p=>`${valueText(p.amount,' 元')} / ${conditionLabel(p.condition)} / ${valueText(p.observed_at)}`).join('\n'))]];
  for(const key of keys)rows.push([labels[key],...items.map(i=>key==='storage_gb'?valueText(i.sku.storage_gb,' GB'):i.specifications.filter(r=>r.spec_key===key).map(r=>`${valueText(r.value_num??r.value_text??r.raw_value)} ${r.unit??''} (${r.subject_type})`).join('\n')||'未记载')]);
  const wrap=element('div');wrap.append(table(headers,rows),element('p','不另算综合优劣分。机型级评测展示不代表已适用于所有版本；可比性以推荐输入规则为准。','small'));return wrap;
}
