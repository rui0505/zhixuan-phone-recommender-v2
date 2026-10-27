import test from 'node:test';
import assert from 'node:assert/strict';
import {renderReviews,renderReviewError} from '../../frontend/reviews.mjs';
class Node{
 constructor(tag){this.tagName=tag;this.children=[];this.attributes={};this.listeners={};this._text='';}
 set textContent(value){this._text=String(value);this.children=[];}
 get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
 append(...nodes){this.children.push(...nodes);}
 setAttribute(key,value){this.attributes[key]=String(value);}
 addEventListener(key,fn){this.listeners[key]=fn;}
}
globalThis.document={createElement:tag=>new Node(tag)};
const walk=node=>[node,...node.children.flatMap(walk)];
const payload={model:{display_name:'测试机'},available:true,sample_total:21,total:21,grade:'all',page:1,page_size:20,grade_counts:{good:20,bad:1},items:[{username:'买家<script>昵称',grade_label:'好评',content:'<img src=x onerror=alert(1)>',follow_up:'真实追评',purchase_spec:'256GB',product_title:'商品原题'}]};
test('source nickname, review and followup stay inert and scoped to a model',()=>{
 const view=renderReviews(payload);assert.match(view.textContent,/<img src=x onerror=alert\(1\)>/);assert.match(view.textContent,/真实追评/);assert.match(view.textContent,/不同配置与成色/);assert.match(view.textContent,/买家<script>昵称/);assert.doesNotMatch(view.textContent,/匿名买家/);
 assert.equal(walk(view).filter(n=>n.tagName==='img'||n.tagName==='script').length,0);
});
test('grade and pagination actions keep their intended query values',()=>{
 let grade,page;const view=renderReviews(payload,{onFilter:v=>grade=v,onPage:v=>page=v});
 const controls=walk(view).filter(n=>n.tagName==='button');controls.find(n=>n.textContent==='差评 1').listeners.click();assert.equal(grade,'bad');
 controls.find(n=>n.textContent==='下一页').listeners.click();assert.equal(page,2);assert.equal(controls.find(n=>n.textContent==='上一页').disabled,true);
});
test('empty filtered data and request failures provide recovery',()=>{
 assert.match(renderReviews({...payload,items:[]}).textContent,/切换“全部”/);assert.match(renderReviews({...payload,sample_total:0,items:[]}).textContent,/暂无该机型/);
 let retried=false;const error=renderReviewError('网络失败',()=>retried=true);walk(error).find(n=>n.tagName==='button').listeners.click();assert.equal(retried,true);
});
