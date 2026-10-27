import {element,button} from './render.mjs';

const grades=[['all','全部'],['good','好评'],['neutral','中评'],['bad','差评'],['default_good','默认好评'],['unknown','未标注']];
export function renderReviews(data,{onFilter,onPage}={}){
  const wrap=element('section',undefined,'buyer-reviews');
  wrap.append(element('h3',`${data.model.display_name} · 淘宝买家评价`),element('p',`已收录 ${data.sample_total} 条机型评价。评价可能来自不同配置与成色，请结合购买规格和商品原标题阅读。`,'small'));
  const filters=element('div',undefined,'review-filters');filters.setAttribute('aria-label','按评价等级筛选');
  for(const [value,label] of grades){
    const count=value==='all'?data.sample_total:data.grade_counts[value]??0;
    if(value==='unknown'&&!count)continue;
    const control=button(`${label} ${count}`,()=>onFilter?.(value));control.setAttribute('aria-pressed',String(data.grade===value));filters.append(control);
  }wrap.append(filters);
  if(!data.items.length){wrap.append(element('p',!data.available?'评价数据尚未接入。':data.sample_total?'该筛选下暂无评价，可切换“全部”查看。':'暂无该机型的淘宝买家评价。','empty'));return wrap;}
  wrap.append(element('p','按评价日期从新到旧展示 · 买家昵称及评价等级沿用采集记录','small'));
  const list=element('div',undefined,'review-list');
  for(const review of data.items){
    const item=element('article',undefined,'buyer-review');const head=element('div',undefined,'review-heading');
    head.append(element('strong',review.grade_label),element('span',`${review.username||'买家昵称未记录'} · ${review.reviewed_at??'日期未记录'}`,'review-buyer small'));item.append(head);
    if(review.purchase_spec)item.append(element('p',`购买规格：${review.purchase_spec}`,'review-spec small'));
    if(review.content)item.append(element('p',review.content,'review-body'));
    if(review.follow_up){const follow=element('div',undefined,'review-followup');follow.append(element('strong','买家追评'),element('p',review.follow_up,'review-body'));item.append(follow);}
    const source=element('details',undefined,'review-source');source.append(element('summary','商品原标题与采集信息'),element('p',review.product_title||'商品标题未记录','small'),element('p',`淘宝 · 商品 ${review.product_id??'未记录'} · 采集于 ${review.collected_at??'日期未记录'}`,'small'));item.append(source);list.append(item);
  }wrap.append(list);
  const pages=Math.max(1,Math.ceil(data.total/data.page_size));const pagination=element('nav',undefined,'pagination');pagination.setAttribute('aria-label','评价分页');
  const previous=button('上一页',()=>onPage?.(data.page-1)),next=button('下一页',()=>onPage?.(data.page+1));previous.disabled=data.page<=1;next.disabled=data.page>=pages;
  pagination.append(previous,element('span',`${data.page} / ${pages} 页 · ${data.total} 条`),next);wrap.append(pagination);return wrap;
}

export function renderReviewError(message,onRetry){
  const wrap=element('div',undefined,'review-error');wrap.setAttribute('role','alert');
  wrap.append(element('p',message||'评价加载失败，请重试。'),button('重新加载评价',onRetry));return wrap;
}
