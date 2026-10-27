import {createState,setPreferences,applyMeta,beginRequest,acceptResponse,addComparison,removeComparison} from './state.mjs';
import {fetchJson,fetchDownload} from './api.mjs';
import {element,button,labels,renderCard,renderDecision,renderComparison,renderDetail,renderPerformance} from './render.mjs';
import {draftFields,pendingReview} from './needs.mjs';
import {requestFields,withoutCriterion,reviseNeedsText,preferenceMetrics,importanceLevels,importanceWeight,importancePosition} from './preferences.mjs';
import {uniqueModels,diverseModels,renderHero,renderRail,renderFeatured,renderComparisonBoard,renderData} from './alpine.mjs';
import {renderReviews,renderReviewError} from './reviews.mjs';
let detailItem=null,reviewMode=false;

const $=id=>document.getElementById(id),state=createState(),controllers=new Map();
let view='recommend',metaSequence=0,needsPendingBudget=false,catalogCategory='all';
let needsReview={report:null,acknowledged:new Set(),boxes:[],status:null};
let discovery=[],discoverySequence=0,featuredIndex=0,featuredSequence=0;
const featuredDetails=new Map();
const metrics=preferenceMetrics;
function notify(message,error=false){const node=$('message');node.textContent=message;node.hidden=!message;node.setAttribute('role',error?'alert':'status');if(error)node.focus();}
function abortRequests(){for(const controller of controllers.values())controller.abort();controllers.clear();}
function numeric(id){return $(id).value.trim()===''?null:Number($(id).value);}
function selected(id){return [...$(id).selectedOptions].map(option=>option.value);}
function fields(){return requestFields({needs:{budget:{min:numeric('min-price'),max:numeric('max-price')},min_storage:numeric('min-storage'),brands:{include:selected('include-brands'),exclude:selected('exclude-brands')}},
  defaulted_metrics:metrics.filter(key=>Number($(key).value)===1),weights:Object.fromEntries(metrics.map(key=>[key,importanceWeight(key,Number($(key).value))])),target_screen:numeric('target-screen'),mode:$('mode').value,method:$('method').value,evaluation_mode:$('evaluation-mode').value,game_scope:$('game-scope').value},state.meta);}
function preferencesSummary(){
  const f=fields(),budget=f.needs.budget;
  $('preferences-summary').textContent=`${{new:'新机',used:'二手',all:'全部成色（含未知）'}[f.mode]} · ${budget.min??'不限'}—${budget.max??'不限'} 元 · ${f.needs.min_storage?`${f.needs.min_storage}GB 起`:'存储不限'} · ${f.needs.brands.include.join('、')||'全部品牌'}`;
  for(const key of metrics){const text=`${importanceLevels[$(key).value]} · 权重 ${importanceWeight(key,Number($(key).value))}`;document.querySelector(`output[for="${key}"]`).textContent=text;$(key).setAttribute("aria-valuetext",text);}
  const reference=f.evaluation_mode==='spec_reference',current=Boolean(state.results?.performance_capabilities),caps=state.results?.performance_capabilities??state.meta?.performance_capabilities;
  $('performance-summary').replaceChildren(renderPerformance(caps,f.weights,state.needsIntent,current,f.evaluation_mode));
  $('performance-counts').textContent=reference?`七项规格参考分按固定规则换算，缺失显示“资料不足”。参考规则 ${f.expected_reference_rule_version??'待读取'}；具体输入与换算依据见推荐卡片。`:`${current?'本次条件':'当前条件待验证；默认新机参考'}：${[['dxomark_photo','拍照'],['honor_of_kings_avg_fps','游戏'],['battery_mixed_usage_duration_min','实测续航（不评分）']].map(([key,name])=>`${name} ${caps?.[key]?.eligible_model_count??'—'} 机型 / ${caps?.[key]?.eligible_sku_count??'—'} 配置`).join(' · ')}。各指标独立计数。`;
  $('preference-policy').textContent=`按重视程度选择五个等级，各维度按对应等级赋权。未提及保留非零系统初值，拍照和游戏各为 1；不在意表示主动不计入。价格和品牌只作硬性筛选。${reference?'规格资料不足时不会补零。':'未提及的拍照与游戏若实测资料不完整，将保留为参考，并明确说明本次未计入综合分。'}`;
  $('dxomark_photo-help').textContent=reference?'按主摄像素与超广角、长焦规格参考，不代表成像质量；资料缺失不补零。':'仅比较同协议 DXOMARK 照片子分；缺失不补零。';
  $('honor_of_kings_avg_fps-help').textContent=reference?'规格估算：80% 处理器参考分 + 20% 电池容量参考分；不代表实测帧率或实际续航。':'仅比较同条件王者荣耀平均帧率，不代表其他游戏。';
  $('screen_size_inch-help').textContent='越接近下方理想尺寸越优先；这是尺寸匹配，不代表屏幕显示质量。';
  $('game-scope-control').hidden=reference;$('game-scope').disabled=reference;
  $('method-status').textContent=`${reference?'规格参考分':'可比实测资料'} · 本次${f.method==='hybrid_rrf'?'实验混合排序':'默认 TOPSIS'} · ${f.expected_algorithm_version??'版本待读取'}`;
  const blockers=[];
  if(!reference&&f.weights.battery_mixed_usage_duration_min>0)blockers.push('续航尚无对应实测，请选择“续航暂不计入”后重新确认，或保留需求等待资料。');
  if(!reference&&f.weights.honor_of_kings_avg_fps>0&&!f.defaulted_metrics.includes('honor_of_kings_avg_fps')&&f.game_scope!=='honor_of_kings')blockers.push('游戏范围尚未覆盖或确认。可明确改用王者荣耀作参考，或选择“游戏暂不计入”；原始意图仍保留。');
  $('performance-blocker').textContent=blockers.join(' ');
  const pendingBudget=needsPendingBudget&&budget.max===null&&!$('budget-unlimited').checked;
  const pendingExceptions=pendingReview(needsReview.report,needsReview.acknowledged).length;
  $('confirmed').disabled=pendingExceptions>0;
  if(needsReview.status)needsReview.status.textContent=pendingExceptions?`还有 ${pendingExceptions} 项解析异常待核对；核对后再勾选整体确认。`:'异常已逐项核对，请检查下方条件与偏好后勾选整体确认。';
  $('generate').disabled=!state.release||!$('confirmed').checked||state.loading.recommend||state.loading.needs||pendingBudget||pendingExceptions>0||blockers.length>0;
  $('extract-needs').disabled=!state.meta?.features.needs_extraction||state.loading.needs||!$('needs-text').value.trim();
}
function clearResults(){
  $('results').replaceChildren();$('more-results').replaceChildren();$('more-recommendations').hidden=true;$('decision-details').hidden=true;$('recommend-pagination').replaceChildren();$('downloads').hidden=true;
  $('decision-summary').replaceChildren(renderDecision(state));$('confirmed').checked=false;
  $('catalog-results').replaceChildren();$('catalog-pagination').replaceChildren();$('catalog-status').textContent='筛选已变更，请点击“应用上方筛选”。';
  $('catalog-featured').replaceChildren();if($('detail-dialog').open)$('detail-dialog').close();preferencesSummary();renderDiscovery();renderSupportingPhones();
}
function changed(){abortRequests();needsReview.acknowledged.clear();for(const box of needsReview.boxes)box.checked=false;setPreferences(state,fields());clearResults();notify('条件已变更，旧推荐已清除，请重新检查并确认。');}
function compareCount(){$('compare-count').textContent=state.comparisonIds.length;}
function setView(next){
  notify('');$('top')?.scrollIntoView?.({block:'start'});
  view=next;for(const name of ['recommend','catalog','compare','data'])$(`${name}-view`).hidden=name!==view;
  document.body.dataset.currentView=view;$('recommend-workspace').hidden=view!=='recommend';
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
    if(initial){for(const key of metrics)$(key).value=1;$('mode').value=meta.defaults.mode;$('target-screen').value=meta.defaults.target_screen;$('evaluation-mode').value=meta.defaults.evaluation_mode??'spec_reference';}
    for(const option of $('method').options)option.disabled=Boolean(meta.algorithms)&&!meta.algorithms.some(a=>a.method===option.value);
    if($('method').selectedOptions[0]?.disabled)$('method').value='topsis';
    fillBrands('include-brands',meta.brands);fillBrands('exclude-brands',meta.brands);state.preferences=structuredClone(fields());preferencesSummary();
    $('needs-status').textContent=meta.features.offline_fixture?'离线测试夹具可用：仅用于本地验证，未调用 DeepSeek。':meta.features.needs_extraction?'可用：先理解需求，再检查下方条件与偏好。':'自然语言理解暂未启用，仍可直接手动填写。';
    $('needs-service-notice').textContent=meta.features.offline_fixture?'本地离线测试：使用确定性规则夹具解析需求，不调用付费服务，也不代表真实 DeepSeek 输出。':'点击后将这段购机需求发送给 DeepSeek，提取预算和偏好；你确认后，再由算法生成推荐。';
    const acceptance=meta.acceptance;
    $('release-status').textContent=`${meta.counts.phone_models} 机型 · ${meta.counts.skus} 配置 · 机器 ${acceptance.machine_acceptance||'未知'} / 总体 ${acceptance.overall_acceptance||'未知'} · WPS ${acceptance.wps_review||'未知'}`;
    $('release-info').replaceChildren(element('p',`数据 ${meta.release.dataset_version} / 规则 ${meta.release.feature_rule_version} / 规格参考 ${meta.reference_rule_version??'版本未提供'}`),element('p',`人工抽查仍待完成；当前发布未提供人工全通过证明。已关联 ${meta.features.image_models??0} 个机型展示图；${meta.features.offline_fixture?'需求理解为本地离线测试夹具':`DeepSeek ${meta.features.needs_extraction?'需求理解已配置':meta.features.llm?'已配置（仅解释）':'未启用'}`}；VIKOR 未实现。`),element('p','默认七项规格参考分为固定规则估算，不是实测体验分。可选实测模式要求照片子分协议可比，游戏仅限同条件王者荣耀平均帧率；1% Low 缺失，不推算完整游戏总分。'),element('p',`数据库指纹 ${meta.release.snapshot_sha256}`,'small'));
    const catalogCount=`${meta.counts.phone_models} 个机型 · ${meta.counts.skus} 个配置`;
    $('home-catalog-count').textContent=catalogCount;$('catalog-count').textContent=catalogCount;
    $('data-content').replaceChildren(renderData(meta));
    if(initial||changedRelease){discovery=[];featuredDetails.clear();loadDiscovery();}
    if(!initial)notify(changedRelease?'数据或算法版本已变化，请重新确认并生成推荐。':'数据和算法状态已刷新，版本未变化。');
  }catch(error){if(ordinal!==metaSequence)return;abortRequests();state.release=null;setPreferences(state,fields());clearResults();$('release-status').textContent='数据服务暂不可用';notify(error.message,true);}
}
async function request(channel,path,options,onSuccess,onError){
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
    if(['release_changed','decision_changed','release_unavailable','algorithm_changed','reference_rule_changed'].includes(error.code)){setPreferences(state,fields());clearResults();await refreshMeta();}
    notify(error.message,true);onError?.(error);
  }finally{
    if(controllers.get(channel)===controller){controllers.delete(channel);for(const id of ['results','catalog-results','detail-content','compare-content'])$(id).setAttribute('aria-busy','false');preferencesSummary();}
  }
}
function addToCompare(id){
  if(!addComparison(state,id)){notify(state.comparisonIds.includes(id)?'该配置已在对比中。':'最多对比四个配置，请先移除一个。');return;}
  controllers.get('compare')?.abort();compareCount();notify(`已加入对比（${state.comparisonIds.length}/4）。`);renderCurrentCards();if(view==='compare')loadComparison();
}
function showNeeds(payload){
  const panel=$('needs-result');panel.replaceChildren(element('p',payload.notice));
  const extracted=payload.extraction;if(!extracted)return;
  if(extracted.verification){
    const report=extracted.verification;
    if(report.issues.length){
      const group=element('fieldset',undefined,'needs-review');group.append(element('legend','解析结果需要核对'),element('p','请先检查下方表单，逐项核对这些异常。修改条件会撤销已勾选的核对。'));
      const names={'budget':'预算','budget.min':'预算下限','budget.max':'预算上限','budget.reference':'预算参考','min_storage':'最低存储','target_screen':'屏幕尺寸','brands.include':'包含品牌','brands.exclude':'排除品牌','preferences.weight':'轻便偏好','preferences.storage':'存储偏好','preferences.screen':'屏幕尺寸偏好','preferences.photo':'拍照偏好','preferences.game':'游戏偏好','preferences.battery':'电池容量偏好','preferences.chipset':'处理器偏好'};
      const valueText=value=>value===null?'未提取或待填写':Array.isArray(value)?value.join('、')||'不限品牌':typeof value==='object'?`${value.min??'不限'}—${value.max??'不限'} 元`:String(value);
      for(const issue of report.issues){
        const row=element('div',undefined,'needs-review-item');
        row.append(element('p',`${names[issue.field]??'需求条件'}：${issue.message}`,'warning'),element('p',issue.model_value===issue.rule_value?`解析建议：${valueText(issue.model_value)}，需你核对。`:`原解析：${valueText(issue.model_value)}；待确认建议：${valueText(issue.rule_value)}。`),element('p',issue.evidence?`相关原话：「${issue.evidence}」`:'未找到可核对的对应原话。','small'));
        const label=element('label',undefined,'needs-review-choice'),box=element('input');box.type='checkbox';box.id=`needs-review-${issue.id}`;
        box.addEventListener('change',()=>{box.checked?needsReview.acknowledged.add(issue.id):needsReview.acknowledged.delete(issue.id);abortRequests();setPreferences(state,fields());clearResults();});
        label.append(box,element('span',`我已核对${names[issue.field]??'这项条件'}，接受下方表单当前填写的值`));row.append(label);group.append(row);needsReview.boxes.push(box);
      }
      needsReview.status=element('p',undefined,'warning');needsReview.status.setAttribute('role','status');group.append(needsReview.status);panel.append(group);
    }else panel.append(element('p','针对性核验未发现已覆盖类型的冲突；请继续检查预算、品牌和七项偏好。','small'));
  }
  if(payload.draft?.budget_inferred)panel.append(element('p',`预算为程序推断：${payload.draft.needs.budget.min}—${payload.draft.needs.budget.max} 元（参考锚点 ${extracted.budget.reference} 元）。可在硬性条件中修改，确认后才用于筛选。`));
  else if(extracted.budget.reference!==null)panel.append(element('p',`预算参考锚点：${extracted.budget.reference} 元；实际筛选范围以你确认的硬性条件为准。`));
  const list=element('ul');const titles={weight:'重量',storage:'存储',screen:'屏幕尺寸',game:'游戏',photo:'拍照',battery:'电池容量',chipset:'处理器档位'};
  for(const p of extracted.preferences)list.append(element('li',`${titles[p.criterion]??p.criterion}：${p.level}${p.verdict==='inferred'?' · 使用场景推断，待确认':''}${p.evidence?`（原话：${p.evidence}）`:'（没有明确提及）'}`));panel.append(list);
  if(extracted.game_use)panel.append(element('p',`原始游戏意图：${extracted.game_use.names?.join('、')||extracted.game_use.evidence||'游戏未明确'}。规格模式用处理器与电池容量估算；实测模式目前仅有王者荣耀资料，是否改用该参考由你决定。`,'small'));
  for(const c of extracted.clarifications){const item=element('p',`待确认：${c.question}`,'warning');item.dataset.clarification=c.field;panel.append(item);}
  for(const n of extracted.unsupported_needs)panel.append(element('p',`未自动参与排序：${n.need}${n.evidence?`（原话：${n.evidence}）`:''}。请查看数据限制，或在下方手动设置可用指标。`,'warning'));
}
async function understandNeeds(event){
  event.preventDefault();const text=$('needs-text').value.trim();if(!text||!state.meta?.features.needs_extraction)return;
  // Re-understanding invalidates the old decision even if the HTTP request fails.
  abortRequests();needsReview={report:null,acknowledged:new Set(),boxes:[],status:null};
  state.needsIntent=null;setPreferences(state,fields());clearResults();$('needs-result').replaceChildren();
  controllers.get('needs')?.abort();const controller=new AbortController();controllers.set('needs',controller);
  const token=beginRequest(state,'needs');preferencesSummary();$('needs-status').textContent=state.meta?.features.offline_fixture?'离线测试夹具正在解析需求…':'DeepSeek 正在理解需求，请稍候…';
  try{
    const payload=await fetchJson('/api/v1/needs',{method:'POST',body:{text},signal:controller.signal});
    if(!acceptResponse(state,'needs',token,payload))return;
    needsReview={report:payload.extraction?.verification??null,acknowledged:new Set(),boxes:[],status:null};
    // Even a failed replacement invalidates the previous ranking and confirmation.
    abortRequests();setPreferences(state,fields());clearResults();
    const draft=draftFields(payload,fields());
    if(draft){
      abortRequests();for(const [id,value] of [['min-price',draft.needs.budget.min],['max-price',draft.needs.budget.max],['min-storage',draft.needs.min_storage],['target-screen',draft.target_screen]])$(id).value=value??'';
      for(const k of ['include','exclude'])for(const o of $(`${k}-brands`).options)o.selected=draft.needs.brands[k].includes(o.value);
      for(const key of metrics){const criterion={storage_gb:'storage',weight_g:'weight',battery_capacity_mah:'battery',chipset_tier:'chipset',screen_size_inch:'screen',dxomark_photo:'photo',honor_of_kings_avg_fps:'game'}[key];const level=payload.extraction?.preferences?.find(p=>p.criterion===criterion)?.level;$(key).value=importancePosition(key,draft.weights[key]??0,level);}
      $('game-scope').value=draft.game_scope??'honor_of_kings';$('evaluation-mode').value=draft.evaluation_mode;state.needsIntent=structuredClone(payload);
      needsPendingBudget=payload.extraction.clarifications.some(c=>c.field==='budget.max');$('budget-unlimited').checked=false;$('budget-unlimited-line').hidden=!needsPendingBudget;
      setPreferences(state,fields());clearResults();notify('已填入待确认草稿，请检查条件与权重；尚未生成推荐。');
    }
    showNeeds(payload);$('preferences-panel').open=true;$('needs-notices').open=true;
    $('needs-status').textContent=draft?'需求已理解，请检查下方表单并确认。':'未取得可靠草稿，请使用下方手动填写。';
  }catch(error){
    if(error.name==='AbortError'||!acceptResponse(state,'needs',token,{error}))return;
    $('needs-status').textContent=`${error.message}；仍可手动填写。`;$('preferences-panel').open=true;
  }finally{
    if(controllers.get('needs')===controller)controllers.delete('needs');preferencesSummary();
  }
}
function cardContext(id,explanations={},reasons={}){return {explanation:explanations[id],reason:reasons[id],method:state.results?.method,scoreLabel:state.results?.score_label,referenceRuleVersion:state.results?.reference_rule_version,selected:state.comparisonIds.includes(id),onDetails:loadDetail,onCompare:addToCompare,onExplain:explain,llmEnabled:state.meta?.features.llm};}
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
  if(state.results?.ranked){
    const rows=state.results.ranked,render=row=>renderCard(row,cardContext(row.sku_id,state.results.explanations,state.results.recommendation_reasons));
    $('results-title').textContent='为你推荐';$('results-subtitle').textContent='基于你已确认的条件，说明值得考虑的理由与取舍。';
    const primary=uniqueModels(rows).slice(0,3),selected=new Set(primary.map(row=>row.sku_id)),remaining=rows.filter(row=>!selected.has(row.sku_id));$('results').replaceChildren(...primary.map(render));$('more-results').replaceChildren(...remaining.map(render));$('more-recommendations').hidden=!remaining.length;$('decision-details').hidden=false;
  }else renderDiscovery();
  if(state.responses.catalog)renderCatalogCards();
  renderSupportingPhones();
}
function renderDiscovery(){
  if(state.results)return;
  $('results-title').textContent='先看看这些机型';$('results-subtitle').textContent='来自真实手机库；确认你的偏好后，再生成专属推荐。';
  $('results').replaceChildren(...uniqueModels(discovery).slice(0,3).map(row=>renderCard(row,{...cardContext(row.sku_id),discovery:true})));
  if(!discovery.length)$('results').append(element('p',state.release?'正在读取真实机型…':'数据连接后展示真实机型。','empty'));
}
function renderSupportingPhones(){
  const rows=state.results?.ranked?.length?state.results.ranked:discovery;
  $('tradeoff-items').replaceChildren(...renderRail(rows,{onDetails:loadDetail}));
  $('compare-suggestions').replaceChildren(...renderRail(discovery.filter(row=>!state.comparisonIds.includes(row.sku_id)),{onCompare:addToCompare,selected:state.comparisonIds}));
}
async function loadDiscovery(){
  const ordinal=++discoverySequence,release=JSON.stringify(state.release);
  try{const payload=await fetchJson('/api/v1/catalog?mode=new&page=1&page_size=100');
    if(ordinal!==discoverySequence||release!==JSON.stringify(state.release)||!Array.isArray(payload.items))return;
    if(payload.release&&JSON.stringify(payload.release)!==release)return;
    discovery=diverseModels(payload.items);$('hero-phones').replaceChildren(renderHero(discovery));renderDiscovery();renderSupportingPhones();
  }catch(error){if(ordinal!==discoverySequence)return;$('results').replaceChildren(element('p',`手机库暂未载入：${error.message}。可刷新数据状态后重试。`,'empty'));}
}
function catalogRows(){const rows=[...(state.responses.catalog?.items??[])],sort=$('catalog-sort').value;if(sort==='price-asc')rows.sort((a,b)=>(a.price_cny??Infinity)-(b.price_cny??Infinity));if(sort==='price-desc')rows.sort((a,b)=>(b.price_cny??-Infinity)-(a.price_cny??-Infinity));return rows;}
function renderCatalogCards(){
  const rows=catalogRows();if(!rows.length){$('catalog-featured').replaceChildren();return;}
  featuredIndex=Math.min(featuredIndex,rows.length-1);const feature=rows[featuredIndex];
  const secondary=uniqueModels(rows.filter(row=>row.model_id!==feature.model_id)),shown=secondary.slice(0,4),seen=new Set([feature.sku_id,...shown.map(row=>row.sku_id)]);
  const context=row=>({...cardContext(row.sku_id),category:state.responses.catalog.category});
  $('catalog-results').replaceChildren(...shown.map(row=>renderCard(row,context(row))));
  const remaining=rows.filter(row=>!seen.has(row.sku_id));if(remaining.length){const more=element('details',undefined,'catalog-more');more.append(element('summary',`查看本页其他 ${remaining.length} 个准确配置`));const cards=element('div',undefined,'cards');cards.append(...remaining.map(row=>renderCard(row,context(row))));more.append(cards);$('catalog-results').append(more);}
  const showFeature=()=>{const current=catalogRows()[featuredIndex];if(!current)return;$('catalog-featured').replaceChildren(renderFeatured(current,featuredDetails.get(current.sku_id),{...cardContext(current.sku_id),onPrevious:()=>{featuredIndex=(featuredIndex-1+rows.length)%rows.length;renderCatalogCards();},onNext:()=>{featuredIndex=(featuredIndex+1)%rows.length;renderCatalogCards();}}));};
  showFeature();
  if(!featuredDetails.has(feature.sku_id)){const ordinal=++featuredSequence;fetchJson(`/api/v1/skus/${encodeURIComponent(feature.sku_id)}`).then(item=>{if(ordinal!==featuredSequence||!state.release||JSON.stringify(item.release)!==JSON.stringify(state.release))return;featuredDetails.set(feature.sku_id,item);if(catalogRows()[featuredIndex]?.sku_id===feature.sku_id)showFeature();}).catch(()=>{});}
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
  if(!state.release)return;const f=fields(),q=new URLSearchParams({mode:f.mode,page:String(page),page_size:'24',q:$('search').value});
  if(catalogCategory!=='all'){q.set('category',catalogCategory);q.set('category_status',$('category-status').value);}
  for(const [key,value] of [['min_price',f.needs.budget.min],['max_price',f.needs.budget.max],['min_storage',f.needs.min_storage]])if(value!==null)q.set(key,String(value));
  for(const key of ['include','exclude'])for(const brand of f.needs.brands[key])q.append(key,brand);
  featuredIndex=0;featuredSequence++;categoryControls();$('catalog-status').textContent='正在读取目录…';$('catalog-results').replaceChildren();$('catalog-featured').replaceChildren();$('catalog-pagination').replaceChildren();
  $('catalog-applied-filters').textContent=`当前筛选：${$('preferences-summary').textContent}${f.needs.brands.exclude.length?` · 排除 ${f.needs.brands.exclude.join('、')}`:''}。下方为准确配置；分类计数按机型去重。`;
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
  detailItem=null;reviewMode=false;$('detail-title').textContent='配置详情与来源';$('view-buyer-reviews').textContent='查看淘宝买家评价';$('view-buyer-reviews').disabled=true;$('view-buyer-reviews').setAttribute('aria-pressed','false');
  $('detail-content').replaceChildren(element('p','正在读取配置与来源…'));if(!$('detail-dialog').open)$('detail-dialog').showModal();
  request('detail',`/api/v1/skus/${encodeURIComponent(id)}`,{},payload=>{detailItem=payload;$('detail-content').replaceChildren(renderDetail(payload));$('view-buyer-reviews').disabled=false;});
}
function loadBuyerReviews(page=1,grade='all'){
  if(!detailItem)return;reviewMode=true;$('detail-title').textContent='淘宝买家评价';$('view-buyer-reviews').textContent='返回配置详情';$('view-buyer-reviews').setAttribute('aria-pressed','true');
  $('detail-dialog').scrollTop=0;$('detail-content').replaceChildren(element('p','正在读取淘宝买家评价…','review-loading'));
  const id=detailItem.sku.sku_id;
  request('detail',`/api/v1/skus/${encodeURIComponent(id)}/reviews?page=${page}&page_size=20&grade=${grade}`,{},payload=>{
    $('detail-content').replaceChildren(renderReviews(payload,{onFilter:value=>loadBuyerReviews(1,value),onPage:value=>loadBuyerReviews(value,grade)}));
    $('detail-title').focus();
  },error=>$('detail-content').replaceChildren(renderReviewError(error.message,()=>loadBuyerReviews(page,grade))));
}
function loadComparison(){
  const context={onDetails:loadDetail,onAdd:()=>setView('catalog'),onRemove:id=>{removeComparison(state,id);controllers.get('compare')?.abort();compareCount();renderCurrentCards();loadComparison();}};
  if(!state.comparisonIds.length){$('compare-content').replaceChildren(renderComparisonBoard([],context));renderSupportingPhones();return;}
  const ids=[...state.comparisonIds];$('compare-content').replaceChildren(element('p','正在按准确配置关联对比…'));
  request('compare','/api/v1/compare',{method:'POST',body:{sku_ids:ids}},payload=>{
    $('compare-content').replaceChildren(renderComparisonBoard(payload.items,context));renderSupportingPhones();
  });
}
async function download(format){
  if(!state.confirmedRequest||state.results?.status!=='ready')return;
  controllers.get('download')?.abort();const controller=new AbortController();controllers.set('download',controller);const token=beginRequest(state,'download');
  const body={request:structuredClone(state.confirmedRequest),fingerprints:{decision_sha256:state.results.decision_sha256,ranking_sha256:state.results.ranking_sha256}};
  try{
    const blob=await fetchDownload(format,body,{signal:controller.signal});if(!acceptResponse(state,'download',token,{}))return;
    const url=URL.createObjectURL(blob),link=element('a');link.href=url;link.download=`zhixuan-recommendation.${format}`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);notify('已导出完整排名，非当前页；数值与证据按服务器重新核验。');
  }catch(error){if(error.name==='AbortError'||!acceptResponse(state,'download',token,{error}))return;if(['decision_changed','release_changed','release_unavailable','algorithm_changed','reference_rule_changed'].includes(error.code)){setPreferences(state,fields());clearResults();await refreshMeta();}notify(error.message,true);}
  finally{if(controllers.get('download')===controller)controllers.delete('download');}
}
for(const node of document.querySelectorAll('[data-view]'))node.addEventListener('click',()=>setView(node.dataset.view));
$('preferences').addEventListener('input',event=>{
  if(event.target.id==='max-price'&&event.target.value.trim())$('budget-unlimited').checked=false;
  if(event.target.id==='budget-unlimited'&&event.target.checked)$('max-price').value='';
  event.target.id==='confirmed'&&event.target.checked?preferencesSummary():changed();
});
$('preferences').addEventListener('submit',event=>{event.preventDefault();if(!$('confirmed').checked||!state.release||$('generate').disabled)return;state.confirmedRequest={...structuredClone(fields()),confirmed:true,expected_release:structuredClone(state.release)};for(const item of $('needs-result').querySelectorAll('[data-clarification]'))item.textContent=`已检查确认：${item.textContent.replace(/^待确认：/,'')}`;if($('needs-result').childElementCount)$('needs-status').textContent='表单已确认，推荐将按下方结果显示。';$('preferences-panel').open=true;$('more-preferences').open=false;$('advanced-settings').open=false;$('needs-notices').open=false;setView('recommend');generate();});
$('natural-needs').addEventListener('submit',understandNeeds);
$('needs-text').addEventListener('input',()=>{abortRequests();needsReview={report:null,acknowledged:new Set(),boxes:[],status:null};reviseNeedsText(state,fields());clearResults();$('needs-result').replaceChildren();$('needs-status').textContent='需求文字已改变，请重新理解并确认；也可检查表单后手动确认。';});
for(const node of document.querySelectorAll('[data-not-count]'))node.addEventListener('click',()=>{
  const reset=withoutCriterion(fields(),node.dataset.notCount);$(node.dataset.notCount).value=0;changed();
  notify('本次已暂不计入该项；原始需求和硬条件已保留，请重新检查并确认。');
});
$('manual-needs').addEventListener('click',()=>{$('preferences-panel').open=true;$('max-price').focus();});
$('edit-preferences').addEventListener('click',()=>{$('preferences-panel').open=true;$('max-price').focus();});
$('global-search').addEventListener('submit',event=>{event.preventDefault();$('search').value=$('global-query').value;setView('catalog');});
$('catalog-sort').addEventListener('change',()=>{featuredIndex=0;renderCatalogCards();});
$('catalog-edit-filters').addEventListener('click',()=>{setView('recommend');$('preferences-panel').open=true;$('advanced-settings').open=true;$('max-price').focus();});
$('clear-catalog-filters').addEventListener('click',()=>{for(const id of ['min-price','max-price','min-storage'])$(id).value='';for(const id of ['include-brands','exclude-brands'])for(const option of $(id).options)option.selected=false;$('search').value='';$('global-query').value='';catalogCategory='all';changed();loadCatalog(1);});
$('refresh-meta').addEventListener('click',()=>refreshMeta());$('catalog-search').addEventListener('submit',event=>{event.preventDefault();loadCatalog();});$('apply-catalog').addEventListener('click',()=>loadCatalog());
for(const node of document.querySelectorAll('[data-category]'))node.addEventListener('click',()=>{catalogCategory=node.dataset.category;loadCatalog(1);});
$('category-status').addEventListener('change',()=>loadCatalog(1));
$('view-buyer-reviews').addEventListener('click',()=>{
  if(!reviewMode){loadBuyerReviews();return;}
  controllers.get('detail')?.abort();controllers.delete('detail');state.sequence.detail++;state.loading.detail=false;reviewMode=false;
  $('detail-title').textContent='配置详情与来源';$('view-buyer-reviews').textContent='查看淘宝买家评价';$('view-buyer-reviews').setAttribute('aria-pressed','false');
  $('detail-content').setAttribute('aria-busy','false');$('detail-content').replaceChildren(renderDetail(detailItem));$('detail-dialog').scrollTop=0;$('detail-title').focus();
});
$('close-detail').addEventListener('click',()=>$('detail-dialog').close());
$('detail-dialog').addEventListener('close',()=>{controllers.get('detail')?.abort();state.sequence.detail++;detailItem=null;reviewMode=false;});
$('clear-comparison').addEventListener('click',()=>{for(const id of [...state.comparisonIds])removeComparison(state,id);controllers.get('compare')?.abort();compareCount();renderCurrentCards();loadComparison();});
for(const node of document.querySelectorAll('[data-download]'))node.addEventListener('click',()=>download(node.dataset.download));
$('decision-summary').replaceChildren(renderDecision(state));refreshMeta(true);
