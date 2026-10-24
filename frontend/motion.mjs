// Decorative enhancement only. All needs, prices and ranking use incumbent modules.
const reduced=matchMedia('(prefers-reduced-motion: reduce)'),fine=matchMedia('(hover: hover) and (pointer: fine)');
const toggle=document.getElementById('motion-toggle'),label=document.getElementById('motion-label');
let wanted=true,frame=0;
const allowed=()=>wanted&&!reduced.matches;
function update(){const enabled=allowed();document.body.classList.toggle('motion-off',!enabled);toggle.setAttribute('aria-pressed',String(enabled));toggle.setAttribute('aria-label',reduced.matches?'系统已关闭界面动效':enabled?'关闭界面动效':'开启界面动效');toggle.disabled=reduced.matches;label.textContent=reduced.matches?'跟随系统 · 静态':enabled?'动效开启':'动效关闭';if(!enabled){document.getAnimations().forEach(a=>a.cancel());document.querySelectorAll('.glass-panel').forEach(p=>{p.style.removeProperty('--light-x');p.style.removeProperty('--light-y')})}}
toggle.addEventListener('click',()=>{wanted=!wanted;update()});reduced.addEventListener('change',update);update();
function step(n){document.querySelectorAll('.steps li').forEach((el,i)=>{el.classList.toggle('current',i===n-1);el.classList.toggle('done',i<n-1);if(i===n-1)el.setAttribute('aria-current','step');else el.removeAttribute('aria-current')})}
document.getElementById('needs-text').addEventListener('input',()=>step(1));
document.getElementById('preferences').addEventListener('input',()=>step(2));
document.querySelectorAll('[data-example]').forEach(b=>b.addEventListener('click',()=>{const text=document.getElementById('needs-text');text.value=b.dataset.example;text.dispatchEvent(new Event('input',{bubbles:true}));text.focus()}));
const panel=document.getElementById('preferences-panel');
new MutationObserver(()=>{if(panel.open)step(2)}).observe(panel,{attributes:true,attributeFilter:['open']});
const needsStatus=document.getElementById('needs-status');
new MutationObserver(()=>{if(!needsStatus.textContent.startsWith('需求已理解'))return;step(2);if(allowed())panel.animate([{borderColor:'rgba(192,246,255,.26)'},{borderColor:'rgba(191,251,255,.9)'},{borderColor:'rgba(192,246,255,.26)'}],{duration:650,easing:'ease-out'})}).observe(needsStatus,{childList:true});
for(const id of ['results','catalog-results'])new MutationObserver(()=>{const cards=[...document.getElementById(id).querySelectorAll('.card')];if(!cards.length)return;if(id==='results')step(3);if(allowed())cards.forEach((card,i)=>card.animate([{opacity:0,transform:'translateY(12px)'},{opacity:1,transform:'translateY(0)'}],{duration:360,delay:Math.min(i,5)*55,easing:'cubic-bezier(.2,.7,.2,1)',fill:'backwards'}))}).observe(document.getElementById(id),{childList:true});
document.addEventListener('pointermove',e=>{if(!allowed()||!fine.matches)return;const p=e.target.closest('.glass-panel');if(!p)return;cancelAnimationFrame(frame);const x=e.clientX,y=e.clientY;frame=requestAnimationFrame(()=>{if(!allowed())return;const r=p.getBoundingClientRect();p.style.setProperty('--light-x',(x-r.left)/r.width*100+'%');p.style.setProperty('--light-y',(y-r.top)/r.height*100+'%')})});
