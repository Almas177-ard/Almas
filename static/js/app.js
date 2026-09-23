/* Customer WebApp: catalog, card invoices, UZS wallet and encrypted-delivery views. */
(async () => {
  const A=window.Activon,{S,e,t,money,date,icon,api,toast,modal,closeModal,translate}=A;
  const state={products:[],orders:[],wallet:null,view:'home',category:'all',search:'',sort:'popular',activeProduct:null,quantity:1,poll:null,timer:null};
  if(!(await A.init())) return;
  const $=id=>document.getElementById(id);
  const imgAvatar=(node)=>{const name=S.me.name||'A';node.textContent=name.slice(0,1).toUpperCase();if(S.me.photoUrl){node.innerHTML=`<img src="${e(S.me.photoUrl)}" alt="">`}};
  ['header-avatar','profile-avatar'].forEach(id=>imgAvatar($(id)));
  $('profile-name').textContent=S.me.name;$('profile-handle').textContent=S.me.username?'@'+S.me.username:'Activon';$('profile-id').textContent=String(S.me.telegramId);
  $('admin-link').hidden=S.me.role!=='ADMIN';
  $('avatar-upload').addEventListener('change',async event=>{const file=event.target.files[0];if(!file)return;
    try{const data=await api('/api/profile/avatar',{method:'POST',body:file,raw:true,headers:{'Content-Type':file.type}});
      S.me.photoUrl=data.photoUrl;['header-avatar','profile-avatar'].forEach(id=>imgAvatar($(id)));toast(t('profile.photoSaved'));
    }catch(err){toast(err.message,true)}finally{event.target.value=''}
  });
  $('side-support').href=S.config.supportUrl;$('profile-support').href=S.config.supportUrl;
  $('hero').style.backgroundImage=`linear-gradient(90deg,rgba(10,18,47,.65),rgba(10,18,47,0) 90%),url("${S.config.heroImage}")`;
  if(S.config.logoImage){$('sidebar-logo').src=S.config.logoImage;}
  $('welcome-name').textContent=S.me.name.split(' ')[0];
  $('today-label').textContent=A.today();
  if(!S.config.checkoutEnabled||S.config.maintenance){$('notice-bar').hidden=false;$('notice-text').textContent=t(S.config.maintenance?'error.MAINTENANCE':'detail.checkoutDisabled')}
  function clearPoll(){if(state.poll)clearInterval(state.poll);if(state.timer)clearInterval(state.timer);state.poll=null;state.timer=null}
  function page(view){if(!['home','catalog','orders','wallet','profile'].includes(view))return;state.view=view;
    document.querySelectorAll('.view').forEach(el=>el.classList.toggle('active',el.id==='view-'+view));
    document.querySelectorAll('[data-nav]').forEach(el=>el.classList.toggle('active',el.dataset.nav===view));
    $('page-title').textContent=t('nav.'+view);document.title=`${S.config?.brand||'Activon'} · ${t('nav.'+view)}`;
    window.scrollTo({top:0,behavior:'smooth'});
    if(view==='orders')refreshOrders();if(view==='wallet')refreshWallet();
  }
  document.addEventListener('click',ev=>{const nav=ev.target.closest('[data-nav]');if(nav){page(nav.dataset.nav);if(nav.id==='top-search')setTimeout(()=>$('catalog-search').focus(),50)}});
  document.addEventListener('keydown',ev=>{if(ev.key==='/'&&!['INPUT','TEXTAREA'].includes(document.activeElement.tagName)){ev.preventDefault();page('catalog');$('catalog-search').focus()}});
  function renderBalances(){const value=money(S.me.balanceSom);['home-balance','wallet-balance','profile-balance'].forEach(id=>$(id).textContent=value)}
  function tagIcon(type){return {LINK:'link',COUPON:'ticket',READY_ACCOUNT:'key'}[type]||'spark'}
  function card(p){const disabled=!p.inStock||!S.config.checkoutEnabled||S.config.maintenance;
    return `<article class="product-card"><div class="product-image"><img src="${e(p.imageUrl)}" alt="${e(p.name)}" loading="lazy"><span class="product-tag">${icon(tagIcon(p.deliveryType))}${e(t('delivery.'+p.deliveryType))}</span>${!p.inStock?`<span class="out-tag">${e(t('catalog.out'))}</span>`:''}</div><div class="product-meta"><span class="product-provider">${e(p.provider)}</span><h3 title="${e(p.name)}">${e(p.name)}</h3><div class="product-info"><span>${icon('bolt')}${p.durationDays?e(t('catalog.days',{count:p.durationDays})):e(t('detail.instant'))}</span><span>${icon('box')}${e(t('catalog.stock',{count:p.stock}))}</span></div><div class="product-footer"><span class="product-price"><small>${e(t('catalog.from'))}</small><strong>${e(money(p.unitSom))}</strong></span><button class="choose-btn" data-product="${e(p.slug)}" aria-label="${e(t('catalog.select'))}: ${e(p.name)}" ${disabled?'disabled':''}>${icon('arrow-right')}</button></div></div></article>`;
  }
  function renderProducts(){const all=state.products;
    $('home-products').textContent=String(all.filter(x=>x.inStock).length);
    $('featured-products').innerHTML=all.slice(0,3).map(card).join('')||`<div class="empty-inline">${e(t('catalog.emptyTitle'))}</div>`;
    const providers=[...new Map(all.map(p=>[p.providerKey,p.provider])).entries()];
    if(!providers.some(([key])=>key===state.category))state.category='all';
    $('category-tabs').innerHTML=`<button class="${state.category==='all'?'active':''}" data-category="all">${e(t('catalog.all'))}</button>`+providers.map(([key,name])=>`<button class="${state.category===key?'active':''}" data-category="${e(key)}">${e(name)}</button>`).join('');
    let list=all.filter(p=>(state.category==='all'||p.providerKey===state.category)&&(!state.search||`${p.name} ${p.provider}`.toLocaleLowerCase().includes(state.search.toLocaleLowerCase())));
    if(state.sort==='price-low')list=[...list].sort((a,b)=>a.unitSom-b.unitSom);if(state.sort==='price-high')list=[...list].sort((a,b)=>b.unitSom-a.unitSom);
    $('catalog-products').innerHTML=list.map(card).join('');$('catalog-empty').hidden=list.length>0;$('catalog-count').textContent=t('common.items',{count:list.length});
  }
  $('category-tabs').addEventListener('click',ev=>{const btn=ev.target.closest('[data-category]');if(btn){state.category=btn.dataset.category;renderProducts()}});
  $('catalog-search').addEventListener('input',ev=>{state.search=ev.target.value.trim();renderProducts()});
  $('catalog-sort').addEventListener('change',ev=>{state.sort=ev.target.value;renderProducts()});
  document.addEventListener('click',ev=>{const button=ev.target.closest('[data-product]');if(button&&!button.disabled)showProduct(button.dataset.product)});
  async function refreshProducts(){try{state.products=(await api('/api/catalog?lang='+S.lang)).data||[];renderProducts()}catch(err){toast(err.message,true)}}
  async function refreshWallet(){try{state.wallet=await api('/api/wallet');S.me.balanceSom=state.wallet.balanceSom;renderBalances();renderWallet()}catch(err){toast(err.message,true)}}
  async function refreshOrders(){try{state.orders=(await api('/api/orders')).data||[];renderOrders()}catch(err){toast(err.message,true)}}
  function renderWallet(){if(!state.wallet)return;const labels={TOPUP:'wallet.creditNote',PURCHASE:'wallet.purchaseNote',REFUND:'wallet.refundNote',ADJUSTMENT:'wallet.adjustNote'};
    $('wallet-history').innerHTML=(state.wallet.transactions||[]).map(x=>`<div class="txn-row"><span class="txn-icon">${icon(x.amountSom>=0?'plus':'bag')}</span><span><strong>${e(t(labels[x.kind]||x.kind))}</strong><small>${e(date(x.createdAt))}</small></span><b class="${x.amountSom>=0?'positive':''}">${x.amountSom>=0?'+':'−'}${e(money(Math.abs(x.amountSom)))}</b></div>`).join('');$('wallet-empty').hidden=(state.wallet.transactions||[]).length>0;
  }
  function renderOrders(){const list=state.orders;$('orders-list').innerHTML=list.map(o=>`<article class="order-card"><img class="order-thumb" src="${e(o.imageUrl)}" alt=""><div class="order-primary"><h3>${e(o.name)}</h3><span>${e(o.code)} · ${e(date(o.createdAt))}</span></div><div class="order-side"><strong>${e(money(o.totalSom))}</strong><span class="status ${e(o.status)}">${e(t('status.'+o.status))}</span></div><button class="order-action" data-order="${e(o.code)}" aria-label="${e(t('orders.detail'))}">${icon('chevron-right')}</button></article>`).join('');$('orders-empty').hidden=list.length>0;
  }
  document.addEventListener('click',ev=>{const button=ev.target.closest('[data-order]');if(button)showOrder(button.dataset.order)});
  function copyText(text){if(navigator.clipboard?.writeText){navigator.clipboard.writeText(text).then(()=>toast(t('common.copied'))).catch(()=>fallback());}else fallback();function fallback(){const input=document.createElement('textarea');input.value=text;input.style.position='fixed';document.body.append(input);input.select();document.execCommand('copy');input.remove();toast(t('common.copied'));}}
  async function showProduct(slug){let product;
    try{product=await api('/api/catalog/'+encodeURIComponent(slug)+'?lang='+S.lang);}catch(err){toast(err.message,true);return;}
    state.activeProduct=product;state.quantity=1;
    function draw(){const p=state.activeProduct;const q=state.quantity;const total=p.unitSom*q;
      modal(`<div class="modal" aria-labelledby="modal-title"><div class="modal-head"><div><h2 id="modal-title">${e(p.name)}</h2><p>${e(p.provider)} · ${e(t('delivery.'+p.deliveryType))}</p></div><button class="modal-x" aria-label="${e(t('common.close'))}">${icon('close')}</button></div><img class="modal-product-image" src="${e(p.imageUrl)}" alt="${e(p.name)}"><div class="detail-pills"><span>${icon('box')}${e(t('detail.stock',{count:p.stock}))}</span><span>${icon('bolt')}${p.durationDays?e(t('catalog.days',{count:p.durationDays})):e(t('detail.instant'))}</span>${p.warrantyDays?`<span>${icon('shield')}${e(t('detail.warranty',{count:p.warrantyDays}))}</span>`:''}${p.bulk?`<span>${icon('spark')}${e(t('catalog.bulk'))}</span>`:''}</div><p class="modal-desc">${e(p.description||'')}</p><div class="quantity-line"><span>${e(t('detail.quantity'))}</span><div class="quantity-picker"><button id="qty-minus" aria-label="${e(t('detail.quantity'))} −" ${q<=1?'disabled':''}>${icon('minus')}</button><strong id="qty-count">${q}</strong><button id="qty-plus" aria-label="${e(t('detail.quantity'))} +" ${q>=p.maxQuantity?'disabled':''}>${icon('plus')}</button></div></div><div class="checkout-total"><span>${e(t('detail.total'))}</span><strong id="detail-total">${e(money(total))}</strong></div><span class="method-label">${e(t('detail.method'))}</span><div class="payment-methods"><label class="method-option">${icon('credit-card')}<span>${e(t('detail.card'))}</span><input type="radio" name="method" value="CARD" checked></label><label class="method-option">${icon('wallet')}<span>${e(t('detail.wallet'))}</span><input type="radio" name="method" value="WALLET"></label></div><p class="modal-note">${icon('info')}${e(t('detail.walletBalance',{amount:A.amount(S.me.balanceSom)}))}</p><button id="checkout-button" class="btn btn-primary btn-wide" ${!S.config.checkoutEnabled||S.config.maintenance?'disabled':''}>${icon('bag')}${e(t('detail.buy'))} · <span id="detail-buy-amount">${e(money(total))}</span></button><p class="modal-note">${icon('shield')}${e(t('detail.paymentHint'))}</p></div>`);
      document.getElementById('qty-minus').onclick=()=>changeQty(-1);document.getElementById('qty-plus').onclick=()=>changeQty(1);
      document.getElementById('checkout-button').onclick=async()=>{const btn=document.getElementById('checkout-button');btn.disabled=true;btn.textContent=t('common.loading');try{const method=document.querySelector('input[name="method"]:checked').value;const result=await api('/api/checkout',{method:'POST',body:{productSlug:p.slug,quantity:state.quantity,method}});await Promise.all([refreshOrders(),refreshWallet()]);if(result.error){toast(t('error.'+result.error),true);closeModal();page('orders');return;}if(result.payment)showPayment(result.payment,result.order);else if(result.order)showOrder(result.order.code);}catch(err){toast(err.message,true);btn.disabled=false;btn.textContent=t('detail.buy');if(['OUT_OF_STOCK','PRODUCT_UNAVAILABLE'].includes(err.code))refreshProducts();}};
    }
    async function changeQty(diff){const oldQty=state.quantity,newQty=oldQty+diff;if(newQty<1||newQty>state.activeProduct.maxQuantity)return;
      const controls=['qty-minus','qty-plus','checkout-button'].map(id=>document.getElementById(id));controls.forEach(el=>{if(el)el.disabled=true});
      try{const fresh=await api('/api/catalog/'+encodeURIComponent(slug)+'?lang='+S.lang+'&quantity='+newQty);
        if(!document.getElementById('qty-count')||state.activeProduct?.slug!==slug)return;
        state.quantity=newQty;state.activeProduct=fresh;document.getElementById('qty-count').textContent=newQty;
        document.getElementById('detail-total').textContent=money(fresh.unitSom*newQty);
        document.getElementById('detail-buy-amount').textContent=money(fresh.unitSom*newQty);
        document.getElementById('qty-minus').disabled=newQty<=1;document.getElementById('qty-plus').disabled=newQty>=fresh.maxQuantity;
        document.getElementById('checkout-button').disabled=!S.config.checkoutEnabled||S.config.maintenance;
      }catch(err){toast(err.message,true);if(document.getElementById('qty-count')){document.getElementById('qty-minus').disabled=oldQty<=1;document.getElementById('qty-plus').disabled=oldQty>=state.activeProduct.maxQuantity;document.getElementById('checkout-button').disabled=!S.config.checkoutEnabled||S.config.maintenance}}}
    draw();
  }
  function showPayment(p,order=null){clearPoll();let active=true;const isDemo=S.config.demo;
    const extra=order&&p.amountSom!==order.unitSom*order.quantity?`<p class="modal-note">${icon('info')}${e(t('payment.offset'))}</p>`:'';
    modal(`<div class="modal" aria-labelledby="modal-title"><div class="modal-head"><div><h2 id="modal-title">${e(t('payment.title'))}</h2><p>${e(t('payment.subtitle'))}</p></div><button class="modal-x" aria-label="${e(t('common.close'))}">${icon('close')}</button></div><div class="payment-card"><span class="payment-card-icon">${icon('credit-card')}</span><small>${e(t('payment.cardLabel'))}</small><strong class="card-number" id="payment-card-number">${e(p.card||'—')}</strong><span class="payment-amount">${e(money(p.amountSom))}</span><small>${e(t('payment.amountLabel'))}</small>${!isDemo?`<button class="copy-action" id="copy-card">${icon('copy')}${e(t('common.copy'))}</button>`:''}</div>${extra}<div class="payment-steps">${icon('shield')}<span>${e(t(isDemo?'payment.testOnly':'payment.instructions'))}</span></div><div class="payment-timer">${icon('clock')}<span id="payment-time">${e(t('payment.waiting'))}</span></div><p class="modal-note">${e(t('payment.pending'))}</p>${isDemo?`<button id="demo-confirm" class="btn btn-primary btn-wide">${icon('check')}${e(t('payment.demoConfirm'))}</button>`:''}<button id="check-payment" class="btn btn-soft btn-wide">${icon('refresh')}${e(t('payment.check'))}</button></div>`,()=>{active=false;clearPoll()});
    if(!isDemo)document.getElementById('copy-card')?.addEventListener('click',()=>copyText(p.card||''));
    async function check(){if(!active)return;try{const latest=await api('/api/payments/'+encodeURIComponent(p.id));if(latest.status==='PAID'){await Promise.all([refreshWallet(),refreshOrders()]);if(order){const o=await api('/api/orders/'+encodeURIComponent(order.code));if(['DELIVERED','REFUNDED','NEEDS_ATTENTION'].includes(o.status)){clearPoll();showOrder(order.code)}else{if(state.timer){clearInterval(state.timer);state.timer=null}const wait=document.getElementById('payment-time');if(wait)wait.textContent=t('payment.delivering');document.getElementById('demo-confirm')?.remove()}}else{clearPoll();toast(t('payment.paid'));closeModal();page('wallet')}return;}if(latest.status==='CANCELLED'){clearPoll();toast(t('payment.cancelled'),true);closeModal();refreshOrders()}}catch{}}
    document.getElementById('check-payment').onclick=check;
    if(isDemo)document.getElementById('demo-confirm').onclick=async()=>{const btn=document.getElementById('demo-confirm');btn.disabled=true;try{await api('/api/demo/payments/'+encodeURIComponent(p.id)+'/confirm',{method:'POST',body:{}});await check()}catch(err){toast(err.message,true);btn.disabled=false}};
    const tick=()=>{if(!active)return;const secs=Math.max(0,Math.floor((new Date(p.expiresAt).getTime()-Date.now())/1000));const target=document.getElementById('payment-time');if(target)target.textContent=t('payment.expires',{time:`${String(Math.floor(secs/60)).padStart(2,'0')}:${String(secs%60).padStart(2,'0')}`})};tick();state.timer=setInterval(tick,1000);state.poll=setInterval(check,5000);
  }
  async function showOrder(code){let order;try{order=await api('/api/orders/'+encodeURIComponent(code));}catch(err){toast(err.message,true);return;}
    let deliveryMarkup='';if(order.status==='DELIVERED'&&order.delivery){const lines=order.delivery.lines||[order.delivery.item];const key={LINK:'link',COUPON:'code',READY_ACCOUNT:'content'}[order.deliveryType];deliveryMarkup=order.deliveryType==='READY_ACCOUNT'?`<p class="delivery-warning">${e(t('delivery.secure'))}</p>`:'';
      deliveryMarkup+=lines.map((line,index)=>`<div class="delivery-line"><div class="delivery-label">${e(t('delivery.'+order.deliveryType))}${lines.length>1?' #'+(index+1):''}</div><div class="delivery-value">${e(line[key]||'')}</div>${line.instructions?`<p class="modal-desc">${e(t('delivery.instructions'))}: ${e(line.instructions)}</p>`:''}<button class="table-btn copy-delivery" data-copy-index="${index}">${icon('copy')}${e(t('orders.copyDelivery'))}</button></div>`).join('');
    }else if(order.status==='REFUNDED')deliveryMarkup=`<p class="pending-hint">${e(t('orders.refunded'))}</p>`;
    else if(order.status==='NEEDS_ATTENTION')deliveryMarkup=`<p class="pending-hint">${e(t('orders.attention'))}</p>`;
    else deliveryMarkup=`<p class="pending-hint">${e(t('orders.processing'))}</p>`;
    modal(`<div class="modal" aria-labelledby="modal-title"><div class="modal-head"><div><h2 id="modal-title">${e(t('orders.detail'))}</h2><p>${e(order.name)}</p></div><button class="modal-x" aria-label="${e(t('common.close'))}">${icon('close')}</button></div><div class="delivery-meta"><span>${e(t('orders.orderCode'))}</span><strong>${e(order.code)}</strong></div><div class="delivery-meta"><span>${e(t('orders.date'))}</span><strong>${e(date(order.createdAt))}</strong></div><div class="delivery-meta"><span>${e(t('orders.status'))}</span><span class="status ${e(order.status)}">${e(t('status.'+order.status))}</span></div><div class="delivery-meta"><span>${e(t('orders.qty'))}</span><strong>${order.quantity}</strong></div><div class="delivery-meta"><span>${e(t('orders.amount'))}</span><strong>${e(money(order.totalSom))}</strong></div>${order.upstreamOrderCode?`<div class="delivery-meta"><span>${e(t('orders.partnerCode'))}</span><strong>${e(order.upstreamOrderCode)}</strong></div>`:''}<div class="delivery-box">${deliveryMarkup}</div></div>`);
    document.querySelectorAll('.copy-delivery').forEach(btn=>btn.addEventListener('click',()=>{const line=(order.delivery.lines||[order.delivery.item])[+btn.dataset.copyIndex];const key={LINK:'link',COUPON:'code',READY_ACCOUNT:'content'}[order.deliveryType];copyText(line[key]||'')}));
    // A closed invoice can still be paid from order history when the modal is reopened.
    if(order.payment?.status==='PENDING'){const holder=document.createElement('button');holder.className='btn btn-soft btn-wide';holder.textContent=t('payment.title');holder.onclick=()=>showPayment(order.payment,order);document.querySelector('.delivery-box')?.append(holder)}
  }
  $('topup-button').onclick=async()=>{const amount=Number($('topup-amount').value);if(!Number.isSafeInteger(amount)||amount<10000||amount>10000000){toast(t('error.INVALID_AMOUNT'),true);return}const btn=$('topup-button');btn.disabled=true;try{const result=await api('/api/wallet/topup',{method:'POST',body:{amountSom:amount}});if(result.error)toast(t('error.'+result.error),true);else showPayment(result.payment);}catch(err){toast(err.message,true)}finally{btn.disabled=false}};
  document.querySelectorAll('[data-amount]').forEach(button=>button.onclick=()=>{document.querySelectorAll('[data-amount]').forEach(b=>b.classList.remove('active'));button.classList.add('active');$('topup-amount').value=button.dataset.amount});
  $('topup-amount').addEventListener('input',()=>document.querySelectorAll('[data-amount]').forEach(b=>b.classList.remove('active')));
  if(!S.config.checkoutEnabled||S.config.maintenance)$('topup-button').disabled=true;
  document.addEventListener('activon:language',async()=>{const oldSearch=$('catalog-search').value;try{await refreshProducts()}catch{}$('catalog-search').value=oldSearch;renderBalances();renderWallet();renderOrders();$('page-title').textContent=t('nav.'+state.view);document.title=`${S.config?.brand||'Activon'} · ${t('nav.'+state.view)}`;$('today-label').textContent=A.today();if(!$('notice-bar').hidden)$('notice-text').textContent=t(S.config.maintenance?'error.MAINTENANCE':'detail.checkoutDisabled')});
  renderBalances();await Promise.all([refreshProducts(),refreshOrders(),refreshWallet()]);
  setInterval(()=>{if(document.hidden)return;if(state.view==='orders')refreshOrders();if(state.view==='wallet')refreshWallet()},12000);
  const start=location.hash.slice(1);if(['home','catalog','orders','wallet','profile'].includes(start))page(start);
})();
