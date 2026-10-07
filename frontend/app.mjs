import {createState,setPreferences,applyMeta,beginRequest,acceptResponse,addComparison,removeComparison} from './state.mjs';
import {fetchJson,fetchDownload} from './api.mjs';
import {element,button,labels,renderCard,renderDecision,renderComparison,renderDetail} from './render.mjs';

const $=id=>document.getElementById(id),state=createState(),controllers=new Map();
let view='recommend',metaSequence=0;
const metrics=Object.keys(labels);
function notify(message,error=false){const node=$('message');node.textContent=message;node.hidden=!message;node.setAttribute('role',error?'alert':'status');if(error)node.focus();}
function abortRequests(){for(const controller of controllers.values())controller.abort();controllers.clear();}
function numeric(id){return $(id).value.trim()===''?null:Number($(id).value);}
function selected(id){return [...$(id).selectedOptions].map(option=>option.value);}
function fields(){return {needs:{budget:{min:numeric('min-price'),max:numeric('max-price')},min_storage:numeric('min-storage'),brands:{include:selected('include-brands'),exclude:selected('exclude-brands')}},
  weights:Object.fromEntries(metrics.map(key=>[key,Number($(key).value)])),target_screen:numeric('target-screen'),mode:$('mode').value};}
function preferencesSummary(){
  const f=fields(),budget=f.needs.budget;
  $('preferences-summary').textContent=`${{new:'新机',used:'二手',all:'全部成色（含未知）'}[f.mode]} · ${budget.min??'不限'}—${budget.max??'不限'} 元 · ${f.needs.min_storage?`${f.needs.min_storage}GB 起`:'存储不限'} · ${f.needs.brands.include.join('、')||'全部品牌'}`;
  for(const key of metrics)document.querySelector(`output[for="${key}"]`).textContent=$(key).value;
  $('generate').disabled=!state.release||!$('confirmed').checked||state.loading.recommend;
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
    if(initial){for(const key of meta.metrics)$(key).value=meta.defaults.weights[key]??0;$('mode').value=meta.defaults.mode;$('target-screen').value=meta.defaults.target_screen;}
    fillBrands('include-brands',meta.brands);fillBrands('exclude-brands',meta.brands);state.preferences=structuredClone(fields());preferencesSummary();
    const acceptance=meta.acceptance;
    $('release-status').textContent=`${meta.counts.phone_models} 机型 · ${meta.counts.skus} 配置 · 机器 ${acceptance.machine_acceptance||'未知'} / 总体 ${acceptance.overall_acceptance||'未知'} · WPS ${acceptance.wps_review||'未知'}`;
    $('release-info').replaceChildren(element('p',`数据 ${meta.release.dataset_version} / 规则 ${meta.release.feature_rule_version}`),element('p',`人工抽查仍待完成；当前发布未提供人工全通过证明。已关联 ${meta.features.image_models??0} 个机型展示图；DeepSeek ${meta.features.llm?'已配置（仅解释）':'未启用'}；VIKOR 未实现。`),element('p','照片子分需协议可比；游戏仅限同条件王者荣耀实测帧率。1% Low 缺失，不推算完整游戏总分。'),element('p',`数据库指纹 ${meta.release.snapshot_sha256}`,'small'));
    if(!initial)notify(changedRelease?'数据版本已变化，请重新确认并生成推荐。':'数据状态已刷新，版本未变化。');
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
    if(['release_changed','decision_changed','release_unavailable'].includes(error.code)){setPreferences(state,fields());clearResults();await refreshMeta();}
    notify(error.message,true);
  }finally{
    if(controllers.get(channel)===controller){controllers.delete(channel);for(const id of ['results','catalog-results','detail-content','compare-content'])$(id).setAttribute('aria-busy','false');preferencesSummary();}
  }
}
function addToCompare(id){
  if(!addComparison(state,id)){notify(state.comparisonIds.includes(id)?'该配置已在对比中。':'最多对比四个配置，请先移除一个。');return;}
  controllers.get('compare')?.abort();compareCount();notify(`已加入对比（${state.comparisonIds.length}/4）。`);renderCurrentCards();
}
function cardContext(id,explanations={}){return {explanation:explanations[id],selected:state.comparisonIds.includes(id),onDetails:loadDetail,onCompare:addToCompare,onExplain:explain,llmEnabled:state.meta?.features.llm};}
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
  if(state.results?.ranked)$('results').replaceChildren(...state.results.ranked.map(row=>renderCard(row,cardContext(row.sku_id,state.results.explanations))));
  if(state.responses.catalog)$('catalog-results').replaceChildren(...state.responses.catalog.items.map(row=>renderCard(row,cardContext(row.sku_id))));
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
function loadCatalog(page=1){
  if(!state.release)return;const f=fields(),q=new URLSearchParams({mode:f.mode,page:String(page),page_size:'12',q:$('search').value});
  for(const [key,value] of [['min_price',f.needs.budget.min],['max_price',f.needs.budget.max],['min_storage',f.needs.min_storage]])if(value!==null)q.set(key,String(value));
  for(const key of ['include','exclude'])for(const brand of f.needs.brands[key])q.append(key,brand);
  $('catalog-status').textContent='正在读取目录…';
  request('catalog',`/api/v1/catalog?${q}`,{},payload=>{
    $('catalog-status').textContent=`共 ${payload.total} 个准确配置 · 价格为记录快照`;
    renderCurrentCards();if(!payload.total)$('catalog-results').append(element('p','当前条件下没有配置，请调整筛选或搜索。','empty'));
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
  }catch(error){if(error.name==='AbortError'||!acceptResponse(state,'download',token,{error}))return;if(['decision_changed','release_changed','release_unavailable'].includes(error.code)){setPreferences(state,fields());clearResults();await refreshMeta();}notify(error.message,true);}
  finally{if(controllers.get('download')===controller)controllers.delete('download');}
}
for(const node of document.querySelectorAll('[data-view]'))node.addEventListener('click',()=>setView(node.dataset.view));
$('preferences').addEventListener('input',event=>event.target.id==='confirmed'&&event.target.checked?preferencesSummary():changed());
$('preferences').addEventListener('submit',event=>{event.preventDefault();if(!$('confirmed').checked||!state.release)return;state.confirmedRequest={...structuredClone(fields()),confirmed:true,expected_release:structuredClone(state.release)};$('preferences-panel').open=false;setView('recommend');generate();});
$('edit-preferences').addEventListener('click',()=>{$('preferences-panel').open=true;$('mode').focus();});
$('refresh-meta').addEventListener('click',()=>refreshMeta());$('catalog-search').addEventListener('submit',event=>{event.preventDefault();loadCatalog();});$('apply-catalog').addEventListener('click',()=>loadCatalog());
$('close-detail').addEventListener('click',()=>{$('detail-dialog').close();controllers.get('detail')?.abort();state.sequence.detail++;});
$('clear-comparison').addEventListener('click',()=>{for(const id of [...state.comparisonIds])removeComparison(state,id);controllers.get('compare')?.abort();compareCount();renderCurrentCards();loadComparison();});
for(const node of document.querySelectorAll('[data-download]'))node.addEventListener('click',()=>download(node.dataset.download));
$('decision-summary').replaceChildren(renderDecision(state));refreshMeta(true);
