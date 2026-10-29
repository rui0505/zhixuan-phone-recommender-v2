/** Refresh public data after publication or returning to an existing tab. */
export function watchCatalogUpdates(refresh,{
  windowTarget=typeof window==='undefined'?null:window,
  BroadcastChannelClass=windowTarget?.BroadcastChannel,
  throttleMs=1000,now=()=>Date.now(),setTimer=setTimeout,clearTimer=clearTimeout,
  onError=()=>{}
}={}){
  if(!windowTarget?.addEventListener||typeof refresh!=='function')return ()=>{};
  let closed=false,inFlight=false,pending=false,timer=null,lastStarted=-Infinity,channel;
  function schedule(){
    if(closed)return;pending=true;if(inFlight||timer!==null)return;
    const wait=Math.max(0,throttleMs-(now()-lastStarted));
    if(wait>0){timer=setTimer(()=>{timer=null;run();},wait);return;}run();
  }
  function run(){
    if(closed||inFlight)return;pending=false;inFlight=true;lastStarted=now();
    Promise.resolve().then(()=>closed?undefined:refresh()).catch(error=>{if(!closed)onError(error);}).finally(()=>{inFlight=false;if(pending&&!closed)schedule();});
  }
  const message=event=>{if(event?.data?.type==='catalog-published')schedule();};
  try{if(BroadcastChannelClass){channel=new BroadcastChannelClass('zhixuan-catalog');channel.addEventListener('message',message);}}catch{}
  windowTarget.addEventListener('focus',schedule);
  return ()=>{closed=true;pending=false;if(timer!==null)clearTimer(timer);windowTarget.removeEventListener('focus',schedule);channel?.close();};
}
