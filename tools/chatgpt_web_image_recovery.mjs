import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
import {pathToFileURL,fileURLToPath} from 'node:url';
const hash=value=>createHash('sha256').update(value).digest('hex');
const normalize=value=>String(value??'').replace(/\r\n?/g,'\n').trim();
const legacyCollectorPins=Object.freeze({'chatgptImages.js':'ceae7c37a063ba06a8a59527053b1438eaa54ae69605592792392a9ebf7d3085','chatgptFiles.js':'0aaef0c1b0163a519544ce252d8bb5c17dc66ac9279c445ce83e9d415e2ab91d'});
export async function resolveRecoveryCollectorPins(request,base){
 const binding=request.runtime_binding;
 if(!binding)return legacyCollectorPins;
 if(!binding.installation||!binding.manifest_path||!binding.manifest_sha256||!path.isAbsolute(binding.manifest_path)||path.resolve(binding.installation)!==path.resolve(request.oracle_root))throw new Error('Recovery runtime binding is incomplete');
 const bytes=await fs.readFile(binding.manifest_path);if(hash(bytes)!==binding.manifest_sha256)throw new Error('Recovery runtime manifest changed');
 const manifest=JSON.parse(bytes);if(!Array.isArray(manifest.files))throw new Error('Recovery runtime file manifest is invalid');
 const pins={};
 for(const name of Object.keys(legacyCollectorPins)){
  const target=path.resolve(base,name),matches=manifest.files.filter(item=>item.path&&path.resolve(item.path)===target);
  if(!matches.length||matches.some(item=>!/^[a-f0-9]{64}$/.test(item.sha256||'')))throw new Error('Recovery collector source is not pinned: '+name);
  const actual=hash(await fs.readFile(target));if(matches.some(item=>item.sha256!==actual))throw new Error('Recovery collector source changed: '+name);
  pins[name]=actual;
 }
 return pins;
}
export function validateDesktopReceipt(receipt,request,pid){
 const own=/^OracleIsolated_[0-9a-f]{32}$/;
 if(!receipt||receipt.workerPid!==pid||receipt.state!=='RUNNING'||receipt.inheritedHandleCount!==3||!own.test(receipt.desktop??''))return false;
 if(receipt.inputBefore==='Default')return true;
 const pre=receipt.prelaunch;
 return request.windows_isolated_browser===true&&receipt.inputBefore===null&&receipt.inputReadable===false&&receipt.inputError===5&&receipt.threadDesktop==='Default'&&receipt.sessionQuerySucceeded===true&&receipt.connectState===4&&receipt.allowDisconnectedSession===true&&Number.isInteger(receipt.sessionId)&&receipt.sessionId>0&&receipt.sessionQueryError===null&&Boolean(pre)&&pre.sessionId===receipt.sessionId&&pre.inputReadable===false&&pre.inputName===null&&pre.inputError===5&&pre.sessionQuerySucceeded===true&&pre.connectState===4&&pre.sessionQueryError===null&&(pre.threadDesktop==='Default'||pre.threadDesktop===receipt.desktop);
}
export function buildNativeTokenProbe(url,inputSelectors){
 return `(() => {if(location.href!==${JSON.stringify(url)})return {mismatch:true};
 const mains=document.querySelectorAll('main');if(mains.length!==1)return {ambiguous:true};const main=mains[0];
 const nodes=Array.from(new Set(${JSON.stringify(inputSelectors)}.flatMap(selector=>Array.from(main.querySelectorAll(selector))))).filter(n=>(n.tagName==='TEXTAREA'||n.isContentEditable===true)&&n.isConnected&&n.getBoundingClientRect().width>0&&n.getBoundingClientRect().height>0);
 if(nodes.length!==1||!nodes[0].closest('form')||!main.contains(nodes[0].closest('form')))return {ambiguous:true};const editor=nodes[0];
 const norm=s=>String(s??'').replace(/\\s+/g,' ').trim();const labels=['Create image','\\u0421\\u043e\\u0437\\u0434\\u0430\\u0442\\u044c \\u0438\\u0437\\u043e\\u0431\\u0440\\u0430\\u0436\\u0435\\u043d\\u0438\\u0435'];
 const leaves=Array.from(editor.querySelectorAll('*')).filter(n=>labels.includes(norm(n.textContent))&&!Array.from(n.children).some(c=>norm(c.textContent)===norm(n.textContent)));
 const describe=n=>({tag:n.tagName.toLowerCase(),role:['button','textbox','group','none','presentation'].includes(n.getAttribute('role'))?n.getAttribute('role'):null,attributes:Array.from(n.attributes,a=>a.name).filter(a=>['role','contenteditable','data-type','data-value','data-tool','data-testid','aria-label'].includes(a)),contenteditable:['true','false','plaintext-only'].includes(n.getAttribute('contenteditable'))?n.getAttribute('contenteditable'):null,isContentEditable:n.isContentEditable===true,toolValues:Object.fromEntries(['data-type','data-value','data-tool'].filter(a=>n.hasAttribute(a)).map(a=>[a,['image','plugin','mention','tool','inline','chip'].includes(n.getAttribute(a))?n.getAttribute(a):'OTHER']))});
 const atoms=Array.from(editor.querySelectorAll('[contenteditable="false"]'));return {atomicCount:atoms.length,atoms:atoms.map(n=>({parentAtomIndex:atoms.findIndex(other=>other!==n&&other.contains(n)&&!atoms.some(between=>between!==n&&between!==other&&other.contains(between)&&between.contains(n))),containsExactLabel:leaves.some(leaf=>n.contains(leaf)),attributeNames:Array.from(n.attributes,a=>a.name),nestedSVG:Array.from(n.querySelectorAll('svg,svg *')).map(svg=>({tag:svg.tagName.toLowerCase(),attributeNames:Array.from(svg.attributes,a=>a.name),role:['img','presentation','none'].includes(svg.getAttribute('role'))?svg.getAttribute('role'):(svg.hasAttribute('role')?'OTHER':null),tabindex:['-1','0'].includes(svg.getAttribute('tabindex'))?svg.getAttribute('tabindex'):(svg.hasAttribute('tabindex')?'OTHER':null),ariaHidden:['true','false'].includes(svg.getAttribute('aria-hidden'))?svg.getAttribute('aria-hidden'):(svg.hasAttribute('aria-hidden')?'OTHER':null),textLength:String(svg.textContent??'').length,namespaceSVG:svg.namespaceURI==='http://www.w3.org/2000/svg'})),exactImageLabel:labels.includes(norm(n.textContent)),textLength:String(n.textContent??'').length,visible:n.getBoundingClientRect().width>0&&n.getBoundingClientRect().height>0,childTags:Object.fromEntries(Array.from(n.children).reduce((counts,child)=>{const tag=child.tagName.toLowerCase();counts.set(tag,(counts.get(tag)??0)+1);return counts;},new Map())),...describe(n)})),matchCount:leaves.length,editor:describe(editor),matches:leaves.slice(0,4).map(n=>{const ancestors=[];for(let depth=0;n&&depth<4;depth++,n=n.parentElement){ancestors.push(describe(n));if(n===editor)break;}return {ancestors};})};})()`;
}
export function buildNativeUserRenderProbe(url,rootsExpression,semantics){
 return `(() => {if(location.href!==${JSON.stringify(url)})return {mismatch:true};${semantics}
 const roots=${rootsExpression};const users=roots.filter(root=>oracleReadTurnRole(root)==='user');if(users.length!==1)return {ambiguous:true,userCount:users.length};const root=users[0];
 const norm=s=>String(s??'').replace(/\\s+/g,' ').trim();const leaves=Array.from(root.querySelectorAll('*')).filter(n=>norm(n.textContent)==='Create image'&&!Array.from(n.children).some(c=>norm(c.textContent)==='Create image'));
 const literals=['image','create_image','create-image','Create image','true','false'];
 const classifyHref=v=>{if(literals.includes(v))return {kind:'literal',value:v};try{const parsed=new URL(v,location.href);return {kind:'url',protocol:['image:','create-image:','create_image:','prompt:','chatgpt:','system-hint:','http:','https:'].includes(parsed.protocol)?parsed.protocol:'OTHER',host:['','image','create-image'].includes(parsed.host)?parsed.host:'OTHER',hostLength:parsed.host.length,knownLiteral:['image://','image:','create-image://','create_image://','prompt://image'].includes(v)?v:'OTHER',bareNativeMarker:v.length<60&&/^[a-z][a-z0-9_-]*:\\/\\/[a-z0-9_-]*\\/?$/.test(v)&&!['http:','https:'].includes(parsed.protocol)?v:null,sameOrigin:parsed.origin===new URL(location.href).origin,path:['/','/images','/image'].includes(parsed.pathname)?parsed.pathname:'OTHER',pathLength:parsed.pathname.length,query:Array.from(parsed.searchParams,([key,value])=>({name:key.length<=40&&/^[a-zA-Z0-9_-]+$/.test(key)?key:'OTHER',value:literals.includes(value)?value:'OTHER',valueLength:value.length})).slice(0,8),queryCount:Array.from(parsed.searchParams).length,hashPresent:Boolean(parsed.hash)};}catch{return {kind:'other',length:v.length};}};
 const describe=n=>({tag:n.tagName.toLowerCase(),attributeNames:Array.from(n.attributes,a=>a.name),contenteditable:['true','false'].includes(n.getAttribute('contenteditable'))?n.getAttribute('contenteditable'):null,childTextLengths:Array.from(n.children,c=>String(c.textContent??'').length),childNodeKinds:Array.from(n.childNodes??[],c=>c.nodeType===3?'text':c.nodeType===1?'element':'other'),nativeValues:Object.fromEntries(['data-system-hint-type','data-system-hint-label','data-inline-selection-pill','data-prompt-link-label'].filter(a=>n.hasAttribute(a)).map(a=>{const v=n.getAttribute(a);return [a,v.length<=80&&/^[\\p{L}\\p{N} _-]*$/u.test(v)?v:'OTHER'];})),promptLinkHref:n.hasAttribute('data-prompt-link-href')?classifyHref(n.getAttribute('data-prompt-link-href')):null});
 return {userCount:1,matchCount:leaves.length,leaves:leaves.slice(0,4).map(n=>{const ancestors=[];let pill=null;for(let depth=0;n&&depth<8;depth++,n=n.parentElement){ancestors.push(describe(n));if(n.hasAttribute('data-prompt-link-href'))pill=n;if(n===root)break;}const paragraph=pill?.parentElement;const ownedParagraph=paragraph&&root.contains(paragraph)&&paragraph.tagName==='P';const meaningful=ownedParagraph?Array.from(paragraph.childNodes??[]).filter(c=>c.nodeType!==3||String(c.textContent??'').trim()):[];return {ancestors,rootReached:n===root,pillDirectParagraph:!!ownedParagraph,pillFirstNonWhitespace:!!ownedParagraph&&meaningful[0]===pill,paragraphElementCount:ownedParagraph?paragraph.children.length:null,paragraphMeaningfulNodeCount:ownedParagraph?meaningful.length:null};})};})()`;
}
export function nativeFailureDiagnostic(error,expectedScreenshot=null){
 const result={failedNativeDiagnostic:true,failure:'cleanup-unavailable',modeReady:false,selectionOperations:null,selectionInputAttempts:null,delivery:null,post:null,screenshot:null};
 const dto=error?.nativeImageDiagnostic;
 const counter=value=>value===0||value===1||value===null;
 if(!dto||dto.method!=='trusted-pointer'||!counter(dto.selectionOperations)||!counter(dto.selectionInputAttempts))return result;
 const allowedKeys=new Set(['sawKeyDown','clicked','blocked','pointerDown','traces','event','predicates','allowed','transitionObserved','evaluation','url','navigation','plusIdentity','ownedForm','blank','portal','leaf','target','hit','enabled','trusted','eventTarget','focus','sequence','notPreviouslyBlocked','disabled','ok','reason','observedOnly','plusExpanded','portalConnected','editor','tag','placeholder','ariaLabel','empty','controls','type','label','pressed','expanded','captured','path','clip','x','y','width','height','scale']);
 const strings=new Set(['pointerdown','mousedown','pointerup','mouseup','click','submit','true','false','button','submit','reset','div','textarea','Image','Create image','Ask anything','Message ChatGPT','Add files and more','Remove image mode','Send prompt','OTHER','capture-unavailable','owner-changed','navigation-unverified','event-context-changed','event-target-changed','event-check-failed','unexpected-keydown','submit-refused','pointer-context-changed','pointer-predicate-changed','pointer-delivery-uncertain']);
 const safe=(value,depth=0)=>{if(depth>8)throw Error();if(value===null||typeof value==='boolean')return value;if(typeof value==='number'&&Number.isFinite(value))return value;if(typeof value==='string'&&strings.has(value))return value;if(Array.isArray(value)&&value.length<=64)return value.map(v=>safe(v,depth+1));if(value&&typeof value==='object'){const out={};for(const [key,v] of Object.entries(value)){if(!allowedKeys.has(key))throw Error();out[key]=safe(v,depth+1);}return out;}throw Error();};
 try{result.delivery=safe(dto.delivery??null);result.post=safe(dto.post??null);result.screenshot=dto.screenshot?.captured===false?safe(dto.screenshot):null;if(dto.screenshot?.captured===true&&expectedScreenshot&&dto.screenshot.path===expectedScreenshot)result.screenshot={captured:true,path:expectedScreenshot,clip:safe(dto.screenshot.clip)};result.selectionOperations=dto.selectionOperations;result.selectionInputAttempts=dto.selectionInputAttempts;}catch{return {...result,selectionOperations:null,selectionInputAttempts:null,delivery:null,post:null,screenshot:null};}
 return result;
}
export function validateRecovery(request,meta){
 if(request.native_mode_proof_only===true&&(request.activate_native_image_tool===true||request.pinned_project_editor!==true))throw new Error('Proof-only binding mismatch');
 if(!['recover','recovery-status','image-tool-inspect'].includes(request.operation)||request.opt_in!==true||request.safety_history!=='CLEAR'||request.windows_isolated_browser!==true||request.browser_mode!=='windows-isolated')throw new Error('Recovery mode not authorized');
 if(!request.session_id||meta.id!==request.session_id||meta.mode!=='browser'||meta.options?.prompt!==request.prompt||hash(request.prompt)!==request.prompt_sha256)throw new Error('Original session/prompt mismatch');
 const url=new URL(meta.browser?.runtime?.tabUrl??'');const project=request.project_binding?.route?.project_id;
 if(request.pinned_project_editor===true){
  const route=request.project_binding?.route;
  if(request.operation!=='image-tool-inspect'||(request.activate_native_image_tool!==true&&request.native_mode_proof_only!==true)||!/^g-p-[a-f0-9]+$/.test(project??'')||route.project_url!==`https://chatgpt.com/g/${project}/project`||url.href!==route.project_url||request.oracle_config.browser.chatgptUrl!==route.project_url||request.oracle_config.browser.url!==route.project_url||meta.browser.runtime.promptSubmitted!==false||meta.browser.runtime.submittedPromptHash!==null||meta.browser.runtime.conversationId)throw new Error('Pinned unsubmitted project editor mismatch');
 }else{
 if(url.origin!=='https://chatgpt.com'||!(url.pathname.split('/')[2]===project||url.pathname.split('/')[2].startsWith(project+'-'))||!/^\/g\/g-p-[a-f0-9]+[^/]*\/c\/[a-f0-9-]{36}$/.test(url.pathname)||url.pathname.split('/').at(-1)!==meta.browser.runtime.conversationId)throw new Error('Original project conversation mismatch');
 }
 if(meta.browser?.runtime?.userDataDir!==request.profile_dir||meta.browser?.config?.manualLoginProfileDir!==request.profile_dir)throw new Error('Original profile binding changed');
 if(meta.options.generateImage!==path.join(request.oracle_home,'generated','result.png')||JSON.stringify(meta.browser.config.headless)!=='false'||meta.browser.config.cookieSync!==false||meta.browser.config.manualLoginCookieSync!==false||meta.browser.config.captureProviderNative!==false)throw new Error('Original browser/output config mismatch');
 return url.href;
}
export function matchObservedPrompt(request,meta,observed){
 if(observed.url!==meta.browser.runtime.tabUrl||observed.conversationId!==meta.browser.runtime.conversationId||typeof observed.lastUserContentText!=='string'||normalize(observed.lastUserContentText)!==normalize(request.prompt))throw new Error('Original full prompt not observed; no download or send');
 if(observed.lastUserMessageId)return {conversationId:observed.conversationId,userMessageId:observed.lastUserMessageId,prompt_sha256:request.prompt_sha256,observedContentSha256:hash(observed.lastUserContentText),normalization:'CRLF-to-LF and outer trim'};
 if(observed.lastUserMessageId!==undefined&&observed.lastUserMessageId!==null||observed.turnLayout!=='observed-heading'||!Number.isInteger(observed.lastUserTurnIndex)||observed.lastUserTurnIndex<0||!Number.isInteger(observed.lastAssistantTurnIndex)||observed.lastAssistantTurnIndex!==observed.lastUserTurnIndex+1||observed.assistantFollowsLatestUser!==true||!Number.isInteger(observed.assistantCount)||observed.assistantCount<1)throw new Error('Original no-ID observed turn binding not proven; no download or send');
 return {kind:'observed-content',conversationId:observed.conversationId,userTurnIndex:observed.lastUserTurnIndex,assistantTurnIndex:observed.lastAssistantTurnIndex,prompt_sha256:request.prompt_sha256,observedContentSha256:hash(observed.lastUserContentText),normalizedObservedContentSha256:hash(normalize(observed.lastUserContentText)),normalization:'CRLF-to-LF and outer trim'};
}
export function observeStrictPromptBinding(request,meta,observed){
 if(typeof observed.lastUserContentText!=='string')return {available:false,matched:false};
 try{return {available:true,matched:true,...matchObservedPrompt(request,meta,observed)};}
 catch{return {available:true,matched:false,reason:'original-full-prompt-mismatch'};}
}
export function configureNativeUserRecovery(request,meta,environment){
 validateRecovery(request,meta);
 if(environment.ORACLE_REQUIRE_NATIVE_IMAGE_TOOL!==undefined)throw new Error('Recovery must not inherit native activation');
 if(request.operation==='recover'||request.operation==='recovery-status')environment.ORACLE_PARSE_NATIVE_IMAGE_USER='1';
}
export function buildImageMenuProbe(url,stage,resolverExpression=null){
 return `(() => {
  if(location.href!==${JSON.stringify(url)})return {mismatch:true};
  const visible=node=>node instanceof HTMLElement&&node.getBoundingClientRect().width>0&&node.getBoundingClientRect().height>0;
  const mains=document.querySelectorAll('main');if(mains.length!==1)return {ambiguous:true};const main=mains[0];
  const plusSelector='#composer-plus-btn, button[data-testid="composer-plus-btn"]';let plus=main.querySelector(plusSelector);
  let semanticAmbiguous=false;const canonical=${resolverExpression??'null'};
  if(canonical){plus=canonical.button;semanticAmbiguous=canonical.ambiguous;}
  if(!canonical&&!visible(plus)){
   const editors=Array.from(main.querySelectorAll('#prompt-textarea, textarea[name="prompt-textarea"]')).filter(visible);
   const forms=Array.from(new Set(editors.map(editor=>editor.closest('form')).filter(form=>form&&main.contains(form))));
   if(forms.length!==1)semanticAmbiguous=true;
   else{const candidates=Array.from(forms[0].querySelectorAll('button')).filter(button=>visible(button)&&button.type==='button'&&!button.hasAttribute('disabled')&&button.getAttribute('aria-disabled')!=='true'&&String(button.getAttribute('aria-label')||button.textContent||'').replace(/\\s+/g,' ').trim().toLowerCase()==='add files and more');semanticAmbiguous=candidates.length>1;plus=candidates.length===1?candidates[0]:null;}
  }
  const knownLabels={'add files and more':'Add files and more','add files':'Add files','attach files':'Attach files','open tools':'Open tools','tools':'Tools','gpt-6':'GPT-6','thinking effort':'Thinking effort'};
  const describe=node=>{const label=String(node.getAttribute('aria-label')||node.innerText||node.textContent||'').replace(/\\s+/g,' ').trim().toLowerCase();const role=node.getAttribute('role');const popup=node.getAttribute('aria-haspopup');const expanded=node.getAttribute('aria-expanded');return {tag:String(node.tagName??'').toLowerCase(),role:role===null?null:['button','radio','tab','menuitem'].includes(role)?role:'OTHER',label:knownLabels[label]??'OTHER',haspopup:popup===null?null:['true','false','menu','listbox','dialog','tree','grid'].includes(popup)?popup:'OTHER',expanded:['true','false'].includes(expanded)?expanded:null,visible:visible(node),enabled:!node.hasAttribute('disabled')&&node.getAttribute('aria-disabled')!=='true'};};
  const knownTools={'create image':'Create image','image':'Image','images':'Images','search':'Search','web search':'Search','deep research':'Deep research','study and learn':'Study and learn','agent mode':'Agent mode','\u0441\u043e\u0437\u0434\u0430\u0442\u044c \u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u0435':'Create image','\u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u0435':'Image','\u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u044f':'Images'};
  const knownToolControls=Array.from(document.querySelectorAll('button,[role="button"],[role="menuitem"],[role="option"],[data-radix-collection-item]')).filter(visible).map(node=>{const label=knownTools[String(node.getAttribute('aria-label')||node.innerText||node.textContent||'').replace(/\\s+/g,' ').trim().toLowerCase()];if(!label)return null;const ancestors=[];let current=node;for(let depth=0;current&&depth<4;depth++){ancestors.push({tag:String(current.tagName??'').toLowerCase(),role:['menu','menuitem','option','button','listbox','dialog'].includes(current.getAttribute('role'))?current.getAttribute('role'):null,attributes:['role','aria-expanded','aria-haspopup','aria-label','data-testid','id'].filter(name=>current.hasAttribute(name))});current=current.parentElement;}return {label,ancestors};}).filter(Boolean).slice(0,20);
  const postState={plusExpanded:['true','false'].includes(plus?.getAttribute('aria-expanded'))?plus.getAttribute('aria-expanded'):null,plusFocused:document.activeElement===plus,documentFocused:document.hasFocus(),knownToolControls};
  const diagnostic={plusSelectorCount:main.querySelectorAll(plusSelector).length,formCount:main.querySelectorAll('form').length,buttons:Array.from(main.querySelectorAll('button,[role="button"]')).slice(0,40).map(describe)};
  if(!visible(plus)||plus.hasAttribute('disabled')||plus.getAttribute('aria-disabled')==='true')return {plusReady:false,focused:false,ambiguous:semanticAmbiguous,...diagnostic};
  const raw=Array.from(document.querySelectorAll('main .popover, [data-radix-popper-content-wrapper], [data-floating-ui-portal], [role="menu"], [role="listbox"]')).filter(visible);
  const semantic=raw.filter(node=>['menu','listbox'].includes(node.getAttribute('role')));
  const roots=(semantic.length?semantic:raw.filter(node=>!raw.some(other=>other!==node&&node.contains(other))));
  if(${JSON.stringify(stage)}==='before'){
   if(roots.length!==0||plus.getAttribute('aria-expanded')==='true')return {ambiguous:true,menuCount:roots.length,...diagnostic,...postState};
   plus.focus({preventScroll:true});return {plusReady:true,focused:document.activeElement===plus,menuCount:0,...diagnostic,...postState};
  }
  if(roots.length!==1)return {ambiguous:true,menuCount:roots.length,...postState};
  const menu=roots[0],controls=plus.getAttribute('aria-controls');const controlled=controls?document.getElementById(controls):null;
  const activeContext=document.activeElement===plus||menu.contains(document.activeElement);
  const owner=plus.getAttribute('aria-expanded')==='true'&&activeContext&&(!controls||controlled===menu||controlled?.contains(menu));
  if(!owner)return {owned:false,activeContext,menuCount:1,...postState};
  const known={'create image':'Create image','image':'Image','\u0441\u043e\u0437\u0434\u0430\u0442\u044c \u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u0435':'Create image','\u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u0435':'Image','search':'Search','web search':'Search','deep research':'Deep research','study and learn':'Study and learn','agent mode':'Agent mode','add photos & files':'Add photos/files','add photos and files':'Add photos/files','add files':'Add photos/files'};
  const entries=Array.from(menu.querySelectorAll('[data-radix-collection-item], [role="menuitem"], [role="option"], .__menu-item, [class*="menu-item"]')).filter(visible).map(node=>String(node.getAttribute('aria-label')||node.innerText||node.textContent||'').split('\\n')[0].replace(/\\s+/g,' ').trim().toLowerCase()).map(label=>known[label]).filter(Boolean);
  return {owned:true,activeContext:true,menuCount:1,entries:Array.from(new Set(entries)),...postState};
 })()`;
}
export function buildPopupIdentityProbe(url,key,stage,resolverExpression,turnRootsExpression=null){
 return `(() => {
  if(location.href!==${JSON.stringify(url)})return {mismatch:true};
  const key=${JSON.stringify(key)},stage=${JSON.stringify(stage)};
  if(stage==='cleanup'){window.__storyartPopupIdentity?.delete(key);return {cleaned:true};}
  const rectVisible=node=>node instanceof HTMLElement&&node.getBoundingClientRect().width>0&&node.getBoundingClientRect().height>0;
  const visible=node=>{if(!rectVisible(node))return false;for(let current=node;current&&current instanceof HTMLElement;current=current.parentElement){const style=window.getComputedStyle(current);if(style.display==='none'||style.visibility==='hidden'||style.visibility==='collapse'||Number(style.opacity)===0||current.getAttribute('aria-hidden')==='true')return false;}return true;};
  const mains=document.querySelectorAll('main');if(mains.length!==1)return {ambiguous:true};const main=mains[0];
  const resolved=${resolverExpression??"({button:main.querySelector('#composer-plus-btn, button[data-testid=\"composer-plus-btn\"]')})"};const plus=resolved.button;
  if(!rectVisible(plus)||plus.isConnected!==true||resolved.ambiguous||(stage==='capture'&&!visible(plus)))return {plusUnverified:true};
  const imageLeaves=(acceptedTurns,scope=main)=>Array.from(scope.querySelectorAll('*')).filter(node=>node.children.length===0&&!acceptedTurns.some(turn=>turn===node||turn.contains(node))&&['create image','\u0441\u043e\u0437\u0434\u0430\u0442\u044c \u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u0435'].includes(String(node.textContent??'').replace(/\\s+/g,' ').trim().toLowerCase()));
  const pointHit=node=>{const box=node.getBoundingClientRect();const hit=typeof document.elementFromPoint==='function'?document.elementFromPoint(box.x+box.width/2,box.y+box.height/2):null;return {hit,inside:hit===node||Boolean(hit&&node.contains(hit))};};
  const snapshots=window.__storyartPopupIdentity??=new Map();
  if(stage==='capture'){
   const nodes=Array.from(document.body?.querySelectorAll('*')??[]);const visibility=new WeakMap(nodes.map(node=>[node,visible(node)]));
   const form=plus.closest('form');if(!form||!main.contains(form))return {ownedFormUnverified:true};
   const acceptedTurns=${turnRootsExpression??'[]'};
   const imageBefore=new WeakMap(imageLeaves(acceptedTurns).map(node=>{const box=node.getBoundingClientRect();return [node,{cssVisible:visible(node),rect:{x:box.x??0,y:box.y??0,width:box.width,height:box.height},hitInside:pointHit(node).inside}];}));
   snapshots.set(key,{nodes:new WeakSet(nodes),visibility,active:document.activeElement,plus,form,acceptedTurns,imageBefore});return {captured:true,nodeCount:nodes.length};
  }
  const saved=snapshots.get(key);if(!saved||saved.plus!==plus)return {identityChanged:true};
  const active=document.activeElement;const expanded=plus.getAttribute('aria-expanded');
  const describe=node=>({tag:String(node?.tagName??'').toLowerCase(),role:['button','menu','menuitem','option','listbox','dialog','textbox','combobox','searchbox','presentation','none','group','region','document','list','listitem','toolbar','navigation'].includes(node?.getAttribute?.('role'))?node.getAttribute('role'):null,type:['button','submit','reset'].includes(node?.type)?node.type:null,tabIndex:Number.isInteger(node?.tabIndex)?node.tabIndex:null,attributes:['role','aria-label','aria-controls','aria-expanded','aria-haspopup','data-testid','tabindex','id'].filter(name=>node?.hasAttribute?.(name))});
  const controls=plus.getAttribute('aria-controls');const controlled=controls?document.getElementById(controls):null;
  const activeAncestors=[];let ancestor=active;for(let depth=0;ancestor&&ancestor!==main&&ancestor!==document.body&&depth<6;depth++){activeAncestors.push({...describe(ancestor),existedBefore:saved.nodes.has(ancestor),rectVisible:rectVisible(ancestor),cssVisible:visible(ancestor),ariaHidden:ancestor.getAttribute('aria-hidden')==='true'});ancestor=ancestor.parentElement;}
  const scanLabels=surface=>{
    const known={'create image':'Create image','image':'Image','images':'Images','search':'Search','deep research':'Deep research','study and learn':'Study and learn','agent mode':'Agent mode','\\u0441\\u043e\\u0437\\u0434\\u0430\\u0442\\u044c \\u0438\\u0437\\u043e\\u0431\\u0440\\u0430\\u0436\\u0435\\u043d\\u0438\\u0435':'Create image','\\u0438\\u0437\\u043e\\u0431\\u0440\\u0430\\u0436\\u0435\\u043d\\u0438\\u0435':'Image'};
  const leafs=[surface,...Array.from(surface.querySelectorAll('*'))].filter(node=>visible(node)&&node.children.length===0);const labels=[];let otherCount=0;
  for(const leaf of leafs){const text=String(leaf.innerText||leaf.textContent||'').replace(/\\s+/g,' ').trim().toLowerCase();if(!text)continue;const label=known[text];if(!label){otherCount++;continue;}const ancestors=[];let node=leaf;for(let depth=0;node&&depth<4;depth++){ancestors.push(describe(node));if(node===surface)break;node=node.parentElement;}labels.push({label,ancestors});}
    return {knownLabels:labels.slice(0,20),otherLabelCount:otherCount};
  };
  const plusAriaHidden=(()=>{for(let node=plus;node&&node instanceof HTMLElement;node=node.parentElement)if(node.getAttribute('aria-hidden')==='true')return true;return false;})();
  const formCurrent=saved.form.isConnected===true&&main.contains(saved.form)&&plus.closest('form')===saved.form;
  const fresh=node=>!saved.nodes.has(node)||saved.visibility.get(node)!==true;
  const changed=Array.from(document.body?.querySelectorAll('*')??[]).filter(node=>visible(node)&&fresh(node));const changedSet=new Set(changed);
  const roots=changed.filter(node=>node!==main&&node!==document.body&&!changedSet.has(node.parentElement)&&(!node.parentElement||visible(node.parentElement)));
  const accepted=root=>saved.acceptedTurns.some(turn=>turn===root||turn.contains(root)||root.contains(turn));
  const ownedRoots=roots.filter(root=>formCurrent&&root!==saved.form&&saved.form.contains(root)&&!accepted(root));
  const rect=node=>{const box=node.getBoundingClientRect();return {x:Number(box.x??box.left??0),y:Number(box.y??box.top??0),width:Number(box.width),height:Number(box.height)};};const mainRect=rect(main),formRect=rect(saved.form);
  const relative=node=>{const box=rect(node);const nav=node.closest('aside,nav,[role="navigation"]');const navRect=nav?rect(nav):null;return {rect:box,relativeMain:{x:box.x-mainRect.x,y:box.y-mainRect.y},relativeForm:{x:box.x-formRect.x,y:box.y-formRect.y},relativeNavigation:navRect?{x:box.x-navRect.x,y:box.y-navRect.y}:null};};
  const changedRoots=roots.slice(0,8).map(root=>{const navigation=Boolean(root.closest('aside,nav,[role="navigation"]'));const ancestorChain=[];let node=root;for(let depth=0;node&&node!==document.body&&node!==main&&depth<6;depth++){ancestorChain.push({...describe(node),existedBefore:saved.nodes.has(node),cssVisibleBefore:saved.visibility.get(node)===true,cssVisibleNow:visible(node),...relative(node)});node=node.parentElement;}return {...describe(root),insideOwnedForm:formCurrent&&saved.form.contains(root),insideOwnMain:main.contains(root),containsActive:root.contains(active),newNode:!saved.nodes.has(root),newlyVisible:saved.visibility.get(root)!==true,acceptedTurn:accepted(root),navigationAncestor:navigation,exclusionReason:accepted(root)?'accepted-turn':main.contains(root)?'inside-main':saved.form.contains(root)?'inside-form':navigation?'navigation-ancestor':null,ancestorChain,...relative(root)};});
  const inputContextValid=formCurrent&&saved.form.contains(active);
  const inlinePalette=expanded==='true'&&inputContextValid&&ownedRoots.length===1?{bound:true,binding:'unique-new-owned-form-root',surface:describe(ownedRoots[0]),...scanLabels(ownedRoots[0])}:{bound:false,ownedRootCount:ownedRoots.length};
  const externalItems=changed.filter(node=>node.getAttribute('role')==='listitem'&&!main.contains(node)&&!saved.form.contains(node)&&!node.closest('aside,nav,[role="navigation"]')&&!accepted(node));
  let externalPalette={bound:false,itemCount:externalItems.length};
  if(expanded==='true'&&inputContextValid&&externalItems.length>=2){
   let common=externalItems[0].parentElement;for(let depth=0;common&&common!==document.body&&common!==main&&depth<8;depth++){if(externalItems.every(item=>common.contains(item)))break;common=common.parentElement;}
   const valid=common&&common!==document.body&&common!==main&&common!==saved.form&&!main.contains(common)&&!common.contains(main)&&!saved.form.contains(common)&&!common.contains(saved.form)&&!common.closest('aside,nav,[role="navigation"]')&&visible(common)&&!accepted(common);
   if(valid){
    const insideItem=node=>externalItems.some(item=>item===node||item.contains(node));
    const preexistingOutside=Array.from(common.querySelectorAll('*')).filter(node=>!insideItem(node)&&saved.nodes.has(node)&&saved.visibility.get(node)===true&&visible(node)).length;
    const oldDirectText=saved.nodes.has(common)&&saved.visibility.get(common)===true&&Array.from(common.childNodes??[]).some(node=>node.nodeType===3);
    externalPalette={bound:false,itemCount:externalItems.length,surface:describe(common),preexistingOutside,oldDirectText};
    if(preexistingOutside===0&&!oldDirectText)externalPalette={...externalPalette,bound:true,binding:'unique-new-external-listitem-group',...scanLabels(common)};
   }else externalPalette={...externalPalette,noUniqueExternalRoot:true};
  }
  const recordImageLeaf=leaf=>{const before=saved.imageBefore.get(leaf)??null;const hit=pointHit(leaf);const ancestors=[];let node=leaf;for(let depth=0;node&&node!==main&&node!==document.body&&depth<6;depth++){const style=window.getComputedStyle(node);const overflow=value=>['visible','hidden','clip','scroll','auto','overlay'].includes(value)?value:'OTHER';ancestors.push({...describe(node),rect:rect(node),cssVisibleBefore:saved.visibility.get(node)===true,cssVisibleNow:visible(node),overflowX:overflow(style.overflowX),overflowY:overflow(style.overflowY),containsHit:Boolean(hit.hit&&node.contains(hit.hit))});node=node.parentElement;}return {label:'Create image',leaf:describe(leaf),insideOwnedForm:saved.form.contains(leaf),existedBefore:saved.nodes.has(leaf),before,rect:rect(leaf),cssVisibleNow:visible(leaf),hitInsideLeaf:hit.inside,hit:describe(hit.hit),ancestors};};
  const imageMatches=imageLeaves(saved.acceptedTurns).slice(0,4).map(recordImageLeaf);
  const rendered=imageMatches.filter(match=>match.cssVisibleNow&&match.hitInsideLeaf);const imageRow={bound:expanded==='true'&&inputContextValid&&rendered.length===1,matchCount:imageMatches.length,renderedCount:rendered.length,matches:imageMatches};
  const portalRoots=roots.filter(root=>{if(root.parentElement!==document.body||saved.nodes.has(root)||root.closest('aside,nav,[role="navigation"]')||accepted(root))return false;const box=rect(root),gap=formRect.y-box.y-box.height;return box.x>=mainRect.x&&box.y>=mainRect.y&&box.x+box.width<=mainRect.x+mainRect.width&&box.y+box.height<=mainRect.y+mainRect.height&&Math.abs(box.x-formRect.x)<=2&&Math.abs(box.width-formRect.width)<=2&&gap>=-2&&gap<=12;});
  let nativePortal={bound:false,candidateCount:portalRoots.length};
  if(expanded==='true'&&inputContextValid&&portalRoots.length===1){const root=portalRoots[0];const allLeaves=imageLeaves(saved.acceptedTurns,root);const allMatches=allLeaves.map(recordImageLeaf);const visibleMatches=allMatches.filter(match=>match.cssVisibleNow&&match.hitInsideLeaf);nativePortal={bound:true,binding:'unique-new-body-portal-aligned-owned-composer',surface:describe(root),rect:rect(root),candidateCount:1,imageMatchCount:allMatches.length,renderedImageCount:visibleMatches.length,imageRowBound:visibleMatches.length===1,matches:allMatches.slice(0,4)};}
  const state={plusCSSVisible:visible(plus),plusAriaHidden,plusExpanded:['true','false'].includes(expanded)?expanded:null,activeChanged:active!==saved.active,active:describe(active),activeAncestors,controlsPresent:Boolean(controls),controlledExists:Boolean(controlled),formCurrent,inputContextValid,changedRoots,changedRootCount:roots.length,inlinePalette,externalPalette,imageRow,nativePortal};
  if(expanded!=='true'||active===saved.active||active===document.body||active===main)return {bound:false,...state};
  const controlCandidate=controlled&&controlled!==main&&controlled!==document.body&&visible(controlled)&&controlled.contains(active)&&fresh(controlled)?controlled:null;
  if(controls&&!controlCandidate)return {bound:false,controlsMismatch:true,...state};
  const chain=[];let current=active;for(let depth=0;current&&current!==main&&current!==document.body&&depth<16;depth++){
   if(visible(current)&&fresh(current))chain.push(current);else if(chain.length&&visible(current))break;current=current.parentElement;
  }
  const focusCandidate=chain.at(-1)??null;
  if(controlCandidate&&focusCandidate&&!controlCandidate.contains(focusCandidate)&&!focusCandidate.contains(controlCandidate))return {bound:false,ambiguous:true,...state};
  const surface=controlCandidate??focusCandidate;if(!surface||!surface.contains(active))return {bound:false,...state};
  return {bound:true,binding:controlCandidate?'aria-controls':'new-focus-ancestor',surface:describe(surface),newNode:!saved.nodes.has(surface),newlyVisible:saved.visibility.get(surface)!==true,...scanLabels(surface),...state};
 })()`;
}
export function buildMainScreenshotProbe(url,key){
 return `(() => {
  if(location.href!==${JSON.stringify(url)})return {allowed:false,reason:'url-changed'};
  const saved=window.__storyartPopupIdentity?.get(${JSON.stringify(key)});const mains=document.querySelectorAll('main');
  if(!saved||mains.length!==1)return {allowed:false,reason:'identity-unavailable'};const main=mains[0];
  if(!saved.plus.isConnected||saved.plus.closest('form')!==saved.form)return {allowed:false,reason:'owner-changed'};
  const box=main.getBoundingClientRect();const clip={x:box.x,y:box.y,width:box.width,height:box.height,scale:1};
  if(!Object.values(clip).every(Number.isFinite)||clip.x<0||clip.y<0||clip.width<=0||clip.height<=0||clip.x+clip.width>window.innerWidth||clip.y+clip.height>window.innerHeight)return {allowed:false,reason:'main-not-viewport-bounded'};
  const overlaps=Array.from(document.querySelectorAll('aside,nav,[role="navigation"]')).some(node=>{if(main.contains(node)||!saved.nodes.has(node)||saved.visibility.get(node)!==true)return false;const rect=node.getBoundingClientRect();return rect.width>0&&rect.height>0&&rect.x<clip.x+clip.width&&rect.x+rect.width>clip.x&&rect.y<clip.y+clip.height&&rect.y+rect.height>clip.y;});
  if(overlaps)return {allowed:false,reason:'main-overlaps-preexisting-navigation'};
  return {allowed:true,clip};
 })()`;
}
export function buildScreenshotProbe(url,key,native=false){
 let expression=buildMainScreenshotProbe(url,key);
 if(native)expression=expression.replace('__storyartPopupIdentity','__oracleNativeImageActions').replace("const main=mains[0];","const main=mains[0];if(saved.main!==main||!main.contains(saved.form))return {allowed:false,reason:'owner-changed'};").replace("main.contains(node)||!saved.nodes.has(node)||saved.visibility.get(node)!==true","main.contains(node)");
 return expression;
}
export async function captureOwnedMain(Runtime,Page,url,key,output,native=false){
 const expression=buildScreenshotProbe(url,key,native);
 const proof=(await Runtime.evaluate({expression,returnByValue:true})).result?.value;
 if(proof?.allowed!==true)return {captured:false,reason:proof?.reason??'geometry-unverified'};
 const image=await Page.captureScreenshot({format:'png',clip:proof.clip,captureBeyondViewport:false,fromSurface:true});
 const actual=(await Runtime.evaluate({expression:'location.href',returnByValue:true})).result?.value;
 if(actual!==url)return {captured:false,reason:'url-changed-during-capture'};
 await fs.writeFile(output,Buffer.from(image.data,'base64'),{flag:'wx'});
 return {captured:true,path:output,clip:proof.clip};
}
export async function inspectImageToolMenu(Runtime,Input,url,actions){
 if(typeof Input?.dispatchKeyEvent!=='function')throw new Error('Trusted menu input unavailable; no keys sent');
 const before=(await Runtime.evaluate({expression:buildImageMenuProbe(url,'before',actions.plusResolver?.()),returnByValue:true})).result?.value;
 if(before?.mismatch||before?.ambiguous||before?.plusReady!==true||before?.focused!==true){const error=new Error('Owned composer plus focus/menu baseline not verified; no keys sent');error.menuBaseline=before;throw error;}
 const popupKey=randomUUID();const popupExpression=stage=>buildPopupIdentityProbe(url,popupKey,stage,actions.plusResolver?.(),actions.turnRootsExpression?.());
 const captured=(await Runtime.evaluate({expression:popupExpression('capture'),returnByValue:true})).result?.value;
 if(captured?.captured!==true)throw new Error('Popup identity snapshot unavailable; no activation');
 let activated;try{activated=await actions.plus(Runtime,Input,url);}catch(error){await Runtime.evaluate({expression:popupExpression('cleanup'),returnByValue:true}).catch(()=>undefined);throw error;}
 if(activated?.method!=='trusted-keyboard'||activated.startUrl!==url){await Runtime.evaluate({expression:popupExpression('cleanup'),returnByValue:true}).catch(()=>undefined);throw new Error('Canonical composer menu activation not verified');}
 let opened;const report={activation:'trusted-keyboard',delivery:activated.delivery,before,menuOwned:false,selectionOperations:0,menuClosed:false};try{
  for(let attempt=0;attempt<20;attempt++){
   await actions.plusStay(Runtime,url);
   opened=(await Runtime.evaluate({expression:buildImageMenuProbe(url,'after',actions.plusResolver?.()),returnByValue:true})).result?.value;
   if(attempt===0&&actions.captureMain)report.mainScreenshot=await actions.captureMain(Runtime,url,popupKey);
   report.popup=(await Runtime.evaluate({expression:popupExpression('inspect'),returnByValue:true})).result?.value;report.post=opened;if(opened?.owned===true)break;if(opened?.mismatch||opened?.menuCount>1||opened?.owned===false)throw new Error('Owned image-tools menu context unverified');
   await actions.delay(100);
  }
  if(opened?.owned!==true)throw new Error('Owned image-tools menu unavailable');
  Object.assign(report,{entries:opened.entries,menuOwned:true});return report;
 }catch(error){error.menuInspection=report;throw error;}finally{
  await Runtime.evaluate({expression:popupExpression('cleanup'),returnByValue:true}).catch(()=>undefined);
  if(opened?.owned===true){
   const current=(await Runtime.evaluate({expression:buildImageMenuProbe(url,'after',actions.plusResolver?.()),returnByValue:true})).result?.value;
   if(current?.owned!==true)throw new Error('Owned menu changed; Escape refused');
   await Input.dispatchKeyEvent({type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27,nativeVirtualKeyCode:27});
   await Input.dispatchKeyEvent({type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27,nativeVirtualKeyCode:27});
   await actions.delay(100);
   const closed=(await Runtime.evaluate({expression:buildImageMenuProbe(url,'after',actions.plusResolver?.()),returnByValue:true})).result?.value;
   if(closed?.mismatch||closed?.menuCount!==0)throw new Error('Owned menu closure not verified');report.menuClosed=true;
  }
 }
}
export async function recoverOriginal(request,meta,actions){
 const url=validateRecovery(request,meta);let lease,acquired,connection,ownChrome=false;const cleanup={};let result,parserEvidence;
 const log=message=>process.stderr.write(String(message)+'\n');
 try{
  lease=await actions.acquireLease(request.profile_dir,{maxConcurrentTabs:1,timeoutMs:1000,logger:log,sessionId:request.session_id});
  const cfg=actions.config(request.oracle_config.browser);
  acquired=await actions.acquireChrome(request.profile_dir,cfg,log,request.session_id);
  if(acquired.reusedChrome)throw new Error('Shared Chrome reuse refused and preserved');
  ownChrome=Number.isInteger(acquired.chrome?.pid)&&acquired.chrome.pid>0&&typeof acquired.chrome.kill==='function';
  if(!ownChrome)throw new Error('Owned Chrome handle unavailable');
  const host=acquired.chrome.host??'127.0.0.1';if(host!=='127.0.0.1')throw new Error('Non-loopback Chrome refused');
  await lease.update({chromeHost:host,chromePort:acquired.chrome.port});
  connection=await actions.connect(acquired.chrome.port,log,'about:blank',host,{fallbackToDefault:false,retries:0});
  if(!connection.targetId)throw new Error('Owned recovery target unavailable');
  await lease.update({chromeTargetId:connection.targetId});
  const {Page,Runtime,Browser}=connection.client;await Promise.all([Page.enable(),Runtime.enable()]);
  await actions.navigate(Page,Runtime,url,log);
  if(request.operation==='image-tool-inspect'){
   await actions.delay(5000);await actions.guard(Runtime,false,log);
   const actual=(await Runtime.evaluate({expression:'location.href',returnByValue:true})).result?.value;
   if(actual!==url)throw new Error('Original conversation changed before parser evidence');
   if(request.pinned_project_editor!==true){
   const observed=await actions.harvest({host,port:acquired.chrome.port,target:{targetId:connection.targetId,url,type:'page'}});
   if(observed.url!==url||observed.conversationId!==meta.browser.runtime.conversationId)throw new Error('Original parser evidence identity mismatch');
   parserEvidence={assistantCount:observed.assistantCount,lastUserTurnIndex:observed.lastUserTurnIndex,lastAssistantTurnIndex:observed.lastAssistantTurnIndex,turnLayout:observed.turnLayout,fullPromptMatches:typeof observed.lastUserContentText==='string'&&normalize(observed.lastUserContentText)===normalize(request.prompt),observedContentHash:observed.lastUserContentHashSHA256??null,hashProvenance:observed.lastUserContentHashProvenance??null};
   const model=actions.modelCurrent?await actions.modelCurrent(Runtime):null;
   const modelKnown=['Latest','GPT-6','GPT-5.6 Sol','GPT-5.5','GPT-5.5 Instant','Thinking 5.5','Thinking 5.4','Thinking effortInstant','Thinking effortPro','Thinking effortMedium'];
   parserEvidence.model={label:modelKnown.includes(model?.resolvedLabel)?model.resolvedLabel:'OTHER',strategy:'current',verified:model?.verified===true,status:['already-selected','switched','button-missing'].includes(model?.status)?model.status:'OTHER'};
   }else parserEvidence={pinnedProjectEditor:true,promptSubmitted:false};
   const diagnosticName=request.diagnostic_receipt;if(!/^image-tool-inspect-[a-f0-9]{32}\.json$/.test(diagnosticName??''))throw new Error('Diagnostic screenshot destination unverified');
   const diagnosticActions={...actions,captureMain:(runtime,currentUrl,key)=>captureOwnedMain(runtime,Page,currentUrl,key,path.join(request.oracle_home,diagnosticName.replace(/\.json$/,'-main.png')))};
   const beforeToken=request.pinned_project_editor===true?(await Runtime.evaluate({expression:buildNativeTokenProbe(url,actions.inputSelectors),returnByValue:true})).result?.value:null;
   let menu;
   if(request.native_mode_proof_only===true){
    menu={modeReady:false,selectionOperations:0,selectionInputAttempts:0,nativeToken:beforeToken};try{const proof=await actions.nativeModeProof(Runtime,{url,projectId:request.project_binding.route.project_id,actions:diagnosticActions});menu.productionModeProof={ok:proof?.ok===true,atomicNativeImageToken:proof?.atomicNativeImageToken===true,exactEmpty:proof?.exactPrompt===true&&proof?.promptLength===0};}catch{menu.productionModeProof={ok:false,reason:'native-mode-proof-refused'};}
   }else if(request.activate_native_image_tool===true){
    try{menu=await actions.nativeImageTool(Runtime,connection.client.Input,url,{...diagnosticActions,captureProof:(runtime,currentUrl,key)=>captureOwnedMain(runtime,Page,currentUrl,key,path.join(request.oracle_home,diagnosticName.replace(/\.json$/,'-main.png')),true)});menu.failedNativeDiagnostic=Boolean(menu.failure);if(request.pinned_project_editor===true&&menu.failure===null){try{const proof=await actions.nativeModeProof(Runtime,{url,projectId:request.project_binding.route.project_id,actions:diagnosticActions});menu.productionModeProof={ok:proof?.ok===true,atomicNativeImageToken:proof?.atomicNativeImageToken===true,exactEmpty:proof?.exactPrompt===true&&proof?.promptLength===0};}catch{menu.productionModeProof={ok:false,reason:'native-mode-proof-refused'};}}const token=await Runtime.evaluate({expression:buildNativeTokenProbe(url,actions.inputSelectors),returnByValue:true});menu.nativeToken=token.result?.value??{unavailable:true};if(request.pinned_project_editor===true){menu.beforeToken=beforeToken;menu.tokenSamples=[{atMs:0,value:menu.nativeToken}];for(const [wait,atMs] of [[100,100],[400,500],[500,1000]]){await actions.delay(wait);const sample=await Runtime.evaluate({expression:buildNativeTokenProbe(url,actions.inputSelectors),returnByValue:true});menu.tokenSamples.push({atMs,value:sample.result?.value??{unavailable:true}});}}}
    catch(error){menu=nativeFailureDiagnostic(error,path.join(request.oracle_home,diagnosticName.replace(/\.json$/,'-main.png')));}
   }else menu=await inspectImageToolMenu(Runtime,connection.client.Input,url,diagnosticActions);
   result={sessionId:request.session_id,status:'image-tool-diagnostic',conversationId:meta.browser.runtime.conversationId??null,menu,imageCollectOperations:0};
  }else if(request.operation==='recovery-status'){
   const snapshots=[];
   for(let index=0;index<3;index++){
    const current=await Runtime.evaluate({expression:'location.href',returnByValue:true});
    if(current?.result?.value!==url)throw new Error('Original conversation redirected; diagnostic refused');
    await actions.guard(Runtime,false,log);
    const structure=await Runtime.evaluate({expression:`(() => {if(location.href!==${JSON.stringify(url)})return {url:location.href,mismatch:true};return ({url:location.href,readyState:document.readyState,visibilityState:document.visibilityState,hasFocus:document.hasFocus(),innerWidth:window.innerWidth,innerHeight:window.innerHeight,outerWidth:window.outerWidth,outerHeight:window.outerHeight,viewportWidth:window.visualViewport?.width??null,viewportHeight:window.visualViewport?.height??null,bodyPresent:Boolean(document.body),bodyTextLength:String(document.body?.innerText??'').length,bodyElements:document.body?.querySelectorAll('*').length??0,mainCount:document.querySelectorAll('main').length,articleCount:document.querySelectorAll('article').length,editableCount:document.querySelectorAll('[contenteditable="true"]').length,textareaCount:document.querySelectorAll('textarea').length,userRoles:document.querySelectorAll('[data-message-author-role="user"]').length,assistantRoles:document.querySelectorAll('[data-message-author-role="assistant"]').length,dataTurnUsers:document.querySelectorAll('[data-turn="user"]').length,dataTurnAssistants:document.querySelectorAll('[data-turn="assistant"]').length,conversationTurns:document.querySelectorAll('[data-testid^="conversation-turn-"]').length});})()`,returnByValue:true});
    if(structure?.result?.value?.url!==url)throw new Error('Original conversation redirected during observation');
    const observed=await actions.harvest({host,port:acquired.chrome.port,target:{targetId:connection.targetId,url,type:'page'}});
    if(observed.url!==url||observed.conversationId!==meta.browser.runtime.conversationId)throw new Error('Original conversation harvest identity mismatch');
    const mainObservation=await Runtime.evaluate({expression:`(() => {
      if(location.href!==${JSON.stringify(url)})return {url:location.href,mismatch:true};
      const mains=document.querySelectorAll('main');if(mains.length!==1)return {url:location.href,mainCount:mains.length,ambiguous:true};
      const main=mains[0];const text=String(main.innerText??'');const norm=value=>String(value).replace(/\\r\\n?/g,'\\n').trim();
      const counts=(attribute)=>Object.fromEntries(Array.from(main.querySelectorAll('['+attribute+']')).reduce((entries,node)=>{const value=String(node.getAttribute(attribute)??'');const allowed=attribute==='role'?['button','textbox','article','status','alert','dialog','img','region','list','listitem','heading']:['user','assistant'];if(allowed.includes(value))entries.set(value,(entries.get(value)??0)+1);return entries;},new Map()));
      const testIdCategories=Object.fromEntries(Array.from(main.querySelectorAll('[data-testid]')).reduce((entries,node)=>{const id=String(node.getAttribute('data-testid')??'');const category=['conversation-turn','assistant','user','message','composer','send','download','error','loading','status'].find(label=>id.toLowerCase().includes(label));if(category)entries.set(category,(entries.get(category)??0)+1);return entries;},new Map()));
      const actionLabels=Array.from(new Set(Array.from(main.querySelectorAll('button,[role="button"]'),node=>String(node.getAttribute('aria-label')??'').toLowerCase()).map(label=>['copy','edit','download','regenerate','send'].find(action=>new RegExp('(?:^|\\\\s)'+action+'(?:$|\\\\s)').test(label))).filter(Boolean)));
      const structuralNames=['role','data-message-author-role','data-turn','data-testid','aria-label','aria-labelledby','aria-describedby','data-message-id','id'];
      const describe=node=>({tag:String(node.tagName??'').toLowerCase(),role:['button','textbox','article','status','alert','dialog','img','region','list','listitem','heading'].includes(node.getAttribute?.('role'))?node.getAttribute('role'):null,attributes:structuralNames.filter(name=>node.hasAttribute?.(name))});
      const topology=node=>{const ancestors=[];let current=node;for(let depth=0;current&&depth<5;depth++){ancestors.push(describe(current));if(current===main)break;current=current.parentElement;}const parent=node.parentElement;const siblings=parent&&main.contains(parent)?Object.fromEntries(Array.from(parent.children??[]).reduce((counts,sibling)=>{const tag=String(sibling.tagName??'').toLowerCase();counts.set(tag,(counts.get(tag)??0)+1);return counts;},new Map())):{};return {ancestors,siblingTags:siblings};};
      const descendants=Array.from(main.querySelectorAll('*'));const promptNodes=descendants.filter(node=>norm(node.textContent??'')===norm(${JSON.stringify(request.prompt)})&&!Array.from(node.children??[]).some(child=>norm(child.textContent??'')===norm(${JSON.stringify(request.prompt)})));
      const headings=descendants.filter(node=>['You said:','ChatGPT said:'].includes(String(node.textContent??'').trim())&&!Array.from(node.children??[]).some(child=>String(child.textContent??'').trim()===String(node.textContent??'').trim()));
      const messageTopology={exactPromptLeaves:promptNodes.slice(0,3).map(topology),knownHeadings:headings.slice(0,4).map(node=>({label:String(node.textContent).trim(),...topology(node)}))};
      const tags=Object.fromEntries(['article','section','p','pre','img','textarea','button'].map(tag=>[tag,main.querySelectorAll(tag).length]));
      return {url:location.href,mainCount:1,text:text.slice(0,4096),textLength:text.length,truncated:text.length>4096,fullPromptPresent:norm(text).includes(norm(${JSON.stringify(request.prompt)})),roles:counts('role'),messageRoles:counts('data-message-author-role'),testIdCategories,tags,actionLabels,messageTopology};
    })()`,returnByValue:true});
    if(mainObservation?.result?.value?.mismatch)throw new Error('Original conversation redirected during main observation');
    const canonical=Object.fromEntries(['state','authenticated','loginButtonExists','promptReady','sendExists','stopExists','assistantCount','lastUserTurnIndex','lastAssistantTurnIndex','assistantFollowsLatestUser'].map(key=>[key,observed[key]]));
    const nativeUser=(await Runtime.evaluate({expression:buildNativeUserRenderProbe(url,actions.turnRootsExpression(),actions.turnSemantics()),returnByValue:true})).result?.value;
    snapshots.push({strictPromptBinding:observeStrictPromptBinding(request,meta,observed),nativeUser,index,at:new Date().toISOString(),structure:structure.result.value,canonical,main:mainObservation?.result?.value});
    if(index<2)await actions.delay(index===0?5000:10000);
   }
   result={sessionId:request.session_id,status:'diagnostic',conversationId:meta.browser.runtime.conversationId,snapshots,imageCollectOperations:0};
  }else{
  const deadline=Date.now()+35000;let observed,binding;
  do{
   await actions.guard(Runtime,false,log);await actions.auth(Runtime,log,{remoteSession:true});
   observed=await actions.harvest({host,port:acquired.chrome.port,target:{targetId:connection.targetId,url,type:'page'}});
   if(observed.lastUserContentText!==undefined){binding=matchObservedPrompt(request,meta,observed);if(!observed.stopExists&&observed.assistantFollowsLatestUser===true)break;}
   await actions.delay(1000);
  }while(Date.now()<deadline);
  if(!binding||observed.stopExists||observed.assistantFollowsLatestUser!==true)throw new Error('Original prompt/assistant not ready; no second send');
  const targetPath=meta.options.generateImage;try{await fs.access(targetPath);throw new Error('Existing recovery output cannot be replaced');}catch(error){if(error.code!=='ENOENT')throw error;}
   const claimId=randomUUID();await actions.claimTarget(Runtime,claimId);
   const artifact=await actions.collect({Browser,Client:connection.client,Page,Runtime,generateImagePath:targetPath,minTurnIndex:observed.lastUserTurnIndex,answerText:observed.lastAssistantText,expectedPrompt:request.prompt,nativeRuntime:{tabUrl:url,ownedRecoveryTarget:{claimId}},checkBlockingUiWarning:()=>actions.guard(Runtime,false,log),logger:log});
  if(artifact.savedImages?.length!==1||path.resolve(artifact.savedImages[0].path)!==path.resolve(targetPath))throw new Error('Exactly one original PNG download required');
  result={sessionId:request.session_id,status:'completed',images:artifact.savedImages,recoveryEvidence:binding};
  }
 }catch(error){
  if(request.operation==='image-tool-inspect'&&(error.menuBaseline||error.menuInspection)){result={sessionId:request.session_id,status:'image-tool-diagnostic',conversationId:meta.browser.runtime.conversationId??null,menu:error.menuInspection?{...error.menuInspection,error:String(error.message)}:{baselineFailed:true,before:error.menuBaseline,error:String(error.message),selectionOperations:0,keyOperations:0,activation:'not-attempted'},imageCollectOperations:0};}
  else{
  if(request.operation==='recover'&&typeof connection?.client?.Runtime?.evaluate==='function'){
   const diagnostic=await connection.client.Runtime.evaluate({expression:`(() => {if(location.href!==${JSON.stringify(url)})return {url:location.href,mismatch:true};return ({userRoles:document.querySelectorAll('[data-message-author-role="user"]').length,assistantRoles:document.querySelectorAll('[data-message-author-role="assistant"]').length,dataTurnUsers:document.querySelectorAll('[data-turn="user"]').length,dataTurnAssistants:document.querySelectorAll('[data-turn="assistant"]').length,conversationTurns:document.querySelectorAll('[data-testid^="conversation-turn-"]').length,testIdCategories:Array.from(new Set(Array.from(document.querySelectorAll('[data-testid]'),node=>String(node.getAttribute('data-testid')).replace(/[0-9a-f-]{8,}/gi,'ID').replace(/\d+/g,'N')))).slice(0,80)});})()`,returnByValue:true}).catch(()=>null);
   if(diagnostic?.result?.value&&!diagnostic.result.value.mismatch)await fs.writeFile(path.join(request.oracle_home,'recovery-dom-diagnostic.json'),JSON.stringify({sessionId:request.session_id,conversationId:meta.browser.runtime.conversationId,error:String(error.message),structure:diagnostic.result.value}),{flag:'wx'}).catch(()=>undefined);
  }
  throw error;
  }
 }finally{
  if(connection?.client){try{await connection.client.close();cleanup.client='closed';}catch{cleanup.client='close-failed';}}
  if(lease){try{await lease.release({onRelease:async({isLastLease})=>{
   cleanup.finalLease=isLastLease;if(connection?.targetId){try{await actions.closeTarget(acquired.chrome.port,connection.targetId,log,acquired.chrome.host??'127.0.0.1');cleanup.target='closed';}catch{cleanup.target='close-failed';}}
   if(ownChrome&&isLastLease){await acquired.chrome.kill();cleanup.chrome='owned-terminated';}else cleanup.chrome=ownChrome?'preserved-other-leases':'foreign-preserved';
  }});cleanup.lease='released';}catch{cleanup.lease='release-failed';cleanup.chrome='preserved-unverified-finality';}}
 }
 if(Object.values(cleanup).some(value=>typeof value==='string'&&value.endsWith('failed')))throw new Error('Recovery cleanup failed; preserved original state');
 return {...result,parserEvidence,cleanup,sendOperations:0};
}
async function main(){
 if(process.platform!=='win32'||process.env.ORACLE_IMAGE_BROWSER_DOWNLOAD_ONLY!=='1')throw new Error('Windows UI-only broker required');
 const request=JSON.parse(await fs.readFile(process.argv[2],'utf8'));
 let receipt;for(let i=0;i<40;i++){try{receipt=JSON.parse(await fs.readFile(process.argv[3],'utf8'));break;}catch(error){if(error.code!=='ENOENT')throw error;await new Promise(resolve=>setTimeout(resolve,50));}}
 if(!validateDesktopReceipt(receipt,request,process.pid))throw new Error('Unverified isolated desktop');
 const metaPath=path.join(request.oracle_home,'sessions',request.session_id,'meta.json');const raw=await fs.readFile(metaPath);if(hash(raw)!==request.recovery_meta_sha256)throw new Error('Original metadata changed');const meta=JSON.parse(raw);
  const base=path.join(request.oracle_root,'node_modules/@steipete/oracle/dist/src/browser');const load=name=>import(pathToFileURL(path.join(base,name)).href);
  const collectorPins=await resolveRecoveryCollectorPins(request,base);for(const [name,pin] of Object.entries(collectorPins))if(hash(await fs.readFile(path.join(base,name)))!==pin)throw new Error('Reviewed UI-only collector changed');
 configureNativeUserRecovery(request,meta,process.env);
  const [browser,leases,lifecycle,nav,tabs,images,config,attachments,turns,model,attachmentContext,constants,targetClaims]=await Promise.all(['index.js','tabLeaseRegistry.js','chromeLifecycle.js','actions/navigation.js','liveTabs.js','chatgptImages.js','config.js','actions/attachments.js','conversationTurns.js','actions/modelSelection.js','actions/attachmentContext.js','constants.js','targetClaim.js'].map(load));
 let nativeImageTool,nativeModeProof;
 if(request.activate_native_image_tool===true||request.native_mode_proof_only===true){
  if(request.operation!=='image-tool-inspect')throw new Error('Native activation is diagnostic-only');
  const candidate=path.join(request.oracle_root,'node_modules/@steipete/oracle/dist/src/browser/actions/native-image-production.mjs');
  if(!path.isAbsolute(candidate))throw new Error('Installed native action path is invalid');
  await fs.access(candidate);
  const nativeModule=await import(pathToFileURL(candidate).href);nativeImageTool=nativeModule.activateNativeImageToolPointer;nativeModeProof=nativeModule.assertNativeImageMode;
  if(typeof nativeImageTool!=='function')throw new Error('Native action API unavailable');
 }
  const result=await recoverOriginal(request,meta,{nativeImageTool,nativeModeProof,inputSelectors:constants.INPUT_SELECTORS,navigationValidation:attachmentContext.buildComposerNavigationValidationExpression,acquireChrome:browser.acquireManualLoginChromeForRun,acquireLease:leases.acquireBrowserTabLease,connect:lifecycle.connectWithNewTab,closeTarget:lifecycle.closeTab,navigate:nav.navigateToChatGPT,guard:nav.ensureNotBlocked,auth:nav.ensureLoggedIn,harvest:tabs.harvestChatGptTab,collect:images.collectGeneratedImageArtifacts,claimTarget:targetClaims.claimBrowserTarget,config:config.resolveBrowserConfig,plus:attachments.activateComposerPlus,plusResolver:attachments.buildComposerPlusResolverExpression,turnSemantics:turns.buildConversationTurnSemantics,turnRootsExpression:()=>turns.buildConversationTurnListExpression("document.querySelector('main')"),plusStay:attachments.assertComposerPlusStayedInPlace,modelCurrent:runtime=>model.ensureModelSelection(runtime,'Latest',()=>{},'current',{buttonWaitMs:0}),delay:ms=>new Promise(resolve=>setTimeout(resolve,ms))});
 process.stdout.write(JSON.stringify({type:'result',result})+'\n');
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))await main();
