import {createState,setPreferences,applyMeta,beginRequest,acceptResponse,addComparison,removeComparison} from './state.mjs';
import {fetchJson,fetchDownload} from './api.mjs';
import {element,button,labels,renderCard,renderDecision,renderComparison,renderDetail,renderPerformance} from './render.mjs';
import {draftFields} from './needs.mjs';
import {requestFields,withoutCriterion,reviseNeedsText} from './preferences.mjs';

const $=id=>document.getElementById(id),state=createState(),controllers=new Map();
let view='recommend',metaSequence=0,needsPendingBudget=false,catalogCategory='all';
const metrics=Object.keys(labels);
function notify(message,error=false){const node=$('message');node.textContent=message;node.hidden=!message;node.setAttribute('role',error?'alert':'status');if(error)node.focus();}
function abortRequests(){for(const controller of controllers.values())controller.abort();controllers.clear();}
function numeric(id){return $(id).value.trim()===''?null:Number($(id).value);}
function selected(id){return [...$(id).selectedOptions].map(option=>option.value);}
function fields(){return requestFields({needs:{budget:{min:numeric('min-price'),max:numeric('max-price')},min_storage:numeric('min-storage'),brands:{include:selected('include-brands'),exclude:selected('exclude-brands')}},
  weights:Object.fromEntries(metrics.map(key=>[key,Number($(key).value)])),target_screen:numeric('target-screen'),mode:$('mode').value,method:$('method').value,game_scope:$('game-scope').value},state.meta);}
function preferencesSummary(){
  const f=fields(),budget=f.needs.budget;
  $('preferences-summary').textContent=`${{new:'新机',used:'二手',all:'全部成色（含未知）'}[f.mode]} · ${budget.min??'不限'}—${budget.max??'不限'} 元 · ${f.needs.min_storage?`${f.needs.min_storage}GB 起`:'存储不限'} · ${f.needs.brands.include.join('、')||'全部品牌'}`;
  for(const key of metrics)document.querySelector(`output[for="${key}"]`).textContent=$(key).value;
  const current=Boolean(state.results?.performance_capabilities),caps=state.results?.performance_capabilities??state.meta?.performance_capabilities;
  $('performance-summary').replaceChildren(renderPerformance(caps,f.weights,state.needsIntent,current));
  $('performance-counts').textContent=`${current?'本次条件':'当前条件待验证；默认新机参考'}：${[['dxomark_photo','拍照'],['honor_of_kings_avg_fps','游戏'],['battery_mixed_usage_duration_min','续航']].map(([key,name])=>`${name} ${caps?.[key]?.eligible_model_count??'—'} 机型 / ${caps?.[key]?.eligible_sku_count??'—'} 配置`).join(' · ')}。各指标独立计数。`;
  $('method-status').textContent=`本次${f.method==='hybrid_rrf'?'实验混合排序':'默认 TOPSIS'} · ${f.expected_algorithm_version??'版本待读取'}`;
  const blockers=[];
  if(f.weights.battery_mixed_usage_duration_min>0)blockers.push('续航尚无对应实测，请选择“续航暂不计入”后重新确认，或保留需求等待资料。');
  if(f.weights.honor_of_kings_avg_fps>0&&f.game_scope!=='honor_of_kings')blockers.push('游戏范围尚未覆盖或确认。可明确改用王者荣耀作参考，或选择“游戏暂不计入”；原始意图仍保留。');
  $('performance-blocker').textContent=blockers.join(' ');
  const pendingBudget=needsPendingBudget&&budget.max===null&&!$('budget-unlimited').checked;
  $('generate').disabled=!state.release||!$('confirmed').checked||state.loading.recommend||pendingBudget;
  $('extract-needs').disabled=!state.meta?.features.needs_extraction||state.loading.needs||!$('needs-text').value.trim();
}
function clearResults(){
  $('results').replaceChildren();$('recommend-pagination').replaceChildren();$('downloads').hidden=true;
  $('decision-summary').replaceChildren(renderDecision(state));$('confirmed').checked=false;
  $('catalog-results').replaceChildren();$('catalog-pagination').replaceChildren();$('catalog-status').textContent='筛选已变更，请点击“应用上方筛选”。';
  if($('detail-dialog').open)$('detail-dialog').close();preferencesSummary();
}
function changed(){abortRequests();setPreferences(state,fields());clearResults();notify('条件已变更，旧推荐已清除，请重新检查并确认。');}
function compareCount(){$('compare-count').textContent=state.comparisonIds.length;}
function setView(next){
  view=next;for(const name of ['recommend','catalog','compare'])$(`${name}-view`).hidden=name!==view;
  $('recommend-results-view').hidden=view!=='recommend';
  $('needs-panel').hidden=view!=='recommend';$('steps').hidden=view!=='recommend';
  $('preferences-panel').hidden=view==='compare';
  for(const node of document.querySelectorAll('[data-view]'))node.setAttribute('aria-pressed',String(node.dataset.view===view));
  if(view==='catalog')loadCatalog(1);if(view==='compare')loadComparison();
}
function fillBrands(id,brands){const previous=selected(id);$(id).replaceChildren(...brands.map(brand=>{const option=element('option',brand);option.value=brand;option.selected=previous.includes(brand);return option;}));}
async function refreshMeta(initial=false){
  const ordinal=++metaSequence;
  try{
    const meta=await fetchJson('/api/v1/meta');if(ordinal!==metaSequence)return;
    const changedRelease=applyMeta(state,meta);
    if(changedRelease){abortRequests();clearResults();compareCount();$('compare-content').replaceChildren(renderComparison([]));}
    if(initial){for(const key of metrics)$(key).value=meta.defaults.weights[key]??0;$('mode').value=meta.defaults.mode;$('target-screen').value=meta.defaults.target_screen;}
    for(const option of $('method').options)option.disabled=Boolean(meta.algorithms)&&!meta.algorithms.some(a=>a.method===option.value);
    if($('method').selectedOptions[0]?.disabled)$('method').value='topsis';
    fillBrands('include-brands',meta.brands);fillBrands('exclude-brands',meta.brands);state.preferences=structuredClone(fields());preferencesSummary();
    $('needs-status').textContent=meta.features.offline_fixture?'离线测试夹具可用：仅用于本地验证，未调用 DeepSeek。':meta.features.needs_extraction?'可用：先理解需求，再检查下方条件与偏好。':'自然语言理解暂未启用，仍可直接手动填写。';
    $('needs-service-notice').textContent=meta.features.offline_fixture?'本地离线测试：使用确定性规则夹具解析需求，不调用付费服务，也不代表真实 DeepSeek 输出。':'点击后将这段购机需求发送给 DeepSeek，提取预算和偏好；你确认后，再由算法生成推荐。';
    const acceptance=meta.acceptance;
    $('release-status').textContent=`${meta.counts.phone_models} 机型 · ${meta.counts.skus} 配置 · 机器 ${acceptance.machine_acceptance||'未知'} / 总体 ${acceptance.overall_acceptance||'未知'} · WPS ${acceptance.wps_review||'未知'}`;
    $('release-info').replaceChildren(element('p',`数据 ${meta.release.dataset_version} / 规则 ${meta.release.feature_rule_version}`),element('p',`人工抽查仍待完成；当前发布未提供人工全通过证明。已关联 ${meta.features.image_models??0} 个机型展示图；${meta.features.offline_fixture?'需求理解为本地离线测试夹具':`DeepSeek ${meta.features.needs_extraction?'需求理解已配置':meta.features.llm?'已配置（仅解释）':'未启用'}`}；VIKOR 未实现。`),element('p','照片子分需协议可比；游戏仅限同条件王者荣耀实测帧率。1% Low 缺失，不推算完整游戏总分。'),element('p',`数据库指纹 ${meta.release.snapshot_sha256}`,'small'));
    if(!initial)notify(changedRelease?'数据或算法版本已变化，请重新确认并生成推荐。':'数据和算法状态已刷新，版本未变化。');
  }catch(error){if(ordinal!==metaSequence)return;abortRequests();state.release=null;setPreferences(state,fields());clearResults();$('release-status').textContent='数据服务暂不可用';notify(error.message,true);}
}
async function request(channel,path,options,onSuccess){
  controllers.get(channel)?.abort();const controller=new AbortController();controllers.set(channel,controller);
  const token=beginRequest(state,channel);$(channel==='catalog'?'catalog-results':channel==='recommend'?'results':channel==='detail'?'detail-content':'compare-content').setAttribute('aria-busy','true');preferencesSummary();
  try{
    const value=await fetchJson(path,{...options,signal:controller.signal});
    if(!acceptResponse(state,channel,token,value))return;
    if(value.release&&JSON.stringify(value.release)!==JSON.stringify(state.release)){await refreshMeta();notify('数据版本不同，请刷新并重新确认。',true);return;}
    onSuccess(value);
  }catch(error){
    if(error.name==='AbortError')return;
    if(!acceptResponse(state,channel,token,{error}))return;
    if(['release_changed','decision_changed','release_unavailable','algorithm_changed'].includes(error.code)){setPreferences(state,fields());clearResults();await refreshMeta();}
    notify(error.message,true);
  }finally{
    if(controllers.get(channel)===controller){controllers.delete(channel);for(const id of ['results','catalog-results','detail-content','compare-content'])$(id).setAttribute('aria-busy','false');preferencesSummary();}
  }
}
function addToCompare(id){
  if(!addComparison(state,id)){notify(state.comparisonIds.includes(id)?'该配置已在对比中。':'最多对比四个配置，请先移除一个。');return;}
  controllers.get('compare')?.abort();compareCount();notify(`已加入对比（${state.comparisonIds.length}/4）。`);renderCurrentCards();
}
function showNeeds(payload){
  const panel=$('needs-result');panel.replaceChildren(element('p',payload.notice));
  const extracted=payload.extraction;if(!extracted)return;
  if(extracted.budget.reference!==null)panel.append(element('p',`预算参考：${extracted.budget.reference} 元；参考值不会自动作为预算上限。`));
  const list=element('ul');const titles={price:'价格',weight:'轻巧',storage:'存储',refresh:'刷新率',screen:'尺寸匹配',game:'游戏',photo:'拍照',battery:'续航'};
  for(const p of extracted.preferences)list.append(element('li',`${titles[p.criterion]??p.criterion}：${p.level}${p.verdict==='inferred'?' · 使用场景推断，待确认':''}${p.evidence?`（原话：${p.evidence}）`:'（没有明确提及）'}`));panel.append(list);
  if(extracted.game_use)panel.append(element('p',`原始游戏意图：${extracted.game_use.names?.join('、')||extracted.game_use.evidence||'游戏未明确'}。仅有王者荣耀测试；改用该参考或暂不计入均由你决定。`,'small'));
  for(const c of extracted.clarifications){const item=element('p',`待确认：${c.question}`,'warning');item.dataset.clarification=c.field;panel.append(item);}
  for(const n of extracted.unsupported_needs)panel.append(element('p',`未自动参与排序：${n.need}${n.evidence?`（原话：${n.evidence}）`:''}。请查看数据限制，或在下方手动设置可用指标。`,'warning'));
}
async function understandNeeds(event){
  event.preventDefault();const text=$('needs-text').value.trim();if(!text||!state.meta?.features.needs_extraction)return;
  controllers.get('needs')?.abort();const controller=new AbortController();controllers.set('needs',controller);
  const token=beginRequest(state,'needs');preferencesSummary();$('needs-status').textContent=state.meta?.features.offline_fixture?'离线测试夹具正在解析需求…':'DeepSeek 正在理解需求，请稍候…';
  try{
    const payload=await fetchJson('/api/v1/needs',{method:'POST',body:{text},signal:controller.signal});
    if(!acceptResponse(state,'needs',token,payload))return;
    const draft=draftFields(payload,fields());
    if(draft){
      abortRequests();for(const [id,value] of [['min-price',draft.needs.budget.min],['max-price',draft.needs.budget.max],['min-storage',draft.needs.min_storage],['target-screen',draft.target_screen]])$(id).value=value??'';
      for(const k of ['include','exclude'])for(const o of $(`${k}-brands`).options)o.selected=draft.needs.brands[k].includes(o.value);
      for(const key of metrics)$(key).value=draft.weights[key]??0;
      $('game-scope').value=draft.game_scope??'honor_of_kings';state.needsIntent=structuredClone(payload);
      needsPendingBudget=payload.extraction.clarifications.some(c=>c.field==='budget.max');$('budget-unlimited').checked=false;$('budget-unlimited-line').hidden=!needsPendingBudget;
      setPreferences(state,fields());clearResults();notify('已填入待确认草稿，请检查条件与权重；尚未生成推荐。');
    }
    showNeeds(payload);$('preferences-panel').open=true;
    $('needs-status').textContent=draft?'需求已理解，请检查下方表单并确认。':'未取得可靠草稿，请使用下方手动填写。';
  }catch(error){
    if(error.name==='AbortError'||!acceptResponse(state,'needs',token,{error}))return;
    $('needs-status').textContent=`${error.message}；仍可手动填写。`;$('preferences-panel').open=true;
  }finally{
    if(controllers.get('needs')===controller)controllers.delete('needs');preferencesSummary();
  }
}
function cardContext(id,explanations={},reasons={}){return {explanation:explanations[id],reason:reasons[id],method:state.results?.method,scoreLabel:state.results?.score_label,selected:state.comparisonIds.includes(id),onDetails:loadDetail,onCompare:addToCompare,onExplain:explain,llmEnabled:state.meta?.features.llm};}
function explain(id,container){
  if(!state.confirmedRequest||state.results?.status!=='ready')return;
  const body={request:structuredClone(state.confirmedRequest),fingerprints:{decision_sha256:state.results.decision_sha256,ranking_sha256:state.results.ranking_sha256},sku_id:id};
  notify('正在核验推荐证据并生成辅助解释…');
  request('explain','/api/v1/explain',{method:'POST',body},payload=>{
    if(!container.isConnected)return;
    container.querySelector('.ai-explanation')?.remove();container.append(element('p',`${payload.text}\n${payload.notice??''}`,'explanation ai-explanation'));notify(payload.mode==='template'?'已使用模板解释；排名未变。':'证据已校验；辅助解释不改变排名。');
  });
}
function renderCurrentCards(){
  if(state.results?.ranked)$('results').replaceChildren(...state.results.ranked.map(row=>renderCard(row,cardContext(row.sku_id,state.results.explanations,state.results.recommendation_reasons))));
  if(state.responses.catalog)$('catalog-results').replaceChildren(...state.responses.catalog.items.map(row=>renderCard(row,{...cardContext(row.sku_id),category:state.responses.catalog.category})));
}
function pagination(id,page,total,size,onPage){
  const pages=Math.max(1,Math.ceil(total/size));const previous=button('上一页',()=>onPage(page-1)),next=button('下一页',()=>onPage(page+1));previous.disabled=page<=1;next.disabled=page>=pages;
  $(id).replaceChildren(previous,element('span',`${page} / ${pages} 页 · ${total} 个配置`,'small'),next);
}
function generate(page=1){
  if(!state.confirmedRequest){notify('请先检查条件与权重并勾选确认。',true);return;}
  notify('正在按完整可比候选集计算…');$('results').replaceChildren();$('downloads').hidden=true;
  request('recommend',`/api/v1/recommend?page=${page}&page_size=12`,{method:'POST',body:state.confirmedRequest},payload=>{
    notify('');$('decision-summary').replaceChildren(renderDecision(state));renderCurrentCards();$('downloads').hidden=payload.status!=='ready';
    if(payload.status==='ready')pagination('recommend-pagination',payload.page,payload.ranked_total,payload.page_size,generate);
    else{$('recommend-pagination').replaceChildren();$('results').append(element('p','可继续在手机库查看记录与来源；系统没有为不可比数据生成成绩。','empty'));}
  });
}
function categoryControls(payload){
  const names={all:'全部',gaming:'游戏手机',senior:'长辈友好',photography:'拍照手机'};
  for(const node of document.querySelectorAll('[data-category]')){
    const category=node.dataset.category,count=payload?.category_counts?.find(c=>c.id===category);
    const total=category==='all'?payload?.category_counts?.[0]?.model_count:count?.[$('category-status').value];
    node.textContent=`${names[category]}${total===undefined?'':` · ${total}`}`;
    node.setAttribute('aria-pressed',String(category===catalogCategory));
  }
  $('category-status-control').hidden=catalogCategory==='all';
  if(payload?.category_notice)$('category-help').textContent=payload.category_notice;
}
function loadCatalog(page=1){
  if(!state.release)return;const f=fields(),q=new URLSearchParams({mode:f.mode,page:String(page),page_size:'12',q:$('search').value});
  if(catalogCategory!=='all'){q.set('category',catalogCategory);q.set('category_status',$('category-status').value);}
  for(const [key,value] of [['min_price',f.needs.budget.min],['max_price',f.needs.budget.max],['min_storage',f.needs.min_storage]])if(value!==null)q.set(key,String(value));
  for(const key of ['include','exclude'])for(const brand of f.needs.brands[key])q.append(key,brand);
  categoryControls();$('catalog-status').textContent='正在读取目录…';$('catalog-results').replaceChildren();$('catalog-pagination').replaceChildren();
  request('catalog',`/api/v1/catalog?${q}`,{},payload=>{
    $('catalog-status').textContent=`共 ${payload.total} 个准确配置 · 价格为记录快照`;
    categoryControls(payload);renderCurrentCards();
    if(!payload.total){
      const empty=element('div',undefined,'empty');
      empty.append(element('p',catalogCategory==='all'?'当前条件下没有配置，请调整筛选或搜索。':$('category-status').value==='evidence_available'?'当前条件下没有具备所需评测资料的机型。这不表示其他手机不适合，可查看待评估机型或调整预算等条件。':'当前条件下没有待评估机型，请调整筛选或查看已有资料。'));
      if(catalogCategory!=='all')empty.append(button($('category-status').value==='evidence_available'?'查看待评估机型':'查看有资料机型',()=>{$('category-status').value=$('category-status').value==='evidence_available'?'pending_review':'evidence_available';loadCatalog(1);}));
      $('catalog-results').append(empty);
    }
    pagination('catalog-pagination',payload.page,payload.total,payload.page_size,loadCatalog);
  });
}
function loadDetail(id){
  $('detail-content').replaceChildren(element('p','正在读取配置与来源…'));if(!$('detail-dialog').open)$('detail-dialog').showModal();
  request('detail',`/api/v1/skus/${encodeURIComponent(id)}`,{},payload=>$('detail-content').replaceChildren(renderDetail(payload)));
}
function loadComparison(){
  if(!state.comparisonIds.length){$('compare-content').replaceChildren(renderComparison([]));return;}
  const ids=[...state.comparisonIds];$('compare-content').replaceChildren(element('p','正在按准确配置关联对比…'));
  request('compare','/api/v1/compare',{method:'POST',body:{sku_ids:ids}},payload=>{
    const controls=element('div',undefined,'actions');for(const item of payload.items)controls.append(button(`移除 ${item.model.display_name} · ${item.sku.storage_raw}`,()=>{removeComparison(state,item.sku.sku_id);controllers.get('compare')?.abort();compareCount();renderCurrentCards();loadComparison();}));
    $('compare-content').replaceChildren(controls,renderComparison(payload.items));
    for(const item of payload.items){const sourceButton=button(`查看来源 · ${item.sku.sku_id}`,()=>loadDetail(item.sku.sku_id));$('compare-content').append(sourceButton);}
  });
}
async function download(format){
  if(!state.confirmedRequest||state.results?.status!=='ready')return;
  controllers.get('download')?.abort();const controller=new AbortController();controllers.set('download',controller);const token=beginRequest(state,'download');
  const body={request:structuredClone(state.confirmedRequest),fingerprints:{decision_sha256:state.results.decision_sha256,ranking_sha256:state.results.ranking_sha256}};
  try{
    const blob=await fetchDownload(format,body,{signal:controller.signal});if(!acceptResponse(state,'download',token,{}))return;
    const url=URL.createObjectURL(blob),link=element('a');link.href=url;link.download=`zhixuan-recommendation.${format}`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);notify('已导出完整排名，非当前页；数值与证据按服务器重新核验。');
  }catch(error){if(error.name==='AbortError'||!acceptResponse(state,'download',token,{error}))return;if(['decision_changed','release_changed','release_unavailable','algorithm_changed'].includes(error.code)){setPreferences(state,fields());clearResults();await refreshMeta();}notify(error.message,true);}
  finally{if(controllers.get('download')===controller)controllers.delete('download');}
}
for(const node of document.querySelectorAll('[data-view]'))node.addEventListener('click',()=>setView(node.dataset.view));
$('preferences').addEventListener('input',event=>{
  if(event.target.id==='max-price'&&event.target.value.trim())$('budget-unlimited').checked=false;
  if(event.target.id==='budget-unlimited'&&event.target.checked)$('max-price').value='';
  event.target.id==='confirmed'&&event.target.checked?preferencesSummary():changed();
});
$('preferences').addEventListener('submit',event=>{event.preventDefault();if(!$('confirmed').checked||!state.release||$('generate').disabled)return;state.confirmedRequest={...structuredClone(fields()),confirmed:true,expected_release:structuredClone(state.release)};for(const item of $('needs-result').querySelectorAll('[data-clarification]'))item.textContent=`已检查确认：${item.textContent.replace(/^待确认：/,'')}`;if($('needs-result').childElementCount)$('needs-status').textContent='表单已确认，推荐将按下方结果显示。';$('preferences-panel').open=false;setView('recommend');generate();});
$('natural-needs').addEventListener('submit',understandNeeds);
$('needs-text').addEventListener('input',()=>{abortRequests();reviseNeedsText(state,fields());clearResults();$('needs-result').replaceChildren();$('needs-status').textContent='需求文字已改变，请重新理解并确认；也可检查表单后手动确认。';});
for(const node of document.querySelectorAll('[data-not-count]'))node.addEventListener('click',()=>{
  const reset=withoutCriterion(fields(),node.dataset.notCount);$(node.dataset.notCount).value=reset.weights[node.dataset.notCount];changed();
  notify('本次已暂不计入该项；原始需求和硬条件已保留，请重新检查并确认。');
});
$('manual-needs').addEventListener('click',()=>{$('preferences-panel').open=true;$('mode').focus();});
$('edit-preferences').addEventListener('click',()=>{$('preferences-panel').open=true;$('mode').focus();});
$('refresh-meta').addEventListener('click',()=>refreshMeta());$('catalog-search').addEventListener('submit',event=>{event.preventDefault();loadCatalog();});$('apply-catalog').addEventListener('click',()=>loadCatalog());
for(const node of document.querySelectorAll('[data-category]'))node.addEventListener('click',()=>{catalogCategory=node.dataset.category;loadCatalog(1);});
$('category-status').addEventListener('change',()=>loadCatalog(1));
$('close-detail').addEventListener('click',()=>{$('detail-dialog').close();controllers.get('detail')?.abort();state.sequence.detail++;});
$('clear-comparison').addEventListener('click',()=>{for(const id of [...state.comparisonIds])removeComparison(state,id);controllers.get('compare')?.abort();compareCount();renderCurrentCards();loadComparison();});
for(const node of document.querySelectorAll('[data-download]'))node.addEventListener('click',()=>download(node.dataset.download));
$('decision-summary').replaceChildren(renderDecision(state));refreshMeta(true);
