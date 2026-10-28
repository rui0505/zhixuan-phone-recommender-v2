import {fetchJson,ApiClientError} from './api.mjs';

export const CATEGORY_LABELS={issue:'遇到问题',suggestion:'改进建议',praise:'体验不错',other:'其他反馈'};
export const PAGE_LABELS={recommend:'智能选机',catalog:'手机库',compare:'我的对比',data:'数据说明',other:'整体体验'};
export const STATUS_LABELS={pending:'待审核',published:'已发布',rejected:'未通过'};

export function textNode(root,tag,text,className=''){
  const node=root.createElement(tag);node.textContent=text;node.className=className;return node;
}
export function dateLabel(value){
  const date=new Date(value);return Number.isNaN(date.getTime())?'时间未记录':new Intl.DateTimeFormat('zh-CN',{dateStyle:'medium',timeStyle:'short'}).format(date);
}
export function renderFeedbackList(root,list,items,{admin=false,moderate}={}){
  list.replaceChildren();
  if(!items.length){list.append(textNode(root,'p',admin?'此状态下暂无反馈。':'还没有通过审核的评论。欢迎留下第一条使用反馈。','feedback-empty'));return;}
  for(const item of items){
    const article=root.createElement('article');article.className='feedback-comment';
    const heading=root.createElement('div');heading.className='feedback-comment-heading';
    const avatar=textNode(root,'span',[...(item.nickname||'匿名用户')][0],'feedback-avatar');avatar.setAttribute('aria-hidden','true');
    const identity=root.createElement('div');identity.className='feedback-identity';
    identity.append(textNode(root,'strong',item.nickname||'匿名用户'),textNode(root,'time',dateLabel(item.created_at)));
    const rating=textNode(root,'span',`${item.rating} / 5 星`,'feedback-comment-rating');
    heading.append(avatar,identity,rating);
    article.append(heading,textNode(root,'p',item.content,'feedback-comment-body'),textNode(root,'p',`${CATEGORY_LABELS[item.category]??'其他反馈'} · ${PAGE_LABELS[item.page]??'整体体验'}`,'feedback-comment-meta'));
    if(admin){
      article.append(textNode(root,'p',`${STATUS_LABELS[item.status]} · 编号 ${item.id}`,'feedback-comment-meta'));
      const actions=root.createElement('div');actions.className='actions';let reviewing=false;
      for(const [status,label] of [['published','通过并发布'],['rejected','不予公开'],['pending','退回待审核']]){
        if(status===item.status)continue;
        const button=textNode(root,'button',label);button.type='button';
        button.addEventListener('click',async()=>{
          if(reviewing)return;reviewing=true;for(const action of actions.children)action.disabled=true;
          try{await moderate(item.id,status);}finally{reviewing=false;for(const action of actions.children)action.disabled=false;}
        });
        actions.append(button);
      }
      article.append(actions);
    }
    list.append(article);
  }
}
export function renderFeedbackPagination(root,container,payload,load){
  container.replaceChildren();const pages=Math.ceil(payload.total/payload.page_size);
  if(pages<2)return;
  for(const [label,next,disabled] of [['上一页',payload.page-1,payload.page<=1],['下一页',payload.page+1,payload.page>=pages]]){
    const button=textNode(root,'button',label);button.type='button';button.disabled=disabled;button.addEventListener('click',()=>load(next));
    if(label==='下一页')container.append(textNode(root,'span',`第 ${payload.page} / ${pages} 页`));
    container.append(button);
  }
}

export function submissionId(api=crypto){
  if(api.randomUUID)return api.randomUUID();
  const bytes=api.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
  const hex=[...bytes].map(value=>value.toString(16).padStart(2,'0')).join('');
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}

export class FeedbackComposer{
  constructor({post=body=>fetchJson('/api/v1/feedback',{method:'POST',body,signal:AbortSignal.timeout(15000)}),makeId=submissionId}={}){
    this.post=post;this.makeId=makeId;this.fingerprint=null;this.submissionId=null;this.busy=false;
  }
  async submit(fields){
    if(this.busy)throw new Error('正在提交，请稍候');
    const fingerprint=JSON.stringify(fields);
    if(fingerprint!==this.fingerprint){this.fingerprint=fingerprint;this.submissionId=this.makeId();}
    this.busy=true;
    try{
      const result=await this.post({...fields,submission_id:this.submissionId});
      if(typeof result?.id!=='string'||!Object.hasOwn(STATUS_LABELS,result.status))throw new ApiClientError('invalid_response','服务返回无效回执，请保留内容后重试');
      this.fingerprint=null;return result;
    }finally{this.busy=false;}
  }
}

export function feedbackError(error){return error.name==='TimeoutError'?'连接超时，请重试；已填写内容会保留。':error.message||'暂不可用，请保留内容后重试';}

export function mountFeedback(root,{get=path=>fetchJson(path,{signal:AbortSignal.timeout(15000)}),composer=new FeedbackComposer()}={}){
  const $=id=>root.getElementById(id);if(!$('feedback-form'))return;
  let sequence=0,page=1;
  const count=()=>{$('feedback-counter').textContent=`${[...$('feedback-content').value].length} / 2000 字`;};
  $('feedback-content').addEventListener('input',count);count();
  async function load(next=1){
    const current=++sequence;page=next;$('feedback-summary').textContent='正在读取已审核评论…';
    try{
      const result=await get(`/api/v1/feedback?page=${page}&page_size=20`);if(current!==sequence)return;
      if(result.total>0&&page>Math.ceil(result.total/result.page_size))return load(Math.ceil(result.total/result.page_size));
      renderFeedbackList(root,$('feedback-list'),result.items);
      $('feedback-summary').textContent=`${result.total} 条已审核评论 · ${result.average_rating===null?'暂无体验评分':`体验评分 ${result.average_rating} / 5`}`;
      renderFeedbackPagination(root,$('feedback-pagination'),result,load);
    }catch(error){if(current===sequence){$('feedback-summary').textContent='评论暂未读取成功，请点击“刷新评论”重试。';$('feedback-list').replaceChildren();$('feedback-pagination').replaceChildren();}}
  }
  $('feedback-refresh').addEventListener('click',()=>load(page));
  $('feedback-form').addEventListener('submit',async event=>{
    event.preventDefault();if(composer.busy)return;
    const rating=Number(root.querySelector('input[name="feedback-rating"]:checked')?.value);
    const content=$('feedback-content').value.trim();
    if(!rating||[...content].length<5){$('feedback-message').textContent='请先选择体验评分，并填写至少 5 字的反馈。';return;}
    $('feedback-submit').disabled=true;$('feedback-fields').disabled=true;$('feedback-message').textContent='正在提交反馈…';
    try{
      const result=await composer.submit({nickname:$('feedback-nickname').value.trim(),rating,category:$('feedback-category').value,page:$('feedback-page').value,content});
      $('feedback-form').reset();count();
      $('feedback-message').textContent=`反馈已收到，审核后公开展示。反馈编号：${result.id}`;
    }catch(error){$('feedback-message').textContent=feedbackError(error);}
    finally{$('feedback-submit').disabled=false;$('feedback-fields').disabled=false;}
  });
  load();
}
if(typeof document!=='undefined')mountFeedback(document);
