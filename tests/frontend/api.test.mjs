import test from 'node:test';import assert from 'node:assert/strict';
import {fetchJson,fetchDownload,ApiClientError} from '../../frontend/api.mjs';
async function mockFetch(fn,run){const original=globalThis.fetch;globalThis.fetch=fn;try{await run();}finally{globalThis.fetch=original;}}
test('transport serializes confirmation and passes cancellation',async()=>{
  const controller=new AbortController();let seen;
  await mockFetch(async(path,init)=>{seen={path,init};return new Response('{"status":"ready"}',{headers:{'Content-Type':'application/json'}});},async()=>{
    const result=await fetchJson('/api/v1/recommend?page=2',{method:'POST',body:{confirmed:true},signal:controller.signal});
    assert.equal(result.status,'ready');assert.equal(seen.init.signal,controller.signal);
    assert.equal(seen.init.body,'{"confirmed":true}');assert.equal(seen.path,'/api/v1/recommend?page=2');
  });
});
test('safe server errors and HTML fallback',async()=>{
  for(const status of [409,503])await mockFetch(async()=>new Response(JSON.stringify({error:{code:status===409?'release_changed':'release_unavailable',message:'请刷新'}}),{status,headers:{'Content-Type':'application/json'}}),async()=>{
    await assert.rejects(fetchJson('/api/v1/meta'),e=>e instanceof ApiClientError&&e.status===status&&e.message==='请刷新');
  });
  await mockFetch(async()=>new Response('<html>secret</html>',{status:500}),async()=>{
    await assert.rejects(fetchJson('/api/v1/meta'),e=>!e.message.includes('secret')&&!e.message.includes('<html>'));
  });
});
test('AbortError is preserved and network error is safe',async()=>{
  const abort=new DOMException('aborted','AbortError');
  await mockFetch(async()=>{throw abort;},async()=>await assert.rejects(fetchJson('/api/v1/meta'),e=>e===abort));
  await mockFetch(async()=>{throw new Error('private internal detail');},async()=>await assert.rejects(fetchJson('/api/v1/meta'),e=>e.code==='network_error'&&!e.message.includes('private')));
});
test('downloads contain server bytes, not client scores',async()=>{
  const body={request:{confirmed:true},fingerprints:{decision_sha256:'d',ranking_sha256:'r'}};let sent;
  await mockFetch(async(path,init)=>{sent={path,init};return new Response(new Uint8Array([239,187,191,65]));},async()=>{
    const blob=await fetchDownload('csv',body);assert.deepEqual([...new Uint8Array(await blob.arrayBuffer())],[239,187,191,65]);
    assert.equal(sent.path,'/api/v1/export/csv');assert.deepEqual(JSON.parse(sent.init.body),body);
  });
});
test('rejects arbitrary remote URLs and unknown formats before fetch',async()=>{
  let calls=0;await mockFetch(async()=>{calls++;return new Response('{}');},async()=>{
    for(const path of ['https://evil.test','//evil.test','/api/v1/../data/catalog.sqlite','/.env'])await assert.rejects(fetchJson(path));
    await assert.rejects(fetchDownload('exe',{}));assert.equal(calls,0);
  });
});
