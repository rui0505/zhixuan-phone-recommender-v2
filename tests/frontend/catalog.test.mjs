import test from 'node:test';
import assert from 'node:assert/strict';
const catalog=await import('../../frontend/catalog.mjs').catch(()=>null);

test('catalog defaults cover all conditions and do not alias mutable filter arrays',()=>{
  assert.ok(catalog,'independent catalog helpers are missing');
  const a=catalog.catalogDefaults(),b=catalog.catalogDefaults();
  assert.deepEqual(a,{mode:'all',min_price:null,max_price:null,min_storage:null,include:[],exclude:[],q:'',category:'all',category_status:'evidence_available'});
  a.include.push('苹果');assert.deepEqual(b.include,[]);
});

test('catalog query carries its own applied fields and resets search paging independently',()=>{
  assert.ok(catalog);const filters={...catalog.catalogDefaults(),mode:'used',max_price:2000,min_storage:256,include:['华为'],exclude:['苹果'],q:'Mate 80',category:'gaming',category_status:'pending_review'};
  const query=catalog.catalogQuery(filters,2);
  assert.equal(query.get('mode'),'used');assert.equal(query.get('page'),'2');assert.equal(query.get('page_size'),'24');
  assert.equal(query.get('max_price'),'2000');assert.equal(query.has('min_price'),false);
  assert.deepEqual(query.getAll('include'),['华为']);assert.deepEqual(query.getAll('exclude'),['苹果']);
  assert.equal(query.get('q'),'Mate 80');assert.equal(query.get('category_status'),'pending_review');
});

test('model count respects selected category rather than page rows or global total',()=>{
  assert.ok(catalog);const payload={total:42,category_counts:[{id:'gaming',model_count:147,evidence_available:65,pending_review:82}]};
  assert.equal(catalog.catalogModelCount(payload,catalog.catalogDefaults()),147);
  assert.equal(catalog.catalogModelCount(payload,{...catalog.catalogDefaults(),category:'gaming'}),65);
  assert.equal(catalog.catalogModelCount(payload,{...catalog.catalogDefaults(),category:'gaming',category_status:'pending_review'}),82);
});
