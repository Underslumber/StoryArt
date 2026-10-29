import { randomUUID } from 'node:crypto';

// Browser-local identity state only. This never changes DOM markup or sends a prompt.
export function nativeImageProbe(stage, key, url, resolvePlus, navigation, turnRoots, inputSelectors) {
  const states = window.__oracleNativeImageActions ??= new Map();
  const saved = states.get(key);
  if (stage === 'cleanup' || stage === 'disarm' || stage === 'receipt') {
    if(stage !== 'receipt')saved?.cleanup?.(); if (stage === 'cleanup') states.delete(key);
    return saved ? { sawKeyDown: saved.sawKeyDown === true, clicked: saved.clicked === true, blocked: saved.blocked ?? null, ...(saved.pointer ? {pointerDown:saved.pointerDown===true,traces:saved.traces} : {}) } : null;
  }
  const fail = reason => ({ ok: false, reason });
  if (location.href !== url || navigation.contextMatches !== true || navigation.workSelected || navigation.modeUnverified) return fail('navigation-unverified');
  const visible = node => {
    if (!(node instanceof HTMLElement) || !node.isConnected) return false;
    const r = node.getBoundingClientRect(); if (!(r.width > 0 && r.height > 0)) return false;
    for (let n = node; n instanceof HTMLElement; n = n.parentElement) {
      const s = window.getComputedStyle(n);
      if (s.display === 'none' || s.visibility === 'hidden' || s.visibility === 'collapse' || Number(s.opacity) === 0 || n.getAttribute('aria-hidden') === 'true') return false;
    }
    return true;
  };
  const mains = document.querySelectorAll('main');
  if (mains.length !== 1) return fail('main-ambiguous');
  const main = mains[0], plus = resolvePlus.button, form = plus?.closest('form');
  const editable = node => !node.hasAttribute('disabled') && node.disabled !== true && node.getAttribute('aria-disabled') !== 'true' && !node.hasAttribute('readonly') && node.readOnly !== true && node.getAttribute('aria-readonly') !== 'true' && (node.tagName === 'TEXTAREA' || node.isContentEditable === true);
  const editorNodes = () => Array.from(new Set(inputSelectors.flatMap(selector => Array.from(main.querySelectorAll(selector))))).filter(node => visible(node) && editable(node));
  const ownerValid = () => !resolvePlus.ambiguous && plus instanceof HTMLElement && plus.isConnected && form?.isConnected && main.contains(form) && plus.closest('form') === form && editorNodes().length === 1 && form.contains(editorNodes()[0]);
  if (!ownerValid()) return fail('composer-owner-unverified');
  if (stage === 'capture') {
    if (plus.getAttribute('aria-expanded') === 'true') return fail('already-expanded');
    states.set(key, { main, plus, form, nodes: new WeakSet(document.body.querySelectorAll('*')), turns: Array.from(turnRoots), sawKeyDown: false, clicked: false });
    return { ok: true, captured: true };
  }
  if (!saved || saved.main !== main || saved.plus !== plus || saved.form !== form) return fail('owner-identity-changed');
  const rect = node => node.getBoundingClientRect();
  const excluded = node => Boolean(node.closest('aside,nav,[role="navigation"]')) || saved.turns.some(turn => turn === node || turn.contains(node) || node.contains(turn));
  const aligned = root => {
    const r = rect(root), m = rect(main), f = rect(form), aboveGap = f.y - r.y - r.height, belowGap = r.y - f.y - f.height;
    const adjacent = (aboveGap >= -2 && aboveGap <= 12) || (belowGap >= -2 && belowGap <= 12);
    return root.parentElement === document.body && !saved.nodes.has(root) && visible(root) && !excluded(root) && r.x >= m.x && r.y >= m.y && r.x+r.width <= m.x+m.width && r.y+r.height <= m.y+m.height && Math.abs(r.x-f.x) <= 2 && Math.abs(r.width-f.width) <= 2 && adjacent;
  };
  const norm = text => String(text ?? '').replace(/\s+/g, ' ').trim();
  const editorText = node => norm(node.tagName === 'TEXTAREA' ? node.value : node.textContent);
  const hit = node => { const r = rect(node), n = document.elementFromPoint(r.x+r.width/2, r.y+r.height/2); return n === node || Boolean(n && node.contains(n)); };
  const resolve = () => {
    if (plus.getAttribute('aria-expanded') !== 'true') return fail('plus-not-expanded');
    const roots = Array.from(document.body.children).filter(aligned);
    if (roots.length !== 1) return fail('portal-ambiguous');
    const portal = roots[0];
    // Count all exact leaves in the proved owned portal, including hidden duplicates.
    const leaves = Array.from(portal.querySelectorAll('*')).filter(node => node.children.length === 0 && norm(node.textContent) === 'Create image');
    if (leaves.length !== 1) return fail('label-ambiguous');
    const leaf = leaves[0], target = leaf.closest('button');
    if (!portal.contains(leaf) || !visible(leaf) || !hit(leaf) || !target || !portal.contains(target) || target.type !== 'button' || target.tabIndex !== 0 || !visible(target) || !hit(target) || target.hasAttribute('disabled') || target.getAttribute('aria-disabled') === 'true') return fail('native-button-unverified');
    return { ok: true, portal, leaf, target };
  };
  if (stage === 'ready' || stage === 'prepare' || stage === 'preparePointer') {
    if (!form.contains(document.activeElement)) return fail('composer-input-context-changed');
    if (editorNodes().some(editorText)) return fail('composer-not-blank');
    const selected = resolve(); if (!selected.ok) return selected;
    if (stage === 'ready') return { ok: true, ready: true };
    if(stage === 'preparePointer') {
      const box=rect(selected.leaf),point={x:box.x+box.width/2,y:box.y+box.height/2};
      Object.assign(saved,selected,{pointer:true,point,traces:[]});
      saved.install=(navFn,plusFn)=>{
        const predicates=event=>{
          let nav, currentPlus, current;
          try{nav=navFn();currentPlus=plusFn();current=resolve();}catch{return {evaluation:false};}
          return {evaluation:true,url:location.href===url,navigation:nav.contextMatches===true&&!nav.workSelected&&!nav.modeUnverified,plusIdentity:!currentPlus.ambiguous&&currentPlus.button===plus,ownedForm:ownerValid(),blank:!editorNodes().some(editorText),portal:current.ok===true&&current.portal===saved.portal,leaf:current.ok===true&&current.leaf===saved.leaf,target:current.ok===true&&current.target===saved.target,hit:hit(saved.leaf)&&hit(saved.target),enabled:saved.target.isConnected&&saved.target.type==='button'&&!saved.target.hasAttribute('disabled')&&saved.target.getAttribute('aria-disabled')!=='true',trusted:event.isTrusted===true,eventTarget:event.target instanceof Node&&saved.target.contains(event.target),focus:form.contains(document.activeElement)||document.activeElement===saved.target,sequence:event.type==='pointerdown'?!saved.pointerDown:saved.pointerDown===true,notPreviouslyBlocked:!saved.blocked};
        };
        const cancel=event=>{event.preventDefault();event.stopImmediatePropagation();};
        const onPointer=event=>{
          const checks=predicates(event),ok=Object.values(checks).every(value=>value===true);
          const transitionObserved=saved.pointerDown===true&&checks.url&&checks.navigation&&checks.plusIdentity&&checks.ownedForm&&checks.blank&&plus.getAttribute('aria-expanded')!=='true'&&!saved.portal.isConnected;
          saved.traces.push({event:event.type,predicates:checks,allowed:ok,transitionObserved:Boolean(transitionObserved)});
          if(!ok){saved.blocked??='pointer-predicate-changed';cancel(event);return;}
          if(event.type==='pointerdown')saved.pointerDown=true;
          if(event.type==='click')saved.clicked=true;
        };
        const onSubmit=event=>{saved.blocked??='submit-refused';saved.traces.push({event:'submit',allowed:false});cancel(event);};
        const names=['pointerdown','mousedown','pointerup','mouseup','click'];
        for(const name of names)window.addEventListener(name,onPointer,true);window.addEventListener('submit',onSubmit,true);
        saved.cleanup=()=>{for(const name of names)window.removeEventListener(name,onPointer,true);window.removeEventListener('submit',onSubmit,true);};
      };
      return {ok:true,point};
    }
    Object.assign(saved, selected); selected.target.focus({ preventScroll: true });
    if (document.activeElement !== selected.target) return fail('native-button-focus-failed');
    const check = () => {
      try {
        // Re-evaluate supplied canonical resolvers and navigation at the actual event.
        const nav = saved.navigation(), currentPlus = saved.resolvePlus();
        if (location.href !== url || nav.contextMatches !== true || nav.workSelected || nav.modeUnverified || currentPlus.ambiguous || currentPlus.button !== plus || !ownerValid() || editorNodes().some(editorText)) return fail('event-context-changed');
        const current = resolve();
        if (!current.ok || current.portal !== saved.portal || current.leaf !== saved.leaf || current.target !== saved.target || document.activeElement !== saved.target) return fail('event-target-changed');
        return { ok: true };
      } catch { return fail('event-check-failed'); }
    };
    // Event-time functions are installed by the expression builder below.
    saved.install = (navFn, plusFn) => {
      saved.navigation = navFn; saved.resolvePlus = plusFn;
      const cancel = (event, reason) => { event.preventDefault(); event.stopImmediatePropagation(); saved.blocked ??= reason; };
      const onKey = event => {
        if (event.key !== 'Enter') return;
        // Native selection may focus the editor synchronously. Never deliver Enter-up there.
        if (event.type === 'keyup') { event.preventDefault(); event.stopImmediatePropagation(); return; }
        const state = check();
        if (!event.isTrusted || saved.sawKeyDown || event.target !== saved.target || !state.ok || saved.blocked || editorNodes().some(editorText)) cancel(event, state.reason ?? 'unexpected-keydown');
        else saved.sawKeyDown = true;
      };
      const onClick = event => {
        const state = check();
        if (!event.isTrusted || !saved.sawKeyDown || saved.clicked || !state.ok || !(event.target instanceof Node) || !saved.target.contains(event.target) || saved.blocked) cancel(event, state.reason ?? 'unexpected-click');
        else saved.clicked = true;
      };
      const onSubmit = event => cancel(event, 'submit-refused');
      window.addEventListener('keydown', onKey, true); window.addEventListener('keyup', onKey, true); window.addEventListener('click', onClick, true); window.addEventListener('submit', onSubmit, true);
      saved.cleanup = () => { window.removeEventListener('keydown', onKey, true); window.removeEventListener('keyup', onKey, true); window.removeEventListener('click', onClick, true); window.removeEventListener('submit', onSubmit, true); };
    };
    return { ok: true, focused: true };
  }
  if (stage === 'post') {
    // Return actual bounded composer signals. Their meaning requires actual-state review.
    return { ok: true, observedOnly: true, plusExpanded: plus.getAttribute('aria-expanded'), portalConnected: saved.portal?.isConnected === true, editor: editorNodes().map(n => ({ tag: n.tagName.toLowerCase(), placeholder: n.getAttribute('placeholder'), ariaLabel: n.getAttribute('aria-label'), empty: !editorText(n) })), controls: Array.from(form.querySelectorAll('button')).filter(visible).map(n => ({ type: n.type, label: norm(n.getAttribute('aria-label') || n.textContent).slice(0, 120), pressed: n.getAttribute('aria-pressed'), disabled: n.hasAttribute('disabled') })).slice(0, 20) };
  }
  return fail('unknown-stage');
}

export function buildNativeImageExpression(stage, key, url, actions) {
  // Cleanup is independent of URL, DOM resolution, and navigation validation failures.
  if (stage === 'cleanup' || stage === 'disarm' || stage === 'receipt') return `(${nativeImageProbe.toString()})(${JSON.stringify(stage)},${JSON.stringify(key)},${JSON.stringify(url)},null,null,null)`;
  const resolver = actions.plusResolver(), nav = actions.navigationValidation(url);
  return `(() => {const result=(${nativeImageProbe.toString()})(${JSON.stringify(stage)},${JSON.stringify(key)},${JSON.stringify(url)},${resolver},${nav},${actions.turnRootsExpression()},${JSON.stringify(actions.inputSelectors)});if(result.ok&&['prepare','preparePointer'].includes(${JSON.stringify(stage)}))window.__oracleNativeImageActions.get(${JSON.stringify(key)}).install(()=>${nav},()=>${resolver});return result;})()`;
}

// Diagnostic pointer primitive: post-state and submit protection survive uncertain
// delivery or a provider selecting on pointerdown before the later click exists.
export async function activateNativeImageToolPointer(Runtime,Input,url,actions) {
  if(typeof Input?.dispatchMouseEvent!=='function'||!Array.isArray(actions.inputSelectors)||!actions.inputSelectors.length||['plusResolver','navigationValidation','turnRootsExpression','plus','plusStay','delay'].some(k=>typeof actions[k]!=='function'))throw new Error('Canonical pointer dependencies unavailable; no activation');
  const key=randomUUID();
  const evaluate=async stage=>{
    const response=await Runtime.evaluate({expression:buildNativeImageExpression(stage,key,url,actions),returnByValue:true});
    if(response?.exceptionDetails)throw new Error(`Native pointer ${stage} evaluation failed`);
    return response?.result?.value;
  };
  let delivery=null,post=null,screenshot=null,failure=null,selectionOperations=0,selectionInputAttempts=0;
  try {
    const captured=await evaluate('capture');if(!captured?.ok)throw new Error(`snapshot:${captured?.reason}`);
    const plus=await actions.plus(Runtime,Input,url);
    if(plus?.method!=='trusted-keyboard'||plus.startUrl!==url||plus.delivery?.blocked||!plus.delivery?.sawKeyDown||!plus.delivery?.clicked)throw new Error('plus-delivery-unverified');
    let ready;for(let attempt=0;attempt<20;attempt++){
      await actions.plusStay(Runtime,url);ready=await evaluate('ready');if(ready?.ok)break;
      if(!['plus-not-expanded','portal-ambiguous','label-ambiguous','native-button-unverified'].includes(ready?.reason))throw new Error(`readiness:${ready?.reason}`);
      if(attempt<19)await actions.delay(100);
    }
    if(!ready?.ok)throw new Error(`readiness:${ready?.reason}`);
    const prepared=await evaluate('preparePointer');if(!prepared?.ok)throw new Error(`prepare:${prepared?.reason}`);
    selectionOperations=1;
    // One sequence only, never a retry following a transport exception.
    selectionInputAttempts=1;
    await Input.dispatchMouseEvent({type:'mousePressed',...prepared.point,button:'left',buttons:1,clickCount:1});
    await Input.dispatchMouseEvent({type:'mouseReleased',...prepared.point,button:'left',buttons:0,clickCount:1});
  } catch(error) {
    // No raw transport message/exception description is exposed in this receipt.
    failure=selectionOperations?'pointer-delivery-uncertain':'pointer-preparation-refused';
  }
  try {
    try {await actions.delay(100);post=await evaluate('post');}catch{failure??='post-observation-unavailable';}
    if(actions.captureProof&&post?.ok){try{screenshot=await actions.captureProof(Runtime,url,key);}catch{screenshot={captured:false,reason:'capture-unavailable'};}}
    try{delivery=await evaluate('receipt');}catch{failure??='delivery-receipt-unavailable';}
    return {method:'trusted-pointer',selectionInputAttempts,selectionOperations,delivery,post,screenshot,failure,modeReady:false};
  } finally {
    try{await evaluate('cleanup');}catch{
      const error=new Error('Native pointer cleanup unavailable');
      error.nativeImageDiagnostic={method:'trusted-pointer',selectionInputAttempts,selectionOperations,delivery,post,screenshot,failure:'cleanup-unavailable',modeReady:false};throw error;
    }
  }
}

export function assertNativeImageProject(url,projectId) {
  if(!/^g-p-[a-f0-9]+$/.test(projectId??''))throw new Error('Native image pinned project ID invalid');
  const parsed=new URL(url);
  const route=parsed.pathname.split('/');
  if(parsed.origin!=='https://chatgpt.com'||route[1]!=='g'||!(route[2]===projectId||route[2]?.startsWith(projectId+'-'))||parsed.search||parsed.hash)throw new Error('Native image pinned project route changed');
  return parsed.href;
}

export async function createNativeImageActions() {
  const [attachments,context,turns,constants,utils]=await Promise.all([import('./attachments.js'),import('./attachmentContext.js'),import('../conversationTurns.js'),import('../constants.js'),import('../utils.js')]);
  return {inputSelectors:constants.INPUT_SELECTORS,sendSelectors:constants.SEND_BUTTON_SELECTORS,plusResolver:attachments.buildComposerPlusResolverExpression,navigationValidation:context.buildComposerNavigationValidationExpression,turnRootsExpression:()=>turns.buildConversationTurnListExpression("document.querySelector('main')"),plus:attachments.activateComposerPlus,plusStay:attachments.assertComposerPlusStayedInPlace,delay:utils.delay};
}

export function nativeImageModeProbe(url,projectId,inputSelectors,selection,navigation,expectedPrompt) {
  const fail=reason=>({ok:false,reason});
  if(location.href!==url||navigation.contextMatches!==true||navigation.workSelected||navigation.modeUnverified)return fail('navigation-changed');
  const parsed=new URL(location.href),route=parsed.pathname.split('/');
  if(parsed.origin!=='https://chatgpt.com'||route[1]!=='g'||!(route[2]===projectId||route[2]?.startsWith(projectId+'-')))return fail('project-changed');
  const visible=node=>{
    if(!(node instanceof HTMLElement)||!node.isConnected)return false;
    const rect=node.getBoundingClientRect();if(!(rect.width>0&&rect.height>0))return false;
    for(let n=node;n instanceof HTMLElement;n=n.parentElement){const css=window.getComputedStyle(n);if(css.display==='none'||css.visibility==='hidden'||css.visibility==='collapse'||Number(css.opacity)===0||n.getAttribute('aria-hidden')==='true')return false;}
    return true;
  };
  const mains=document.querySelectorAll('main');if(mains.length!==1)return fail('main-ambiguous');
  const main=mains[0],plus=selection.button,form=plus?.closest('form');
  if(selection.ambiguous||!plus?.isConnected||!form?.isConnected||!main.contains(form))return fail('owner-unverified');
  const editors=Array.from(new Set(inputSelectors.flatMap(selector=>Array.from(main.querySelectorAll(selector))))).filter(node=>visible(node)&&node.isContentEditable===true&&!node.hasAttribute('disabled')&&!node.hasAttribute('readonly')&&node.getAttribute('aria-disabled')!=='true'&&node.getAttribute('aria-readonly')!=='true');
  if(editors.length!==1||editors[0].closest('form')!==form)return fail('editor-ambiguous');
  const editor=editors[0],atoms=Array.from(editor.querySelectorAll('[contenteditable="false"]'));
  const outerAtoms=atoms.filter(atom=>!atoms.some(other=>other!==atom&&other.contains(atom)));
  if(outerAtoms.length!==1)return fail('atomic-tool-ambiguous');
  const token=outerAtoms[0];
  if(token.tagName!=='SPAN'||token.isContentEditable!==false||!visible(token)||String(token.textContent??'').trim()!=='Create image')return fail('native-image-token-unverified');
  for(const atom of atoms.filter(node=>node!==token)){
    const attributes=Array.from(atom.attributes).map(attribute=>attribute.name);
    if(!token.contains(atom)||atom.tagName!=='SPAN'||!attributes.includes('contenteditable')||attributes.some(name=>!['contenteditable','class'].includes(name))||String(atom.textContent??'').trim()!==''||atom.children.length!==1||atom.children[0].tagName.toLowerCase()!=='svg')return fail('nested-atomic-tool-unverified');
    const svg=atom.children[0];
    if(String(svg.textContent??'').trim()!==''||[svg,...Array.from(svg.querySelectorAll('*'))].some(node=>['a','button','input','textarea','select','foreignobject'].includes(node.tagName.toLowerCase())||node.getAttribute('role')||node.hasAttribute('tabindex')||node.hasAttribute('contenteditable')))return fail('nested-atomic-tool-unverified');
  }
  // Derive a clone path from proved identity. Remove only that atomic token from
  // a detached clone; provider DOM remains untouched and prompt text is exact.
  const path=[];for(let node=token;node!==editor;node=node.parentNode)path.unshift(Array.from(node.parentNode.childNodes).indexOf(node));
  const clone=editor.cloneNode(true);let cloneToken=clone;for(const index of path)cloneToken=cloneToken.childNodes[index];cloneToken.remove();
  const serialize=(node,isRoot=false)=>{
    if(node.nodeType===3)return node.nodeValue??'';
    if(node.nodeType!==1)return '';
    if(node.tagName==='BR')return '\n';
    if(!['DIV','P','SPAN'].includes(node.tagName))throw new Error('unsupported-composer-node');
    const content=Array.from(node.childNodes).map(child=>serialize(child)).join('');
    return content+(!isRoot&&['DIV','P'].includes(node.tagName)?'\n':'');
  };
  const normalize=value=>String(value??'').replace(/\r\n?/g,'\n').trim();
  let text;try{text=normalize(serialize(clone,true));}catch{return fail('unsupported-composer-node');}
  if(text!==normalize(expectedPrompt))return fail('exact-prompt-mismatch');
  return {ok:true,atomicNativeImageToken:true,exactPrompt:true,promptLength:text.length};
}

export function buildNativeImageModeExpression(settings,expectedPrompt='') {
  const {url,projectId,actions}=settings;assertNativeImageProject(url,projectId);
  return `(${nativeImageModeProbe.toString()})(${JSON.stringify(url)},${JSON.stringify(projectId)},${JSON.stringify(actions.inputSelectors)},${actions.plusResolver()},${actions.navigationValidation(url)},${JSON.stringify(expectedPrompt)})`;
}
export async function assertNativeImagePromptStaged(Runtime,prompt,settings) {
  const response=await Runtime.evaluate({expression:buildNativeImageModeExpression(settings,prompt),returnByValue:true});
  if(response?.exceptionDetails||response?.result?.value?.ok!==true)throw new Error(`Native image pre-send proof refused: ${response?.exceptionDetails?'evaluation-exception':response?.result?.value?.reason??'proof-unavailable'}`);
  return response.result.value;
}
export async function assertNativeImageMode(Runtime,settings) {return assertNativeImagePromptStaged(Runtime,'',settings);}

export function buildNativeImageSendGuardExpression(stage,key,prompt,settings) {
  if(stage==='cleanup')return `(() => {const guards=window.__oracleNativeImageSendGuards;const guard=guards?.get(${JSON.stringify(key)});if(!guard)return null;guard.cleanup();guards.delete(${JSON.stringify(key)});return {delivered:guard.delivered,blocked:guard.blocked};})()`;
  const proof=buildNativeImageModeExpression(settings,prompt),{actions}=settings;
  return `(() => {
    const baseline=${proof};if(!baseline.ok)return baseline;
    const resolvePlus=()=>${actions.plusResolver()},selectors=${JSON.stringify(actions.inputSelectors)},sendSelectors=${JSON.stringify(actions.sendSelectors)};
    const visible=n=>{if(!(n instanceof HTMLElement)||!n.isConnected||n.getBoundingClientRect().width<=0||n.getBoundingClientRect().height<=0)return false;for(let current=n;current instanceof HTMLElement;current=current.parentElement){const css=window.getComputedStyle(current);if(css.display==='none'||css.visibility==='hidden'||css.visibility==='collapse'||Number(css.opacity)===0||current.getAttribute('aria-hidden')==='true')return false;}return true;};
    const editorNodes=()=>Array.from(new Set(selectors.flatMap(s=>Array.from(document.querySelector('main').querySelectorAll(s))))).filter(n=>visible(n)&&n.isContentEditable===true&&!n.hasAttribute('disabled')&&!n.hasAttribute('readonly')&&n.getAttribute('aria-disabled')!=='true'&&n.getAttribute('aria-readonly')!=='true');
    const plus=resolvePlus().button,form=plus.closest('form'),editor=editorNodes()[0];
    const outerToken=()=>{const atoms=Array.from(editor.querySelectorAll('[contenteditable="false"]'));return atoms.filter(atom=>!atoms.some(other=>other!==atom&&other.contains(atom)))[0];};
    const token=outerToken();
    const guard={delivered:false,clicked:false,submitted:false,activation:null,blocked:null};
    const cancel=(event,reason)=>{event.preventDefault();event.stopImmediatePropagation();guard.blocked??=reason;};
    const check=event=>{
      try{
        const current=${proof};if(!current.ok)return current.reason;
        if(resolvePlus().button!==plus||plus.closest('form')!==form||editorNodes()[0]!==editor||outerToken()!==token)return 'send-owner-identity-changed';
        if(!event.isTrusted||guard.blocked)return 'send-event-untrusted-or-repeated';
        if(event.type==='submit')return guard.delivered&&!guard.submitted&&event.target===form?null:'send-submit-unproved';
        if(guard.delivered&&!(event.type==='click'&&guard.activation==='keydown'&&!guard.clicked))return 'send-event-untrusted-or-repeated';
        const sends=Array.from(new Set(sendSelectors.flatMap(s=>Array.from(form.querySelectorAll(s))))).filter(n=>visible(n)&&!n.hasAttribute('disabled')&&n.getAttribute('aria-disabled')!=='true');
        const targetIsSend=sends.length===1&&sends[0].contains(event.target);
        if(event.type==='keydown'&&!editor.contains(event.target)&&!targetIsSend)return 'send-key-target-changed';
        if(event.type!=='keydown'&&!targetIsSend)return 'send-click-target-changed';
        return null;
      }catch{return 'send-event-proof-exception';}
    };
    const onEvent=event=>{
      if(event.type==='keydown'&&event.key!=='Enter')return;
      if(event.type==='keyup'){if(event.key==='Enter'){event.preventDefault();event.stopImmediatePropagation();}return;}
      const reason=check(event);if(reason){cancel(event,reason);return;}
      if(event.type==='submit')guard.submitted=true;
      if(event.type==='click'){guard.delivered=true;guard.clicked=true;guard.activation??='click';}
      if(event.type==='keydown'){guard.delivered=true;guard.activation='keydown';}
    };
    const names=['pointerdown','mousedown','click','keydown','keyup','submit'];
    for(const name of names)window.addEventListener(name,onEvent,true);
    guard.cleanup=()=>{for(const name of names)window.removeEventListener(name,onEvent,true);};
    (window.__oracleNativeImageSendGuards??=new Map()).set(${JSON.stringify(key)},guard);
    return {ok:true,guarded:true};
  })()`;
}
export async function installNativeImageSendGuard(Runtime,prompt,settings) {
  const key=randomUUID();
  const response=await Runtime.evaluate({expression:buildNativeImageSendGuardExpression('install',key,prompt,settings),returnByValue:true});
  if(response?.exceptionDetails||response?.result?.value?.ok!==true)throw new Error('Native image send guard preparation refused');
  return async()=>{
    const observed=await Runtime.evaluate({expression:buildNativeImageSendGuardExpression('cleanup',key,prompt,settings),returnByValue:true});
    const receipt=observed?.result?.value;
    if(observed?.exceptionDetails||!receipt||receipt.blocked||!receipt.delivered)throw new Error(`Native image send delivery refused: ${receipt?.blocked??'receipt-unavailable'}`);
    return receipt;
  };
}

export async function activateNativeImageTool(Runtime, Input, url, actions) {
  if (typeof Input?.dispatchKeyEvent !== 'function' || !Array.isArray(actions.inputSelectors) || !actions.inputSelectors.length || actions.inputSelectors.some(s => typeof s !== 'string' || !s) || ['plusResolver','navigationValidation','turnRootsExpression','plus','plusStay','delay'].some(k => typeof actions[k] !== 'function')) throw new Error('Canonical native action dependencies unavailable; no activation');
  const key = randomUUID(), evaluate = async stage => {
    const response = await Runtime.evaluate({ expression: buildNativeImageExpression(stage, key, url, actions), returnByValue: true });
    if (response?.exceptionDetails) {
      // Report phase and a bounded exception class, never raw exception descriptions
      // which can contain source text, URLs, or private composer data.
      const name = response.exceptionDetails.exception?.className;
      const safeName = ['ReferenceError','TypeError','SyntaxError','RangeError','EvalError','URIError','Error'].includes(name) ? name : 'JavaScript exception';
      throw new Error(`Native image ${stage} evaluation failed: ${safeName}`);
    }
    return response?.result?.value;
  };
  let delivery, primaryError, post, screenshot;
  try {
    const capture = await evaluate('capture'); if (!capture?.ok) throw new Error(`Native image snapshot refused: ${capture?.reason}`);
    // Exactly one canonical PLUS activation, never retry an uncertain transport.
    const plus = await actions.plus(Runtime, Input, url);
    if (plus?.method !== 'trusted-keyboard' || plus.startUrl !== url || plus.delivery?.blocked || !plus.delivery?.sawKeyDown || !plus.delivery?.clicked) throw new Error('Canonical PLUS delivery unverified; do not retry');
    // React can publish the portal after native PLUS delivery. Poll observations only;
    // focus and event guards are installed exactly once, after readiness is proved.
    let ready;
    for (let attempt = 0; attempt < 20; attempt++) {
      await actions.plusStay(Runtime, url);
      ready = await evaluate('ready');
      if (ready?.ok) break;
      if (!['plus-not-expanded','portal-ambiguous','label-ambiguous','native-button-unverified'].includes(ready?.reason)) throw new Error(`Native image readiness refused: ${ready?.reason}`);
      if (attempt < 19) await actions.delay(100);
    }
    if (!ready?.ok) throw new Error(`Native image readiness unavailable: ${ready?.reason}`);
    const prepared = await evaluate('prepare'); if (!prepared?.ok) throw new Error(`Native image button refused: ${prepared?.reason}`);
    const enter = { key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 };
    await Input.dispatchKeyEvent({ type: 'keyDown', ...enter, text: '\r', unmodifiedText: '\r' });
    await Input.dispatchKeyEvent({ type: 'keyUp', ...enter });
  } catch (error) { primaryError = error; }
  finally { try { delivery = await evaluate('disarm'); } catch (error) { primaryError ??= error; } }
  try {
    if (primaryError) { primaryError.nativeImageDelivery = delivery; throw primaryError; }
    if (!delivery?.sawKeyDown || !delivery?.clicked || delivery.blocked) throw new Error(`Native image delivery unverified (${delivery?.blocked ?? 'missing receipt'}); do not retry`);
    await actions.delay(100);
    await actions.plusStay(Runtime, url);
    post = await evaluate('post');
    if (!post?.ok) throw new Error(`Native image post-state unavailable: ${post?.reason}`);
    if (actions.captureProof) screenshot = await actions.captureProof(Runtime, url, key);
    return { method: 'trusted-keyboard', selectionOperations: 1, delivery, post, screenshot, modeReady: false };
  } finally { await evaluate('cleanup'); }
}
