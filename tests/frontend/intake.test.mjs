import test from 'node:test';
import assert from 'node:assert/strict';

const module = await import('../../frontend/admin.mjs').catch(() => ({}));
const release = {catalog_version:'20261028',manifest_sha256:'manifest-a',dataset_sha256:'dataset-a'};
const nextRelease = {...release,catalog_version:'20261028-2',manifest_sha256:'manifest-b'};
const payload = {model:{brand:'知选测试',display_name:'测试机型',release_month:'2026-10',source_url:'https://example.org/spec'},variant:{variant_name:'标准版',region:'中国大陆',satellite_support:false,is_default:true},skus:[{ram_gb:12,storage_gb:256,memory_type:null,price:{amount:1999,price_kind:'official_current_price',condition:'new',observed_at:'2026-10-28',source_url:'https://example.org/price',source_note:'官方页面'}}],specifications:[{key:'weight_g',value_num:192,unit:'g',raw_value:'机身重量 192g'}],source_note:'待来源复核'};
const record = (status='draft',revision=1) => ({record_id:'record-1',status,revision,created_at:'2026-10-28T00:00:00Z',updated_at:'2026-10-28T00:00:00Z',payload:structuredClone(payload),warnings:[]});
const key='offline-intake-test-maintenance-key-000';
test('reloading a record after a catalog conflict also refreshes the expected release',async()=>{
  let current=release;
  const {admin}=session({request:async path=>path==='/api/v1/meta'?{release:current}:path.includes('/queue')?{records:[record('submitted')],total:1,page:1,page_size:20}:{record:record('submitted')}});
  await admin.login(key);current=nextRelease;await admin.loadRecord('record-1');
  assert.deepEqual(admin.state.release,nextRelease);
});
function session(options={}){
  assert.equal(typeof module.IntakeAdminSession,'function','intake management session must be available');
  const calls=[];
  const request=async(path,options={})=>{calls.push({path,options});if(path==='/api/v1/meta')return {release};if(path.startsWith('/api/v1/intake/queue'))return {records:[record()],total:1,page:1,page_size:20};return {ok:true,record:record()};};
  return {admin:new module.IntakeAdminSession({request,...options}),calls};
}
test('management refuses remote HTTP before sending a maintenance key',()=>{
  assert.equal(typeof module.secureIntakeLocation,'function');
  assert.equal(module.secureIntakeLocation({protocol:'http:',hostname:'example.org'}),false);
  assert.equal(module.secureIntakeLocation({protocol:'https:',hostname:'example.org'}),true);
  assert.equal(module.secureIntakeLocation({protocol:'http:',hostname:'[::1]'}),true);
  assert.equal(module.secureIntakeLocation({protocol:'file:',hostname:'localhost'}),false);
});
test('login reads private queue with a memory-only bearer key and current release',async()=>{
  const {admin,calls}=session();await admin.login(key);
  assert.equal(admin.state.authenticated,true);assert.deepEqual(admin.state.release,release);
  assert.match(calls.find(c=>c.path.startsWith('/api/v1/intake/queue')).options.headers.Authorization,/^Bearer offline-intake/);
  assert.equal(JSON.stringify(admin.state).includes(key),false);
  admin.logout();assert.equal(admin.state.authenticated,false);assert.equal(admin.state.records.length,0);assert.equal(admin.state.record,null);
  await assert.rejects(admin.loadQueue(),/进入管理/);
});
test('logout ignores late private queue responses',async()=>{
  let finish;const {admin}=session({request:async path=>path==='/api/v1/meta'?{release}:new Promise(resolve=>finish=resolve)});
  const login=admin.login(key);await Promise.resolve();admin.logout();finish({records:[record()],total:1,page:1,page_size:20});await login;
  assert.equal(admin.state.authenticated,false);assert.equal(admin.state.records.length,0);
});
test('new draft is created directly, then edited using id, payload, status and expected revision',async()=>{
  const calls=[];const {admin}=session({request:async(path,options={})=>{calls.push({path,options});if(path==='/api/v1/meta')return {release};if(path.includes('/queue'))return {records:[record()],total:1,page:1,page_size:20};return {ok:true,record:record(options.method==='PUT'?'submitted':'draft',options.method==='PUT'?2:1),warnings:['请核对来源']};}});
  await admin.login(key);await admin.save(payload,'draft');
  const create=calls.find(c=>c.path.endsWith('/draft'));assert.deepEqual(create.options.body,payload);
  await admin.save({...payload,source_note:'编辑过的内容'},'submitted');
  const update=calls.find(c=>c.options.method==='PUT');assert.equal(update.path,'/api/v1/intake/record-1');assert.equal(update.options.body.expected_revision,1);assert.equal(update.options.body.status,'submitted');assert.equal(update.options.body.payload.source_note,'编辑过的内容');assert.equal(admin.state.record.revision,2);
});
test('publishing requires explicit source verification and sends release plus revision',async()=>{
  const calls=[],notices=[];let published=false;
  const {admin}=session({notifyCatalog:value=>notices.push(value),request:async(path,options={})=>{calls.push({path,options});if(path==='/api/v1/meta')return {release:published?nextRelease:release};if(path.includes('/queue'))return {records:[record('submitted',3)],total:1,page:1,page_size:20};if(path.endsWith('/publish')){published=true;return {ok:true,record:record('published',4),release:nextRelease};}return {record:record('submitted',3)};}});
  await admin.login(key);await admin.loadRecord('record-1');await assert.rejects(admin.publish(false),/来源/);assert.equal(calls.some(c=>c.path.endsWith('/publish')),false);
  await admin.publish(true);const publish=calls.find(c=>c.path.endsWith('/publish'));
  assert.deepEqual(publish.options.body,{expected_revision:3,expected_release:release,sources_verified:true});assert.deepEqual(admin.state.release,nextRelease);assert.equal(admin.state.record.status,'published');assert.deepEqual(notices,[nextRelease]);
  await assert.rejects(admin.save(payload,'draft'),/已发布/);
});
test('reject uses selected revision and queue loading preserves pagination',async()=>{
  const calls=[];const {admin}=session({request:async(path,options={})=>{calls.push({path,options});if(path==='/api/v1/meta')return {release};if(path.includes('/queue'))return {records:[record()],total:41,page:path.includes('page=2')?2:1,page_size:20};return {record:record(path.endsWith('/reject')?'rejected':'submitted',6)};}});
  await admin.login(key);await admin.loadQueue(2);assert.equal(admin.state.pagination.page,2);await admin.loadRecord('record-1');await admin.reject();
  assert.deepEqual(calls.find(c=>c.path.endsWith('/reject')).options.body,{expected_revision:6});assert.equal(admin.state.record.status,'rejected');
});
test('conflict preserves selected record and requires reloading before retrying',async()=>{
  let conflict=false;const {admin}=session({request:async path=>{if(path==='/api/v1/meta')return {release};if(path.includes('/queue'))return {records:[record()],total:1,page:1,page_size:20};if(conflict)throw Object.assign(new Error('记录已更新'),{status:409});return {record:record()};}});
  await admin.login(key);await admin.loadRecord('record-1');conflict=true;await assert.rejects(admin.save(payload,'submitted'),e=>e.status===409);
  assert.equal(admin.state.record.revision,1);assert.equal(admin.state.needsReload,true);await assert.rejects(admin.save(payload,'draft'),/重新加载/);
});
test('expired key clears private state and cannot authorize another action',async()=>{
  let expired=false;const {admin}=session({request:async path=>{if(expired)throw Object.assign(new Error('Unauthorized'),{status:401});if(path==='/api/v1/meta')return {release};return {records:[record()],total:1,page:1,page_size:20};}});
  await admin.login(key);expired=true;await assert.rejects(admin.loadQueue(),e=>e.status===401);assert.equal(admin.state.authenticated,false);assert.equal(admin.state.records.length,0);await assert.rejects(admin.exportRecords(),/进入管理/);
});
test('only one save request runs while a response is pending',async()=>{
  let finish,calls=0;const {admin}=session({request:async(path)=>{if(path==='/api/v1/meta')return {release};if(path.includes('/queue'))return {records:[],total:0,page:1,page_size:20};calls++;return new Promise(resolve=>finish=resolve);}});
  await admin.login(key);const first=admin.save(payload,'draft');await assert.rejects(admin.save(payload,'submitted'),/正在/);assert.equal(calls,1);finish({ok:true,record:record(),warnings:[]});await first;
});
test('export returns complete records instead of current page',async()=>{
  const {admin}=session({request:async path=>path==='/api/v1/meta'?{release}:path.endsWith('/export')?{records:[record(),record('rejected',2)]}:{records:[record()],total:2,page:1,page_size:1}});
  await admin.login(key);const output=await admin.exportRecords();assert.equal(output.records.length,2);assert.equal(output.records[1].payload.skus[0].price.source_note,'官方页面');
});
test('payload keeps independent price and specification sources and unknown numbers as null',()=>{
  assert.equal(typeof module.buildIntakePayload,'function');
  const result=module.buildIntakePayload({brand:' 测试 ',display_name:' 测试手机 ',release_month:'',source_url:' https://example.org/spec ',variant_name:'标准版',is_default:true},[{ram_gb:'',storage_gb:'256',memory_type:'',price:{amount:'',price_kind:'',condition:'',observed_at:'',source_url:'https://example.org/price',source_note:' 价格待确认 '}}],[{key:'weight_g',value_num:'',unit:'g',raw_value:' 未公布 '}]);
  assert.equal(result.model.source_url,'https://example.org/spec');assert.equal(result.skus[0].price.source_url,'https://example.org/price');assert.equal(result.skus[0].price.source_note,'价格待确认');assert.equal(result.skus[0].ram_gb,null);assert.equal(result.skus[0].price.amount,null);assert.equal(result.specifications[0].value_num,null);assert.equal(result.specifications[0].raw_value,'未公布');
  assert.throws(()=>module.buildIntakePayload({},[{ram_gb:'not-number',price:{}}],[]),/数值/);
});
