const referenceDimensions=[['ref_storage','存储','storage_gb'],['ref_weight','重量','weight_g'],['ref_battery','电池容量','battery_capacity_mah'],['ref_chipset','处理器','chipset_tier'],['ref_screen','屏幕尺寸匹配','screen_size_inch'],['ref_photo','拍照规格','dxomark_photo'],['ref_game','游戏（规格估算）','honor_of_kings_avg_fps']];
export const labels={price_cny:'记录价格',battery_capacity_mah:'电池容量',chipset_tier:'处理器档位',weight_g:'重量',storage_gb:'存储',refresh_rate_hz:'刷新率',screen_size_inch:'屏幕尺寸',dxomark_photo:'DXOMARK 照片子分',honor_of_kings_avg_fps:'王者荣耀平均帧率',battery_mixed_usage_duration_min:'混合使用实测续航',...Object.fromEntries(referenceDimensions.map(([key,name])=>[key,name]))};
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
  const categories=(record.category_assessments??[]).filter(c=>c.status==='evidence_available');
  if(categories.length){const tags=element('div',undefined,'category-tags');tags.setAttribute('aria-label','有评测资料的场景，不代表性能认证');for(const c of categories)tags.append(element('span',`${c.label} · 有资料`,'category-tag'));body.append(tags);}
  const pending=(record.category_assessments??[]).find(c=>c.id===context.category&&c.status==='pending_review');
  if(pending)body.append(element('p',`${pending.label} · 待评估：${pending.reason}`,'category-pending'));
  if(Array.isArray(record.reference_scores))body.append(renderReferenceScores(record.reference_scores,context.referenceRuleVersion));
  if(context.reason)body.append(renderReason(context.reason));
  if(context.explanation||record.score!=null){
    const detail=element('details',undefined,'technical-evidence');detail.append(element('summary','查看数据与计算依据'));
    detail.append(element('p',`配置编号：${record.sku_id}`,'small'));
    if(record.score!=null){detail.append(element('div',`${context.scoreLabel??(context.method==='hybrid_rrf'?'实验融合分':'TOPSIS 相对贴近度')} ${Number(record.score).toFixed(4)}`,'score'));detail.append(element('p','综合排序分只表示当前候选集的相对位置，不是百分制性能分。','small'));}
    if(context.explanation)detail.append(element('p',context.explanation,'explanation'));
    if(context.reason?.fact_ids?.length)detail.append(element('p',`上述理由引用：${context.reason.fact_ids.join('、')}`,'small'));
    if(context.onExplain){const ai=button('DeepSeek 辅助核查',()=>context.onExplain(record.sku_id,detail));ai.disabled=!context.llmEnabled;detail.append(ai,element('p',context.llmEnabled?'仅向服务方发送本配置推荐证据；不改变分数与排名。':'自然语言理由由程序依据数据生成，不需要大模型调用。','small'));}body.append(detail);
  }
  const actions=element('div',undefined,'card-actions');actions.append(button('详情与来源',()=>context.onDetails?.(record.sku_id)),button(context.selected?'已加入对比':'加入对比',()=>context.onCompare?.(record.sku_id)));body.append(actions);card.append(panel,body);return card;
}
function renderReferenceScores(scores,version){
  const section=element('section',undefined,'reference-scores');section.setAttribute('aria-label','七项规格参考分，满分100分');
  section.append(element('h4','七项规格参考分'),element('p','0—100 分 · 固定规则估算，非实测体验分','small'));
  const basis=element('details',undefined,'reference-basis');basis.append(element('summary','查看七项换算依据'));
  if(version)basis.append(element('p',`参考规则：${version}`,'small'));
  for(const [key,name] of referenceDimensions){
    const item=scores.find(value=>value.metric_key===key),raw=item?.score;
    const score=typeof raw==='number'&&Number.isFinite(raw)&&raw>=0&&raw<=100?raw:null;
    const line=element('div',undefined,'reference-score');line.dataset.metricKey=key;
    const heading=element('div',undefined,'reference-score-heading');heading.append(element('span',name),element('span',score===null?'资料不足':`${Number(score.toFixed(1))} / 100`,score===null?'reference-score-value small':'reference-score-value'));line.append(heading);
    if(score!==null){const bar=element('progress');bar.max=100;bar.value=score;bar.setAttribute('aria-label',`${name}规格参考分`);bar.textContent=`${score} / 100`;line.append(bar);}
    else line.classList.add('reference-score-missing');
    section.append(line);
    const inputs=Object.entries(item?.inputs??{}).map(([input,value])=>`${input}=${value===null?'资料不足':typeof value==='object'?JSON.stringify(value):value}`).join('；');
    basis.append(element('p',`${name}：${item?.basis||'资料不足，尚无可用的换算依据。'}${inputs?` 输入：${inputs}`:''}`,'small'));
  }
  section.append(element('p','游戏为规格估算：80% 处理器参考分 + 20% 电池容量参考分，容量不代表实际续航。屏幕分为尺寸匹配，不代表屏幕显示质量；拍照规格不代表成像质量。','reference-limitations small'),basis);
  return section;
}
function renderReason(reason){
  const section=element('section',undefined,'recommendation-reason');section.setAttribute('aria-label','为什么适合你');
  section.append(element('h4','为什么适合你'),element('p',reason.summary));
  if(reason.matches?.length){const list=element('ul',undefined,'reason-matches');for(const text of reason.matches)list.append(element('li',text));section.append(list);}
  if(reason.tradeoffs?.length){section.append(element('h4','你需要权衡'));for(const text of reason.tradeoffs)section.append(element('p',text,'reason-tradeoff'));}
  for(const text of reason.limitations??[])section.append(element('p',text,'reason-limitation'));
  return section;
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
  note.append(element('p',`${result.score_label??'TOPSIS 相对贴近度'}不是绝对性能或购买概率；不同条件下的分数不能直接比较。`,'small'));
  if(result.evaluation_mode==='spec_reference')note.append(element('p','本次使用七项规格参考分：各项按固定规则换算为 0—100 分，再依据你的权重排序。游戏与拍照均为规格代理，不代表实测表现。','small'));
  else if(result.evaluation_mode==='measured')note.append(element('p','本次使用可比实测资料；拍照与游戏的协议、测试条件和缺失资料按实测规则核验。','small'));
  if(result.method==='hybrid_rrf')note.append(element('p','实验融合：theta=1、alpha=0.5、k=10，使用固定参数；未证明比默认排序更准确，也不是学习所得。','warning'));
  for(const item of result.selection_rules?.default_reference_policy?.deferred??[])note.append(element('p',`${labels[item.metric_key]}：保留系统初始权重 ${item.initial_weight}。${item.reason}。若设为一般或以上，将严格检查该项资料。`,'warning'));
  const more=element('details');more.append(element('summary','查看权重、版本与未评分说明'));
  more.append(element('p',`数据 ${result.dataset_version} / 规则 ${result.feature_rule_version} / ${result.algorithm_version}`,'small'));
  if(result.reference_rule_version)more.append(element('p',`规格参考规则 ${result.reference_rule_version}`,'small'));
  const formulas=result.selection_rules?.reference_scoring?.formulas;
  if(formulas)more.append(element('p',`固定换算规则：${typeof formulas==='string'?formulas:JSON.stringify(formulas)}`,'small'));
  more.append(element('p',`有效权重：${Object.entries(result.effective_weights).map(([k,v])=>`${labels[k]??k} ${v}`).join(' · ')}`,'small'));
  more.append(element('p',`未评分 ${result.unscored.length} · 备选 ${result.backups.length} · 决策 ${result.decision_sha256}`,'small'));
  for(const row of [...result.unscored,...result.backups]){
    const missing=row.criteria.filter(c=>c.missing_reason).map(c=>`${labels[c.metric_key]??c.metric_key}: ${c.missing_reason}`).join('；');
    more.append(element('p',`${row.display_name} · ${row.sku_id} · ${missing||'独立机型不足，未评分'}`,'small'));
  }note.append(more);return note;
}
export function renderPerformance(capabilities,weights,intent,current=false,evaluationMode='measured'){
  const content=element('div',undefined,'performance-coverage');
  if(evaluationMode==='spec_reference'){
    content.append(element('p','七项均为固定规则的规格参考；资料不足不补零。处理器档位包含第三方榜单估算，电池容量不代表实际续航，屏幕仅比较尺寸匹配，拍照规格不代表成像质量。','small'));
    const criteria={storage_gb:'storage',weight_g:'weight',battery_capacity_mah:'battery',chipset_tier:'chipset',screen_size_inch:'screen',dxomark_photo:'photo',honor_of_kings_avg_fps:'game'};
    for(const [,name,key] of referenceDimensions){
      const block=element('div',undefined,'performance-record');block.append(element('p',`${name} · 你的权重 ${weights[key]??0}${Number(weights[key])>0?'':'（不参与排序）'}`));
      const preference=intent?.extraction?.preferences?.find(p=>p.criterion===criteria[key]);
      if(preference)block.append(element('p',`${preference.verdict==='inferred'?'使用场景推断，待你确认':preference.evidence?'原话提取，待你确认':'未明确提及'}：${preference.level}${preference.evidence?`；原话「${preference.evidence}」`:''}`,'small'));
      if(key==='honor_of_kings_avg_fps')block.append(element('p',`80% 处理器参考分 + 20% 电池容量参考分，仅为规格估算。${intent?.extraction?.game_use?`原始游戏意图：${intent.extraction.game_use.names?.join('、')||intent.extraction.game_use.evidence||'游戏名称未明确'}。`:''}`,'small'));
      content.append(block);
    }
    return content;
  }
  content.append(element('p',current?'以下为本次已计算条件下的独立指标覆盖；不代表三项共同可比。':'当前条件待验证。以下计数仅为默认新机、未加筛选的参考覆盖；生成推荐后核验当前范围。','small'));
  const dimensions={dxomark_photo:'photo',honor_of_kings_avg_fps:'game',battery_mixed_usage_duration_min:'battery'};
  for(const [key,criterion] of Object.entries(dimensions)){
    const cap=capabilities?.[key],preference=intent?.extraction?.preferences?.find(p=>p.criterion===criterion);
    const block=element('div',undefined,'performance-record');
    block.append(element('p',`${labels[key]} · 你的权重 ${weights[key]??0}${Number(weights[key])>0?'':'（不参与排序）'}`));
    if(cap)block.append(element('p',`${cap.eligible_model_count??0} 个机型 / ${cap.eligible_sku_count??0} 个配置 · ${cap.scope??''}。${cap.reason??''}`,'small'));
    else block.append(element('p','覆盖资料尚未载入，不能据此判断手机表现。','small'));
    if(preference)block.append(element('p',`${preference.verdict==='inferred'?'使用场景推断，待你确认':preference.evidence?'原话提取，待你确认':'未明确提及'}：${preference.level}${preference.evidence?`；原话「${preference.evidence}」`:''}`,'small'));
    if(key==='honor_of_kings_avg_fps'&&intent?.extraction?.game_use){
      const game=intent.extraction.game_use;
      block.append(element('p',`原始游戏意图：${game.names?.join('、')||game.evidence||'游戏名称未明确'}。王者荣耀测试不能代表其他游戏。`,'small'));
      if(game.source_fragments?.length)block.append(element('p',`游戏原话记录：${game.source_fragments.join('；')}`,'small'));
    }
    content.append(block);
  }
  return content;
}
function table(headers,rows){const wrap=element('div',undefined,'table-scroll');const t=element('table');const head=element('thead'),h=element('tr');for(const value of headers)h.append(element('th',value));head.append(h);t.append(head);const body=element('tbody');for(const values of rows){const row=element('tr');for(const v of values)row.append(element('td',valueText(v)));body.append(row);}t.append(body);wrap.append(t);return wrap;}
export function renderDetail(item){
  const content=element('div');content.append(element('h3',`${item.model.display_name} · ${item.sku.variant_name} · ${item.sku.ram_raw} / ${item.sku.storage_raw}`),element('p',item.sku.sku_id,'small'));
  if(item.sku.image)content.append(renderImage({...item.sku,...item.model}));
  if(item.sku.category_assessments?.length){
    content.append(element('h3','场景分类与依据'),element('p','分类用于查找已有资料，不是适用性或性能认证；机型级资料不代表每个配置独立实测。','small'));
    for(const c of item.sku.category_assessments){
      const block=element('details',undefined,'category-detail');block.append(element('summary',`${c.label} · ${c.status==='evidence_available'?'有评测资料':'待评估'}`),element('p',c.reason));
      if(c.status==='pending_review')block.open=true;
      for(const ref of c.evidence_refs??[]){
        block.append(element('p',`${labels[ref.metric_key]??ref.metric_key}：${valueText(ref.value,ref.unit?` ${ref.unit}`:'')} · ${ref.institution||'机构未记载'} · ${ref.protocol||'协议未记载'}`));
        block.append(renderSources([{source_id:ref.source_id,publisher:ref.publisher,urls:ref.urls}]));
        block.append(element('p',`机型级资料 · ${ref.run_id} · 条件：${valueText(ref.conditions_json)}`,'small'));
      }block.append(element('p',`分类规则：${c.rule_version}`,'small'));content.append(block);
    }
  }
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
