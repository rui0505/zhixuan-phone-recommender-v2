import {fetchJson,fetchFeedbackExport} from './api.mjs';
import {renderFeedbackList,renderFeedbackPagination,feedbackError} from './feedback.mjs';

export function secureAdminLocation(location){
  return location.protocol==='https:'||['localhost','127.0.0.1','[::1]'].includes(location.hostname);
}

export function mountFeedbackAdmin(root,{request=fetchJson,download=fetchFeedbackExport}={}){
  const $=id=>root.getElementById(id);if(!$('admin-login'))return;
  if(root.location&&!secureAdminLocation(root.location)){
    $('admin-key').disabled=true;$('admin-login-button').disabled=true;
    $('admin-message').textContent='请通过 HTTPS 地址进入反馈管理；本机预览可使用 localhost。';return;
  }
  let token='',epoch=0,sequence=0,page=1;
  function logout(message='已退出反馈管理。'){
    token='';epoch++;sequence++;$('admin-key').value='';$('admin-list').replaceChildren();$('admin-summary').textContent='';$('admin-pagination').replaceChildren();
    $('admin-workspace').hidden=true;$('admin-login-panel').hidden=false;$('admin-login-button').disabled=false;$('admin-message').textContent=message;
  }
  async function guarded(path,options={}){
    return request(path,{...options,signal:AbortSignal.timeout(15000),headers:{Authorization:`Bearer ${token}`}});
  }
  async function load(next=1){
    const currentEpoch=epoch,current=++sequence;page=next;
    const result=await guarded(`/api/v1/admin/feedback?status=${encodeURIComponent($('admin-filter').value)}&page=${page}&page_size=20`);
    if(currentEpoch!==epoch||current!==sequence)return;
    if(result.total>0&&page>Math.ceil(result.total/result.page_size))return load(Math.ceil(result.total/result.page_size));
    $('admin-workspace').hidden=false;$('admin-login-panel').hidden=true;
    $('admin-summary').textContent=`待审核 ${result.counts.pending} · 已发布 ${result.counts.published} · 未通过 ${result.counts.rejected} · 当前筛选 ${result.total} 条`;
    renderFeedbackList(root,$('admin-list'),result.items,{admin:true,moderate});
    renderFeedbackPagination(root,$('admin-pagination'),result,safeLoad);
  }
  function report(error){if(error.status===401)logout('管理密钥无效或已更换，请重新输入。');else $('admin-message').textContent=feedbackError(error);}
  async function safeLoad(next=1){const current=epoch;try{await load(next);if(current===epoch)$('admin-message').textContent='';}catch(error){if(current===epoch)report(error);}}
  async function moderate(id,status){
    const current=epoch;$('admin-message').textContent='正在保存审核结果…';
    try{
      await guarded(`/api/v1/admin/feedback/${id}`,{method:'PATCH',body:{status}});
      if(current!==epoch)return;await load(page);
      if(current===epoch)$('admin-message').textContent=status==='published'?'已通过审核并公开展示。':'已保存，评论不会公开展示。';
    }catch(error){if(current===epoch)report(error);}
  }
  $('admin-login').addEventListener('submit',async event=>{
    event.preventDefault();if($('admin-login-button').disabled)return;
    token=$('admin-key').value.trim();$('admin-key').value='';epoch++;
    if(token.length<32){logout('请输入完整的反馈管理密钥。');return;}
    $('admin-login-button').disabled=true;$('admin-message').textContent='正在验证并读取反馈…';
    const current=epoch;await safeLoad();if(current===epoch)$('admin-login-button').disabled=false;
  });
  $('admin-filter').addEventListener('change',()=>safeLoad());
  $('admin-refresh').addEventListener('click',()=>safeLoad(page));
  $('admin-logout').addEventListener('click',()=>logout());
  $('admin-export').addEventListener('click',async()=>{
    const current=epoch;$('admin-export').disabled=true;
    try{
      const blob=await download(token,{signal:AbortSignal.timeout(30000)});if(current!==epoch)return;
      const url=URL.createObjectURL(blob),link=root.createElement('a');link.href=url;link.download='zhixuan-feedback.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      $('admin-message').textContent='已导出全部状态的反馈，请妥善保存文件。';
    }catch(error){if(current===epoch)report(error);}
    finally{$('admin-export').disabled=false;}
  });
}
if(typeof document!=='undefined')mountFeedbackAdmin(document);
