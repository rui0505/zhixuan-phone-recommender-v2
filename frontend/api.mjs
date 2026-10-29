export class ApiClientError extends Error{
  constructor(code,message,status=0){super(message);this.name='ApiClientError';this.code=code;this.status=status;}
}
function validPath(path){
  if(typeof path!=='string'||!/^\/api\/v1\/(?:meta|catalog|compare|recommend|explain|needs|feedback|intake\/(?:queue|export|draft|submit|I-[a-f0-9]{32}(?:\/(?:publish|reject))?)|admin\/feedback(?:\/(?:export|[a-f0-9-]+))?|skus\/[A-Za-z0-9_-]+(?:\/reviews)?|export\/(?:csv|xlsx|json))(?:\?[^#\\]*)?$/.test(path))
    throw new ApiClientError('invalid_request','接口路径无效');
}
async function send(path,{method='GET',body,signal,headers={}}={}){
  validPath(path);
  try{return await fetch(path,{method,signal,credentials:'same-origin',cache:'no-store',
    headers:{...headers,...(body===undefined?{}:{'Content-Type':'application/json'})},body:body===undefined?undefined:JSON.stringify(body)});}
  catch(error){if(error.name==='AbortError')throw error;throw new ApiClientError('network_error','网络连接失败，请检查服务并重试');}
}
async function serverError(response){
  try{
    const data=await response.json();const error=data?.error;
    if(typeof error?.code==='string'&&typeof error?.message==='string')return new ApiClientError(error.code,error.message,response.status);
  }catch{}
  return new ApiClientError('invalid_response','服务暂不可用或返回无效内容，请稍后重试',response.status);
}
export async function fetchJson(path,options){
  const response=await send(path,options);
  if(!response.ok)throw await serverError(response);
  try{return await response.json();}catch{throw new ApiClientError('invalid_response','服务返回无效内容，请刷新重试',response.status);}
}
export async function fetchDownload(format,body,{signal}={}){
  if(!['csv','xlsx','json'].includes(format))throw new ApiClientError('invalid_request','导出格式无效');
  const response=await send(`/api/v1/export/${format}`,{method:'POST',body,signal});
  if(!response.ok)throw await serverError(response);
  return response.blob();
}
export async function fetchFeedbackExport(token,{signal}={}){
  const response=await send('/api/v1/admin/feedback/export',{signal,headers:{Authorization:`Bearer ${token}`}});
  if(!response.ok)throw await serverError(response);
  return response.blob();
}
