/* Shared same-origin client, translations, theme, Telegram WebApp auth and modal system. */
(() => {
  const S = {config: null, me: null, lang: localStorage.getItem('activon.lang') || 'uz', theme: localStorage.getItem('activon.theme') || 'light', modalClose: null};
  const allowedIcons = new Set(['spark','grid','bag','wallet','user','moon','sun','globe','search','arrow-right','chevron-right','chevron-left','check','shield','bolt','link','ticket','key','plus','minus','copy','close','receipt','settings','sliders','refresh','upload','box','credit-card','chart','users','alert','clock','external','help','arrow-up-right','info','filter','eye','logout']);
  const e = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const icon = (name, cls='') => `<svg class="icon ${cls}" aria-hidden="true"><use href="/static/img/icons.svg#${allowedIcons.has(name) ? name : 'spark'}"></use></svg>`;
  function t(key, args={}) {
    const raw = S.config?.textOverrides?.[S.lang]?.[key] || window.I18N?.[S.lang]?.[key] || window.I18N?.uz?.[key] || key;
    return String(raw).replace(/\{([a-zA-Z]+)\}/g, (_, name) => String(args[name] ?? `{${name}}`));
  }
  function amount(n) { const num = Math.trunc(Number(n) || 0); const text=String(Math.abs(num)).replace(/\B(?=(\d{3})+(?!\d))/g, S.lang==='en'?',':' '); return (num<0?'−':'')+text; }
  function money(n) { return t('common.som', {amount: amount(n)}); }
  function today() {const months={uz:['yanvar','fevral','mart','aprel','may','iyun','iyul','avgust','sentabr','oktabr','noyabr','dekabr'],ru:['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'],en:['January','February','March','April','May','June','July','August','September','October','November','December']};const d=new Date();return S.lang==='en'?`${months.en[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`:`${d.getDate()} ${months[S.lang][d.getMonth()]} ${d.getFullYear()}`;}
  function date(value) { if (!value) return ''; try { return new Intl.DateTimeFormat({uz:'uz-UZ',ru:'ru-RU',en:'en-US'}[S.lang],{day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(value)); } catch { return value; } }
  function translate(root=document) {
    document.documentElement.lang = S.lang;
    root.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
    root.querySelectorAll('[data-i18n-placeholder]').forEach(el => { el.placeholder = t(el.dataset.i18nPlaceholder); });
    root.querySelectorAll('[data-i18n-aria]').forEach(el => { el.setAttribute('aria-label', t(el.dataset.i18nAria)); });
    document.querySelectorAll('#language-select,#profile-language').forEach(el => {el.value = S.lang});
  }
  function toast(text, isError=false) {
    const el=document.getElementById('toast'); if(!el) return;
    el.textContent=text;el.className='toast show' + (isError?' error':'');clearTimeout(toast.timer);toast.timer=setTimeout(()=>{el.className='toast'},3900);
  }
  function closeModal(){const root=document.getElementById('modal-root'),back=document.getElementById('modal-backdrop');if(root)root.hidden=true;if(back)back.hidden=true;document.body.style.overflow='';if(S.modalClose){const cb=S.modalClose;S.modalClose=null;cb();}}
  function modal(markup,onClose=null){closeModal();S.modalClose=onClose;const root=document.getElementById('modal-root');root.innerHTML=markup;root.hidden=false;document.getElementById('modal-backdrop').hidden=false;document.body.style.overflow='hidden';root.querySelector('.modal-x')?.addEventListener('click',closeModal);root.querySelector('[autofocus]')?.focus();}
  document.getElementById('modal-backdrop')?.addEventListener('click',closeModal);
  document.addEventListener('keydown', event=>{if(event.key==='Escape')closeModal()});
  async function api(url,{method='GET',body,headers={},raw=false}={}){
    const h={...headers};if(body!==undefined && !raw)h['Content-Type']='application/json';
    if(method!=='GET' && S.me?.csrfToken)h['X-CSRF-Token']=S.me.csrfToken;
    let response;
    try{response=await fetch(url,{method,credentials:'same-origin',headers:h,body:raw?body:(body===undefined?undefined:JSON.stringify(body)),cache:'no-store'});}catch{const err=new Error(t('common.connection'));err.code='NETWORK';throw err;}
    let data;try{data=await response.json();}catch{data={};}
    if(!response.ok){const code=data?.error?.code || (response.status===401?'UNAUTHORIZED':response.status===403?'FORBIDDEN':'UNKNOWN');const err=new Error(window.I18N?.[S.lang]?.['error.'+code] ? t('error.'+code):t('common.error'));err.code=code;err.status=response.status;throw err;}
    return data;
  }
  function theme(value){S.theme=value==='dark'?'dark':'light';localStorage.setItem('activon.theme',S.theme);document.documentElement.dataset.theme=S.theme;
    document.querySelectorAll('#theme-toggle .icon use').forEach(el=>el.setAttribute('href','/static/img/icons.svg#'+(S.theme==='dark'?'sun':'moon')));
    const toggle=document.getElementById('profile-theme');if(toggle)toggle.setAttribute('aria-pressed',S.theme==='dark'?'true':'false');
    try{window.Telegram?.WebApp?.setHeaderColor(S.theme==='dark'?'#15213b':'#ffffff');window.Telegram?.WebApp?.setBackgroundColor(S.theme==='dark'?'#0b1530':'#f4f7fd')}catch{}
  }
  theme(S.theme);
  async function language(lang){if(!window.I18N?.[lang])return;S.lang=lang;localStorage.setItem('activon.lang',lang);translate();if(S.me){try{await api('/api/preferences',{method:'POST',body:{language:lang}});}catch{} }document.dispatchEvent(new Event('activon:language'));}
  async function init(){
    try{S.config=await api('/api/public/config');}catch{document.getElementById('boot').hidden=true;document.getElementById('auth-gate').hidden=false;translate();return false;}
    translate();theme(S.theme);
    const tg=window.Telegram?.WebApp;
    try{tg?.ready();tg?.expand();}catch{}
    try{S.me=await api('/api/me');}catch{
      try{
        if(tg?.initData){await api('/api/auth/telegram',{method:'POST',body:{initData:tg.initData}});S.me=await api('/api/me');}
        else if(S.config.demo){await api('/api/demo/login',{method:'POST',body:{}});S.me=await api('/api/me');}
      }catch{S.me=null;}
    }
    document.getElementById('boot').hidden=true;
    if(!S.me){document.getElementById('auth-gate').hidden=false;const demo=document.getElementById('demo-login');if(demo){demo.hidden=!S.config.demo;demo.onclick=async()=>{try{await api('/api/demo/login',{method:'POST',body:{}});location.reload()}catch{toast(t('common.error'),true)}};}const retry=document.getElementById('retry-login');if(retry)retry.onclick=()=>location.reload();translate();return false;}
    if(!localStorage.getItem('activon.lang'))S.lang=S.me.language||'uz';
    document.getElementById('auth-gate').hidden=true;document.getElementById('shell').hidden=false;
    const banner=document.getElementById('demo-banner');if(banner)banner.hidden=!S.config.demo;
    const name=document.querySelector('.brand-name');if(name)name.childNodes[0].textContent=S.config.brand||'Activon';
    document.querySelectorAll('#language-select,#profile-language').forEach(el=>el.onchange=event=>language(event.target.value));
    document.getElementById('theme-toggle')?.addEventListener('click',()=>theme(S.theme==='dark'?'light':'dark'));
    document.getElementById('profile-theme')?.addEventListener('click',()=>theme(S.theme==='dark'?'light':'dark'));
    translate();theme(S.theme);
    return true;
  }
  window.Activon={S,e,t,money,amount,today,date,icon,translate,toast,closeModal,modal,api,theme,language,init};
})();
