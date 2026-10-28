import {fetchJson} from './api.mjs';

export const INTAKE_STATUS={draft:'草稿',submitted:'待审核',rejected:'未通过',published:'已发布'};
const text=value=>String(value??'').trim();
const optional=value=>text(value)||null;
function number(value){
  if(value===null||value===undefined||text(value)==='')return null;
  const parsed=Number(value);if(!Number.isFinite(parsed))throw new Error('数值格式无效，请修改后重试；未知数值请留空。');return parsed;
}
export function secureIntakeLocation(location){
  return location?.protocol==='https:'||(location?.protocol==='http:'&&['localhost','127.0.0.1','[::1]'].includes(location.hostname));
}
export function buildIntakePayload(fields={},skus=[],specifications=[]){
  return {
    model:{brand:text(fields.brand),display_name:text(fields.display_name),release_month:optional(fields.release_month),source_url:optional(fields.source_url)},
    variant:{variant_name:text(fields.variant_name),region:optional(fields.region),satellite_support:Boolean(fields.satellite_support),is_default:Boolean(fields.is_default)},
    skus:skus.map(item=>({ram_gb:number(item.ram_gb),storage_gb:number(item.storage_gb),memory_type:optional(item.memory_type),price:{amount:number(item.price?.amount),price_kind:optional(item.price?.price_kind),condition:optional(item.price?.condition),observed_at:optional(item.price?.observed_at),source_url:optional(item.price?.source_url),source_note:optional(item.price?.source_note)}})),
    specifications:specifications.filter(item=>Object.values(item).some(value=>text(value)!=='')).map(item=>({key:text(item.key),value_num:number(item.value_num),unit:optional(item.unit),raw_value:optional(item.raw_value)})),
    source_note:text(fields.source_note)
  };
}
export function notifyCatalogPublished(release){
  if(typeof window==='undefined'||!window.BroadcastChannel)return;
  try{const channel=new window.BroadcastChannel('zhixuan-catalog');channel.postMessage({type:'catalog-published',release});channel.close();}catch{}
}
class StaleSessionError extends Error{}
function emptyState(){return {authenticated:false,busy:false,records:[],pagination:{total:0,page:1,page_size:20},record:null,release:null,warnings:[],needsReload:false,message:''};}

/** Authentication lives only in this instance, never in storage, the URL or DOM. */
export class IntakeAdminSession{
  #token='';#epoch=0;
  constructor({request=fetchJson,onChange=()=>{},notifyCatalog=notifyCatalogPublished}={}){this.request=request;this.onChange=onChange;this.notifyCatalog=notifyCatalog;this.state=emptyState();}
  #emit(){this.onChange(this.state);}
  logout(message='已退出管理，维护密钥和队列已清空。'){this.#token='';this.#epoch++;this.state={...emptyState(),message};this.#emit();}
  report(error){if(error instanceof StaleSessionError)return;this.state.message=error.status===401?'维护密钥无效或已更换，请重新输入。':error.status===409?'记录或目录版本已更新。请先重新加载记录；已填写内容仍保留。':error.name==='TimeoutError'?'连接超时，请重试；已填写内容仍保留。':error.message||'暂时无法完成，请稍后重试。';this.#emit();}
  async #request(path,options={},epoch=this.#epoch){
    try{
      const result=await this.request(path,{...options,signal:AbortSignal.timeout(30000),headers:{Authorization:`Bearer ${this.#token}`}});
      if(epoch!==this.#epoch)throw new StaleSessionError();return result;
    }catch(error){
      if(epoch!==this.#epoch)throw new StaleSessionError();
      if(error.status===401)this.logout('维护密钥无效或已更换，请重新输入。');
      throw error;
    }
  }
  async #run(action,{allowLogin=false}={}){
    if(!this.#token||(!allowLogin&&!this.state.authenticated))throw new Error('请先输入维护密钥进入管理。');
    if(this.state.busy)throw new Error('正在处理上一项操作，请稍候。');
    const epoch=this.#epoch;this.state.busy=true;this.#emit();
    try{return await action();}
    catch(error){if(error instanceof StaleSessionError)return;if(epoch===this.#epoch&&error.status===409)this.state.needsReload=true;throw error;}
    finally{if(epoch===this.#epoch){this.state.busy=false;this.#emit();}}
  }
  async #readMeta(){const meta=await this.#request('/api/v1/meta');if(!meta?.release||typeof meta.release!=='object')throw new Error('未能读取目录版本，请刷新后重试。');this.state.release=structuredClone(meta.release);}
  async #readQueue(page=1){
    const result=await this.#request(`/api/v1/intake/queue?page=${Math.max(1,Number(page)||1)}&page_size=20`);
    if(!Array.isArray(result?.records))throw new Error('队列返回无效内容，请刷新后重试。');
    const pages=Math.max(1,Math.ceil(result.total/result.page_size));if(result.page>pages)return this.#readQueue(pages);
    this.state.records=result.records;this.state.pagination={total:result.total,page:result.page,page_size:result.page_size};
  }
  async login(key){
    if(this.state.busy)throw new Error('正在验证维护密钥，请稍候。');
    this.#token=text(key);this.#epoch++;this.state=emptyState();
    if(this.#token.length<32){this.logout('请输入完整的维护密钥，与评论审核使用同一密钥。');return;}
    return this.#run(async()=>{await Promise.all([this.#readQueue(),this.#readMeta()]);this.state.authenticated=true;this.state.message='已进入管理。保存草稿后，可继续编辑同一条记录。';},{allowLogin:true});
  }
  async loadQueue(page=this.state.pagination.page){return this.#run(async()=>{await Promise.all([this.#readQueue(page),this.#readMeta()]);this.state.message='队列与当前目录版本已刷新。';});}
  async loadRecord(id){return this.#run(async()=>{const result=await this.#request(`/api/v1/intake/${encodeURIComponent(id)}`);if(!result?.record?.payload)throw new Error('未能读取记录，请刷新队列重试。');await this.#readMeta();this.state.record=result.record;this.state.warnings=result.record.warnings??[];this.state.needsReload=false;this.state.message=`已加载${INTAKE_STATUS[result.record.status]??'记录'}，编号 ${result.record.record_id}。`;return result.record;});}
  newRecord(){if(this.state.busy)return;this.state.record=null;this.state.warnings=[];this.state.needsReload=false;this.state.message='正在新增机型；尚未保存到队列。';this.#emit();}
  #editable(){if(this.state.needsReload)throw new Error('请先重新加载记录，核对最新内容后再保存。');if(this.state.record?.status==='published')throw new Error('已发布的记录不可编辑，请新建录入。');}
  async save(payload,status='draft'){
    if(!['draft','submitted'].includes(status))throw new Error('保存状态无效。');
    return this.#run(async()=>{
      this.#editable();const selected=this.state.record;
      const result=await this.#request(selected?`/api/v1/intake/${encodeURIComponent(selected.record_id)}`:`/api/v1/intake/${status==='submitted'?'submit':'draft'}`,{method:selected?'PUT':'POST',body:selected?{payload,status,expected_revision:selected.revision}:payload});
      this.state.record=result.record;this.state.warnings=result.warnings??result.record?.warnings??[];this.state.needsReload=false;
      await this.#readQueue(this.state.pagination.page);this.state.message=status==='submitted'?'已提交待审核。请核对保存后的来源，再发布到正式目录。':'草稿已保存，可继续编辑同一条记录。';return result.record;
    });
  }
  async reject(){return this.#run(async()=>{
    this.#editable();const selected=this.state.record;if(!selected)throw new Error('请先从队列加载要拒绝的记录。');
    const result=await this.#request(`/api/v1/intake/${encodeURIComponent(selected.record_id)}/reject`,{method:'POST',body:{expected_revision:selected.revision}});
    this.state.record=result.record;this.state.warnings=result.record?.warnings??[];await this.#readQueue(this.state.pagination.page);this.state.message='已标记未通过；此记录不会进入正式目录。';return result.record;
  });}
  async publish(sourcesVerified){return this.#run(async()=>{
    this.#editable();const selected=this.state.record;
    if(!selected||selected.status!=='submitted')throw new Error('请先提交待审核并加载已保存的记录。');
    if(sourcesVerified!==true)throw new Error('请先复核规格与每个配置的价格来源，并勾选来源确认。');
    if(!this.state.release)throw new Error('未能读取目录版本，请刷新队列后重试。');
    const result=await this.#request(`/api/v1/intake/${encodeURIComponent(selected.record_id)}/publish`,{method:'POST',body:{expected_revision:selected.revision,expected_release:structuredClone(this.state.release),sources_verified:true}});
    this.state.record=result.record;this.state.warnings=result.record?.warnings??[];this.state.release=result.release??this.state.release;
    this.notifyCatalog(this.state.release);
    try{await Promise.all([this.#readMeta(),this.#readQueue(this.state.pagination.page)]);this.state.message='发布成功。正式目录已切换，打开的网站会自动刷新数据。';}
    catch(error){if(error instanceof StaleSessionError)throw error;this.state.message='发布成功。刷新队列未完成，请点击刷新确认当前目录。';}
    return result.record;
  });}
  async exportRecords(){return this.#run(async()=>{const result=await this.#request('/api/v1/intake/export');if(!Array.isArray(result?.records))throw new Error('导出返回无效内容，请重试。');this.state.message=`已导出全部 ${result.records.length} 条记录，请妥善保存。`;return result;});}
}

function node(root,tag,label='',className=''){const element=root.createElement(tag);element.textContent=label;element.className=className;return element;}
function date(value){const parsed=new Date(value);return Number.isNaN(parsed.getTime())?'时间未记录':new Intl.DateTimeFormat('zh-CN',{dateStyle:'medium',timeStyle:'short'}).format(parsed);}
function downloadJSON(root,data){const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json;charset=utf-8'}),url=URL.createObjectURL(blob),link=root.createElement('a');link.href=url;link.download='zhixuan-intake-queue.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}

export function mountIntakeAdmin(root,{request=fetchJson,notifyCatalog=notifyCatalogPublished,download=downloadJSON}={}){
  const $=id=>root.getElementById(id);if(!$('intake-login'))return;
  if(root.location&&!secureIntakeLocation(root.location)){
    $('intake-key').disabled=true;$('intake-login-button').disabled=true;
    $('intake-message').textContent='请通过 HTTPS 地址进入机型管理；本机预览可使用 localhost。';return;
  }
  let skuRows=[],specRows=[],ordinal=0,loadedRecord=null,dirty=false,wasAuthenticated=false;
  const scalarFields={brand:'brand','display-name':'display_name','release-month':'release_month','model-source':'source_url','variant-name':'variant_name',region:'region','source-note':'source_note'};
  const admin=new IntakeAdminSession({request,notifyCatalog,onChange:render});
  const run=async action=>{try{return await action();}catch(error){admin.report(error);}};
  function invalidate(){dirty=true;$('sources-verified').checked=false;renderControls(admin.state);}
  function inputField(container,label,name,type,value='',choices){
    const control=node(root,choices?'select':'input');control.id=`intake-field-${++ordinal}`;
    control.name=name;if(!choices)control.type=type;control.value=value??'';
    if(type==='number'){control.step='any';control.inputMode='decimal';}
    if(choices){for(const [value,label] of choices){const option=node(root,'option',label);option.value=value;control.append(option);}control.value=value??'';}
    control.addEventListener('input',invalidate);control.addEventListener('change',invalidate);
    const wrapper=node(root,'label',label);wrapper.htmlFor=control.id;wrapper.append(control);container.append(wrapper);return control;
  }
  function addRow(kind,values={}){
    const isSku=kind==='sku',rows=isSku?skuRows:specRows,box=$(isSku?'sku-rows':'spec-rows');
    const wrapper=node(root,'fieldset','','intake-entry'),heading=node(root,'legend','','intake-entry-heading');
    const remove=node(root,'button','删除此项','intake-remove');remove.type='button';heading.append(node(root,'span',isSku?'配置与价格':'技术规格'),remove);wrapper.append(heading);const grid=node(root,'div','','intake-grid intake-entry-grid');wrapper.append(grid);
    const controls={};
    if(isSku){
      controls.ram_gb=inputField(grid,'运行内存（GB）','ram_gb','number',values.ram_gb);
      controls.storage_gb=inputField(grid,'存储容量（GB）','storage_gb','number',values.storage_gb);
      controls.memory_type=inputField(grid,'内存类型（可留空）','memory_type','text',values.memory_type);
      controls.amount=inputField(grid,'价格（元）','amount','number',values.price?.amount);
      controls.price_kind=inputField(grid,'价格口径','price_kind','',values.price?.price_kind,[['','请选择'],['official_current_price','官方当前价'],['marketplace_listing_price','电商记录价'],['used_market_estimate','二手估价']]);
      controls.condition=inputField(grid,'成色','condition','',values.price?.condition,[['','请选择'],['new','新机'],['used','二手'],['unknown','成色未知']]);
      controls.observed_at=inputField(grid,'价格记录日期','observed_at','date',values.price?.observed_at);
      controls.source_url=inputField(grid,'价格来源链接','source_url','url',values.price?.source_url);
      controls.source_note=inputField(grid,'价格来源说明（可留空）','price_source_note','text',values.price?.source_note);
      controls.source_url.parentElement?.classList.add('intake-wide');controls.source_note.parentElement?.classList.add('intake-wide');
    }else{
      controls.key=inputField(grid,'规格指标键','key','text',values.key);controls.value_num=inputField(grid,'数值（未知请留空）','value_num','number',values.value_num);
      controls.unit=inputField(grid,'单位','unit','text',values.unit);controls.raw_value=inputField(grid,'来源原文','raw_value','text',values.raw_value);
    }
    const row={wrapper,controls};rows.push(row);box.append(wrapper);
    remove.addEventListener('click',()=>{if(admin.state.busy)return;wrapper.remove();rows.splice(rows.indexOf(row),1);invalidate();});
    return row;
  }
  function clearForm(payload){
    const model=payload?.model??{},variant=payload?.variant??{};
    for(const [id,key] of Object.entries(scalarFields))$(id).value=(id==='source-note'?payload?.source_note:key in model?model[key]:variant[key])??(id==='variant-name'?'标准版':'');
    $('satellite').checked=Boolean(variant.satellite_support);$('is-default').checked=variant.is_default??true;
    $('sku-rows').replaceChildren();$('spec-rows').replaceChildren();skuRows=[];specRows=[];
    for(const sku of payload?.skus?.length?payload.skus:[{}])addRow('sku',sku);
    for(const spec of payload?.specifications??[])addRow('spec',spec);
    $('sources-verified').checked=false;dirty=false;
  }
  function readPayload(){
    for(const row of [...skuRows,...specRows])for(const control of Object.values(row.controls))if(control.validity?.badInput)throw new Error('数值格式无效，请修改后重试；未知数值请留空。');
    const fields=Object.fromEntries(Object.entries(scalarFields).map(([id,key])=>[key,$(id).value]));fields.satellite_support=$('satellite').checked;fields.is_default=$('is-default').checked;
    const skus=skuRows.map(({controls:c})=>({ram_gb:c.ram_gb.value,storage_gb:c.storage_gb.value,memory_type:c.memory_type.value,price:Object.fromEntries(['amount','price_kind','condition','observed_at','source_url','source_note'].map(key=>[key,c[key].value]))}));
    const specifications=specRows.map(({controls})=>Object.fromEntries(Object.entries(controls).map(([key,control])=>[key,control.value])));
    return buildIntakePayload(fields,skus,specifications);
  }
  function renderControls(state){
    const published=state.record?.status==='published',blocked=state.busy||published||state.needsReload;
    $('intake-fields').disabled=state.busy||published;
    $('save-draft').disabled=blocked;$('submit-record').disabled=blocked;$('reject-record').disabled=blocked||!state.record||state.record.status==='rejected';
    $('sources-verified').disabled=blocked||dirty||state.record?.status!=='submitted';
    $('publish-record').disabled=blocked||dirty||state.record?.status!=='submitted'||!$('sources-verified').checked;
    $('reload-record').disabled=state.busy||!state.record;$('new-record').disabled=state.busy;
    $('refresh-queue').disabled=state.busy;$('download-queue').disabled=state.busy;$('intake-login-button').disabled=state.busy;
    $('review-guidance').textContent=published?'此记录已发布，内容只读。新资料请创建新记录。':dirty?'表单有尚未保存的修改。请先保存或提交，再复核保存后的资料。':state.record?.status==='submitted'?'请打开下方保存的来源链接，逐项核对后勾选来源确认。':'保存并提交待审核后，可复核来源并发布到正式目录。';
  }
  function renderQueue(state){
    $('queue').replaceChildren();
    if(!state.records.length)$('queue').append(node(root,'p','队列暂无记录。保存第一份草稿后会显示在这里。','intake-muted'));
    for(const record of state.records){
      const article=node(root,'article','','intake-queue-item');
      const heading=node(root,'div','','intake-queue-heading');heading.append(node(root,'strong',record.payload?.model?.display_name||'未填写机型'),node(root,'span',INTAKE_STATUS[record.status]??record.status,`intake-badge intake-status-${record.status}`));
      const load=node(root,'button',record.record_id===state.record?.record_id?'重新加载':'查看 / 编辑');load.type='button';load.disabled=state.busy;
      load.addEventListener('click',()=>loadSelected(record.record_id));
      article.append(heading,node(root,'p',`${record.payload?.model?.brand||'未填品牌'} · ${record.payload?.skus?.length??0} 个配置 · 修订 ${record.revision}`,'intake-muted'),node(root,'p',date(record.updated_at),'intake-muted'),load);$('queue').append(article);
    }
    const {total,page,page_size}=state.pagination,pages=Math.max(1,Math.ceil(total/page_size));$('queue-summary').textContent=`全部 ${total} 条 · 第 ${page} / ${pages} 页`;
    $('queue-pagination').replaceChildren();if(pages>1){for(const [label,next,disabled] of [['上一页',page-1,page<=1],['下一页',page+1,page>=pages]]){const button=node(root,'button',label);button.type='button';button.disabled=disabled||state.busy;button.addEventListener('click',()=>run(()=>admin.loadQueue(next)));$('queue-pagination').append(button);}}
  }
  function sourceLink(label,url){
    const item=node(root,'li');let parsed;try{parsed=new URL(url);}catch{}
    if(parsed&&['http:','https:'].includes(parsed.protocol)){const link=node(root,'a',label);link.href=parsed.href;link.target='_blank';link.rel='noopener noreferrer';item.append(link);}else item.textContent=`${label}：未提供有效链接`;
    return item;
  }
  function render(state){
    $('intake-workspace').hidden=!state.authenticated;$('intake-login-panel').hidden=state.authenticated;$('intake-message').textContent=state.message;$('intake-key').value='';
    if(!state.authenticated&&wasAuthenticated){clearForm();loadedRecord=null;}
    wasAuthenticated=state.authenticated;
    const identity=state.record?`${state.record.record_id}:${state.record.revision}`:null;
    if(identity!==loadedRecord){clearForm(state.record?.payload);loadedRecord=identity;}
    $('record-summary').textContent=state.record?`${INTAKE_STATUS[state.record.status]??state.record.status} · 修订 ${state.record.revision} · ${state.record.record_id}`:'新增记录 · 尚未保存';
    $('catalog-release').textContent=state.release?`当前目录：${state.release.dataset_version??state.release.catalog_version??'已读取'} · 发布时校验完整版本`:'当前目录版本尚未读取';
    $('record-json').textContent=state.record?JSON.stringify(state.record.payload,null,2):'保存后可在这里查看完整记录。';
    $('record-warnings').replaceChildren();for(const warning of state.warnings)$('record-warnings').append(node(root,'li',typeof warning==='string'?warning:JSON.stringify(warning)));
    $('record-warnings').hidden=!state.warnings.length;
    $('record-sources').replaceChildren();if(state.record){const payload=state.record.payload;$('record-sources').append(sourceLink('机型与规格来源',payload.model?.source_url));payload.skus?.forEach((sku,index)=>$('record-sources').append(sourceLink(`配置 ${index+1} 价格来源（${sku.ram_gb??'?'} + ${sku.storage_gb??'?'} GB）`,sku.price?.source_url)));}
    renderQueue(state);renderControls(state);
  }
  async function loadSelected(id){
    if(dirty&&root.defaultView?.confirm&&!root.defaultView.confirm('表单有未保存的修改，重新加载会替换这些内容。继续加载？'))return;
    const record=await run(()=>admin.loadRecord(id));
    if(record&&admin.state.authenticated){clearForm(record.payload);loadedRecord=`${record.record_id}:${record.revision}`;renderControls(admin.state);}
  }
  $('intake-login').addEventListener('submit',async event=>{event.preventDefault();const key=$('intake-key').value;$('intake-key').value='';await run(()=>admin.login(key));});
  $('intake-logout').addEventListener('click',()=>admin.logout());
  $('intake-form').addEventListener('submit',event=>event.preventDefault());
  for(const id of [...Object.keys(scalarFields),'satellite','is-default'])$(id).addEventListener('input',invalidate);
  $('add-sku').addEventListener('click',()=>{addRow('sku');invalidate();});$('add-spec').addEventListener('click',()=>{addRow('spec');invalidate();});
  $('save-draft').addEventListener('click',()=>run(()=>admin.save(readPayload(),'draft')));
  $('submit-record').addEventListener('click',()=>run(()=>admin.save(readPayload(),'submitted')));
  $('new-record').addEventListener('click',()=>{if(dirty&&root.defaultView?.confirm&&!root.defaultView.confirm('新建会清空未保存的修改。继续新建？'))return;admin.newRecord();clearForm();renderControls(admin.state);});
  $('reload-record').addEventListener('click',()=>admin.state.record&&loadSelected(admin.state.record.record_id));
  $('sources-verified').addEventListener('change',()=>renderControls(admin.state));
  $('publish-record').addEventListener('click',()=>run(()=>admin.publish($('sources-verified').checked)));
  $('reject-record').addEventListener('click',()=>run(()=>admin.reject()));
  $('refresh-queue').addEventListener('click',()=>run(()=>admin.loadQueue()));
  $('download-queue').addEventListener('click',()=>run(async()=>{const records=await admin.exportRecords();if(records&&admin.state.authenticated)download(root,records);}));
  clearForm();render(admin.state);return admin;
}
if(typeof document!=='undefined')mountIntakeAdmin(document);
