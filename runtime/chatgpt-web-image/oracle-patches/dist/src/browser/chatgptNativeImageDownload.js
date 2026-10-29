import fs from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";

const sha256 = bytes => createHash("sha256").update(bytes).digest("hex");
const owned = runtime => `(()=>{if(location.href!==${JSON.stringify(runtime.tabUrl)})return false;try{const c=JSON.parse(sessionStorage.getItem('oracle:target-claim')||'null');return c?.id===${JSON.stringify(runtime.ownedRecoveryTarget?.claimId)}&&c.retiring!==true}catch{return false}})()`;
const visible = `n=>{const r=n.getBoundingClientRect(),s=getComputedStyle(n);return r.width>0&&r.height>0&&s.display!=='none'&&!['hidden','collapse'].includes(s.visibility)&&Number(s.opacity)>0}`;

export function buildNativePreviewOpen(runtime, prompt, semanticsOrMinTurnIndex = 0, suppliedRoots, explicitMinTurnIndex) {
  const minTurnIndex = typeof semanticsOrMinTurnIndex === "number" ? semanticsOrMinTurnIndex : (explicitMinTurnIndex ?? 0);
  const semantics = typeof semanticsOrMinTurnIndex === "string" ? semanticsOrMinTurnIndex : "";
  const roots = suppliedRoots ?? "document.querySelectorAll('[data-testid^=\\\"conversation-turn\\\"]')";
  return `(()=>{${semantics}const vis=${visible};if(!(${owned(runtime)}))return {ok:false,reason:'target'};const ms=document.querySelectorAll('main');if(ms.length!==1)return {ok:false,reason:'main'};const ts=${roots};const norm=s=>String(s??'').replace(/\\s+/g,' ').trim();const hits=[];for(let i=${Math.max(0, Math.floor(minTurnIndex))};i<ts.length;i++)if(oracleReadTurnRole(ts[i])==='user'&&norm(oracleReadTurnText(ts[i]))===norm(${JSON.stringify(prompt)}))hits.push(i);if(hits.length!==1)return {ok:false,reason:'prompt-ambiguous'};const i=hits[0];if(!ts[i+1]||oracleReadTurnRole(ts[i+1])!=='assistant')return {ok:false,reason:'assistant'};const root=oracleReadTurnContentRoot(ts[i+1]);const bs=root?Array.from(root.querySelectorAll('button[data-testid="generated-image-preview"]')).filter(vis):[];if(bs.length!==1)return {ok:false,reason:'preview-count'};const b=bs[0],imgs=b.querySelectorAll('img');if(b.disabled||imgs.length!==1||!imgs[0].complete||imgs[0].naturalWidth<1||imgs[0].naturalHeight<1||!String(imgs[0].currentSrc||imgs[0].getAttribute('src')||'').startsWith('blob:'))return {ok:false,reason:'preview'};if(!(${owned(runtime)}))return {ok:false,reason:'target-changed'};b.click();return {ok:true,opened:true,width:imgs[0].naturalWidth,height:imgs[0].naturalHeight,src:imgs[0].currentSrc||imgs[0].getAttribute('src')}})()`;
}

export function buildNativeViewerProbe(runtime, prompt, semanticsOrMinTurnIndex = 0, suppliedRoots, explicitMinTurnIndex) {
  const minTurnIndex = typeof semanticsOrMinTurnIndex === "number" ? semanticsOrMinTurnIndex : (explicitMinTurnIndex ?? 0);
  const semantics = typeof semanticsOrMinTurnIndex === "string" ? semanticsOrMinTurnIndex : "";
  const roots = suppliedRoots ?? "document.querySelectorAll('[data-testid^=\\\"conversation-turn\\\"]')";
  return `(()=>{${semantics}const vis=${visible};if(!(${owned(runtime)}))return {ok:false,reason:'target'};const ts=${roots};const norm=s=>String(s??'').replace(/\\s+/g,' ').trim();const hits=[];for(let i=${Math.max(0, Math.floor(minTurnIndex))};i<ts.length;i++)if(oracleReadTurnRole(ts[i])==='user'&&norm(oracleReadTurnText(ts[i]))===norm(${JSON.stringify(prompt)}))hits.push(i);if(hits.length!==1)return {ok:false,reason:'prompt-ambiguous'};const i=hits[0],root=ts[i+1]&&oracleReadTurnRole(ts[i+1])==='assistant'?oracleReadTurnContentRoot(ts[i+1]):null;const previews=root?Array.from(root.querySelectorAll('button[data-testid="generated-image-preview"]')).filter(vis):[];const dialogs=Array.from(document.querySelectorAll('[role="dialog"]')).filter(vis);const imgState=n=>({complete:n.complete===true,width:n.naturalWidth,height:n.naturalHeight,src:String(n.currentSrc||n.getAttribute('src')||''),source:String(n.currentSrc||n.getAttribute('src')||'').startsWith('blob:')?'blob':'other'});const ds=dialogs.map(d=>({images:Array.from(d.querySelectorAll('img')).filter(vis).map(imgState),controls:Array.from(d.querySelectorAll('button,[role="button"],a[download]')).filter(vis).map(n=>({labels:[n.getAttribute('aria-label'),n.getAttribute('title'),n.textContent].map(x=>String(x||'').trim()),disabled:n.disabled===true||n.getAttribute('aria-disabled')==='true'}))}));return {ok:true,allowed:true,previewCount:previews.length,preview:previews.length===1?imgState(previews[0].querySelector('img')):null,previews:previews.map(b=>imgState(b.querySelector('img'))),dialogCount:dialogs.length,dialogs:ds}})()`;
}

export function buildNativeDownloadClick(runtime, prompt, semantics, roots, minTurnIndex = 0) {
  return `(()=>{${semantics}const vis=${visible};if(!(${owned(runtime)}))return {clicked:false,reason:'target'};const ts=${roots};const norm=s=>String(s??'').replace(/\\s+/g,' ').trim();const hits=[];for(let i=${Math.max(0,Math.floor(minTurnIndex))};i<ts.length;i++)if(oracleReadTurnRole(ts[i])==='user'&&norm(oracleReadTurnText(ts[i]))===norm(${JSON.stringify(prompt)}))hits.push(i);if(hits.length!==1)return {clicked:false,reason:'prompt-ambiguous'};const i=hits[0],root=ts[i+1]&&oracleReadTurnRole(ts[i+1])==='assistant'?oracleReadTurnContentRoot(ts[i+1]):null;const previews=root?Array.from(root.querySelectorAll('button[data-testid="generated-image-preview"] img')):[];const ds=Array.from(document.querySelectorAll('[role="dialog"]')).filter(vis);if(previews.length!==1||ds.length!==1)return {clicked:false,reason:'image-viewer-ambiguous'};const preview=previews[0],imgs=Array.from(ds[0].querySelectorAll('img')).filter(vis);if(imgs.length!==1||!imgs[0].complete||imgs[0].naturalWidth!==preview.naturalWidth||imgs[0].naturalHeight!==preview.naturalHeight||imgs[0].currentSrc!==preview.currentSrc||!String(imgs[0].currentSrc).startsWith('blob:'))return {clicked:false,reason:'viewer-source-mismatch'};const cs=Array.from(ds[0].querySelectorAll('button,[role="button"],a[download]')).filter(vis).filter(n=>[n.getAttribute('aria-label'),n.getAttribute('title'),n.textContent].some(x=>/^(download|download image|save image)$/i.test(String(x||'').trim())));if(cs.length!==1||cs[0].disabled||cs[0].getAttribute('aria-disabled')==='true'||!(${owned(runtime)}))return {clicked:false,reason:'download-control'};cs[0].click();return {clicked:true,sourceMatchesPreview:true,width:imgs[0].naturalWidth,height:imgs[0].naturalHeight}})()`;
}

export function validateOriginalPng(bytes, expected) {
  const sig = Buffer.from([137,80,78,71,13,10,26,10]);
  if(bytes.length < 57 || !bytes.subarray(0,8).equals(sig)) throw Error("Native download is not a complete PNG");
  let off=8, width=0, height=0, ended=false, sawData=false;
  const crc32=data=>{let c=0xffffffff;for(const b of data){c^=b;for(let k=0;k<8;k++)c=(c>>>1)^((c&1)?0xedb88320:0);}return (c^0xffffffff)>>>0;};
  while(off+12<=bytes.length){const n=bytes.readUInt32BE(off);if(n>bytes.length-off-12)throw Error("Truncated PNG chunk");const type=bytes.toString('ascii',off+4,off+8);if(crc32(bytes.subarray(off+4,off+8+n))!==bytes.readUInt32BE(off+8+n))throw Error('PNG chunk checksum failed');if(type==='IHDR'){if(off!==8||n!==13)throw Error('Invalid PNG IHDR');width=bytes.readUInt32BE(off+8);height=bytes.readUInt32BE(off+12);}if(type==='IDAT')sawData=true;if(type==='IEND'){if(n!==0||off+12!==bytes.length)throw Error('Invalid PNG end');ended=true;break;}off+=n+12;}
  if(!ended||!sawData||width<1||height<1||width!==expected.width||height!==expected.height)throw Error("Native PNG dimensions or structure do not match preview");
  return {width,height,sizeBytes:bytes.length,sha256:sha256(bytes)};
}

export async function collectNativeImageArtifact({Runtime,Client,Browser,Page,prompt,minTurnIndex,targetPath,runtime,logger,downloadTimeoutMs}) {
  if(!Runtime||!Client||!Browser||!Page||!runtime?.tabUrl||!runtime?.ownedRecoveryTarget?.claimId||!prompt)throw Error('Native image runtime binding incomplete');
  const absolute=path.resolve(targetPath);try{await fs.access(absolute);throw Error('Requested image target already exists');}catch(e){if(e.code!=='ENOENT')throw e;}
  const evaluate=async expression=>(await Runtime.evaluate({expression,returnByValue:true})).result?.value;
  const turnTools=await import("./conversationTurns.js"), semantics=turnTools.buildConversationTurnSemantics(), roots=turnTools.buildConversationTurnListExpression("document.querySelector('main')");
  const opened=await evaluate(buildNativePreviewOpen(runtime,prompt,semantics,roots,minTurnIndex));if(!opened?.ok)throw Error(`Native preview refused: ${opened?.reason||'unknown'}`);
  let viewer;const deadline=Date.now()+10000;
  do{viewer=await evaluate(buildNativeViewerProbe(runtime,prompt,semantics,roots,minTurnIndex));if(viewer?.ok&&viewer.previewCount===1&&viewer.dialogs?.length===1)break;await new Promise(r=>setTimeout(r,100));}while(Date.now()<deadline);
  const dialog=viewer?.dialogs?.[0], image=dialog?.images?.length===1?dialog.images[0]:null, preview=viewer?.preview;
  if(!viewer?.ok||viewer.previewCount!==1||viewer.dialogs?.length!==1||!image?.complete||image.src!==preview?.src||!image.src.startsWith('blob:')||image.width!==preview.width||image.height!==preview.height)throw Error('Native viewer image does not match the unique completed preview');
  const controls=dialog.controls.filter(c=>c.labels.some(x=>/^(download|download image|save image)$/i.test(x)));
  if(controls.length!==1)throw Error('Native viewer download control is ambiguous');
  const dir=path.join(path.dirname(targetPath),`.oracle-native-${process.pid}-${Date.now()}`);await fs.mkdir(dir,{recursive:false});
  const before=await fs.readdir(dir);if(before.length)throw Error('Native download directory is not empty');
  const set=Client.send?.bind(Client)??Browser.setDownloadBehavior?.bind(Browser);if(!set)throw Error('Browser download control unavailable');
  await set('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:dir,eventsEnabled:true});
  const click=await Runtime.evaluate({expression:buildNativeDownloadClick(runtime,prompt,semantics,roots,minTurnIndex),returnByValue:true});
  if(click.result?.value?.clicked!==true)throw Error(`Native download click refused: ${click.result?.value?.reason||'unknown'}`);let files=[];const until=Date.now()+(Number.isFinite(downloadTimeoutMs)?Math.max(1,downloadTimeoutMs):30000);
  do{files=await fs.readdir(dir);if(files.some(n=>n.endsWith('.crdownload'))){}else if(files.length===1)break;else if(files.length>1)throw Error('Multiple native downloads');await new Promise(r=>setTimeout(r,100));}while(Date.now()<until);
  if(files.length!==1||files[0].endsWith('.crdownload'))throw Error('Native download did not complete');const bytes=await fs.readFile(path.join(dir,files[0]));const proof=validateOriginalPng(bytes,image);await fs.copyFile(path.join(dir,files[0]),absolute,fs.constants.COPYFILE_EXCL);if(sha256(await fs.readFile(absolute))!==proof.sha256)throw Error('Exclusive native image copy hash mismatch');
  logger?.('[browser] Saved original image from the verified native viewer.');return [{kind:'image',path:absolute,label:'Generated image',mimeType:'image/png',sizeBytes:proof.sizeBytes,sourceUrl:'browser-native-download',url:'browser-native-download',finalUrl:'browser-native-download',alt:'Generated image',width:proof.width,height:proof.height}];
}
