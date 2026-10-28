export function catalogDefaults(){
  return {mode:'all',min_price:null,max_price:null,min_storage:null,include:[],exclude:[],q:'',category:'all',category_status:'evidence_available'};
}

export function catalogQuery(filters,page=1){
  const query=new URLSearchParams({mode:filters.mode,page:String(page),page_size:'24',q:filters.q});
  for(const key of ['min_price','max_price','min_storage'])if(filters[key]!==null)query.set(key,String(filters[key]));
  for(const key of ['include','exclude'])for(const brand of filters[key])query.append(key,brand);
  if(filters.category!=='all'){query.set('category',filters.category);query.set('category_status',filters.category_status);}
  return query;
}

export function catalogSummary(filters){
  const price=filters.min_price===null&&filters.max_price===null?'预算不限':`${filters.min_price??'不限'}—${filters.max_price??'不限'} 元`;
  return `手机库筛选：${{all:'全部成色（含未知）',new:'仅新机记录',used:'仅二手记录'}[filters.mode]} · ${price} · ${filters.min_storage?`${filters.min_storage}GB 起`:'存储不限'} · ${filters.include.length?`包含 ${filters.include.join('、')}`:'全部品牌'}${filters.exclude.length?` · 排除 ${filters.exclude.join('、')}`:''}${filters.q?` · 搜索“${filters.q}”`:''}。与智能选机条件独立。`;
}

export function catalogModelCount(payload,filters){
  if(filters.category==='all')return payload.category_counts?.[0]?.model_count??null;
  return payload.category_counts?.find(item=>item.id===filters.category)?.[filters.category_status]??null;
}
