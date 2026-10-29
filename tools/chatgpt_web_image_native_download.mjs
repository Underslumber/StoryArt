import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {pathToFileURL, fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {buildNativePreviewOpen as sharedPreviewOpen, buildNativeViewerProbe as sharedViewerProbe, buildNativeDownloadClick as sharedDownloadClick, validateOriginalPng as sharedPngValidator} from '../runtime/chatgpt-web-image/oracle-patches/dist/src/browser/chatgptNativeImageDownload.js';

const sha = value => createHash('sha256').update(value).digest('hex');
export const buildNativePreviewOpen = (...args) => sharedPreviewOpen(...args);
export const buildNativeViewerProbe = (...args) => sharedViewerProbe(...args);
export const buildNativeDownloadClick = (...args) => sharedDownloadClick(...args);

export function validateOriginalPng(bytes, expected = {}) {
  return sharedPngValidator(bytes, expected);
}

async function verifyRuntimeBinding(binding) {
  if(!binding)return;
  const bytes=await fs.readFile(binding.manifest_path);
  if(sha(bytes)!==binding.manifest_sha256)throw Error('Installed runtime manifest changed');
  for(const item of JSON.parse(bytes).files)if(sha(await fs.readFile(item.path))!==item.sha256)throw Error('Installed runtime file changed: '+item.path);
}

async function main() {
  const journal=path.resolve(process.argv[2]);const guardPath=path.resolve(process.argv[3]);const operation=process.argv[4]||'open-original-image';
  const suppliedViewerReceipt=process.argv[5]?path.resolve(process.argv[5]):null;const suppliedViewerSha=process.argv[6]||null;
  if(!['open-original-image','inspect-original-viewer','download-original-image'].includes(operation))throw Error('Unsupported native image operation');
  const raw=await fs.readFile(journal);const state=JSON.parse(raw);const home=path.resolve(state.oracle_home);
  if(!path.isAbsolute(state.oracle_root)||state.runtime_binding&&path.resolve(state.runtime_binding.installation)!==path.resolve(state.oracle_root))throw Error('Private runtime binding mismatch');
  await verifyRuntimeBinding(state.runtime_binding);
  const active=JSON.parse(await fs.readFile(path.join(path.dirname(journal),'.chatgpt-web-image-active.json')));
  if(path.resolve(active.journal)!==journal)throw Error('Journal is not the active request');
  const guardRaw=await fs.readFile(guardPath);const guard=JSON.parse(guardRaw);
  if(guard.status!=='ACTIVE'||guard.locked_invariants?.send!=='ZERO'||guard.locked_invariants?.cloudflare!=='NO_BYPASS'||guard.required_stages?.find(stage=>stage.id==='REVIEW')?.status!=='COMPLETED')throw Error('Native image action guard mismatch');
  const receiptRaw=await fs.readFile(path.join(home,'receipt.json'));const metaPath=path.join(home,'sessions',state.session_id,'meta.json');const metaRaw=await fs.readFile(metaPath);const meta=JSON.parse(metaRaw);
  const live=await import('./chatgpt_web_image_live_status.mjs');const guardJson=JSON.parse(guardRaw);const {runtime,retainedOriginal}=live.validateLiveStatus(state,meta,JSON.parse(receiptRaw),guardJson);
  if(sha(await fs.readFile(state.scenario_evidence.path))!==state.scenario_evidence.sha256||sha(await fs.readFile(state.scenario_evidence.plan.path))!==state.scenario_evidence.plan.sha256||sha(await fs.readFile(state.project_binding.path))!==state.project_binding.sha256)throw Error('Bound scenario or destination changed');
  const facts=JSON.parse(execFileSync('powershell.exe',['-NoProfile','-Command',`$p=Get-CimInstance Win32_Process -Filter 'ProcessId = ${runtime.chromePid} OR ProcessId = ${runtime.controllerPid}';$n=Get-NetTCPConnection -State Listen -LocalPort ${runtime.chromePort};[pscustomobject]@{chromeAlive=[bool]($p|Where-Object ProcessId -eq ${runtime.chromePid});controllerAlive=[bool]($p|Where-Object ProcessId -eq ${runtime.controllerPid});chromeParent=($p|Where-Object ProcessId -eq ${runtime.chromePid}).ParentProcessId;listenerOwned=[bool]($n|Where-Object {$_.OwningProcess -eq ${runtime.chromePid} -and $_.LocalAddress -eq '127.0.0.1'})}|ConvertTo-Json -Compress`],{windowsHide:true,encoding:'utf8'}));live.validateLiveFacts(facts,runtime,retainedOriginal);
  process.env.ORACLE_PARSE_NATIVE_IMAGE_USER='1';
  const base=path.join(state.oracle_root,'node_modules/@steipete/oracle/dist/src/browser');
  const lifecycle=await import(pathToFileURL(path.join(base,'chromeLifecycle.js')).href);const turns=await import(pathToFileURL(path.join(base,'conversationTurns.js')).href);
  const connection=await lifecycle.connectToRemoteChromeTarget(runtime.chromeHost,runtime.chromePort,()=>{},{targetId:runtime.chromeTargetId});let opened=null,viewer=null,download=null;
  const proofPath=operation==='download-original-image'&&suppliedViewerReceipt?suppliedViewerReceipt:path.join(home,'native-download-viewer-'+Date.now()+'.json');
  try{
    if(connection.targetId!==runtime.chromeTargetId)throw Error('Original browser target mismatch');
    if(operation==='open-original-image'){
      const attempt=path.join(home,'native-download-open-attempt.json');await fs.writeFile(attempt,JSON.stringify({status:'UNKNOWN',operation,sessionId:state.session_id,conversationId:runtime.conversationId,sendOperations:0}),{flag:'wx'});
      opened=(await connection.client.Runtime.evaluate({expression:buildNativePreviewOpen(runtime,state.prompt,turns.buildConversationTurnSemantics(),turns.buildConversationTurnListExpression("document.querySelector('main')")),returnByValue:true})).result?.value;
      if(!opened?.opened)throw Error('Native preview refused: '+(opened?.reason||'unknown'));
    }
    viewer=(await connection.client.Runtime.evaluate({expression:buildNativeViewerProbe(runtime,state.prompt,turns.buildConversationTurnSemantics(),turns.buildConversationTurnListExpression("document.querySelector('main')")),returnByValue:true})).result?.value;
    if(!viewer?.allowed)throw Error('Native viewer probe refused');
    if(operation==='download-original-image'){
      if(!suppliedViewerReceipt||path.dirname(suppliedViewerReceipt)!==home||!/^native-download-viewer-[0-9]+\.json$/.test(path.basename(suppliedViewerReceipt))||!/^([a-f0-9]{64})$/.test(suppliedViewerSha||''))throw Error('Scoped native viewer receipt required');
      const receiptBytes=await fs.readFile(suppliedViewerReceipt);if(sha(receiptBytes)!==suppliedViewerSha)throw Error('Native viewer receipt changed');
      const expected=JSON.parse(receiptBytes);const {sha256:expectedHash,...expectedPayload}=expected;
      if(sha(JSON.stringify(expectedPayload))!==expectedHash||expected.sessionId!==state.session_id||expected.conversationId!==runtime.conversationId)throw Error('Native viewer receipt mismatch');
      const preview=viewer.previews?.[0];if(viewer.previewCount!==1||!preview?.complete||preview.source!=='blob'||viewer.dialogCount!==1)throw Error('Native output is not uniquely identified');
      const directory=path.join(home,'native-original-download');await fs.mkdir(directory);const requested=path.resolve(state.requested_output);
      try{await fs.access(requested);throw Error('Requested output already exists');}catch(error){if(error.code!=='ENOENT')throw error;}
      await connection.client.send('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:directory,eventsEnabled:true});
      const click=(await connection.client.Runtime.evaluate({expression:buildNativeDownloadClick(runtime,state.prompt,turns.buildConversationTurnSemantics(),turns.buildConversationTurnListExpression("document.querySelector('main')")),returnByValue:true})).result?.value;
      if(click?.clicked!==true)throw Error('Native Download refused: '+(click?.reason||'unknown'));
      let entries=[];const deadline=Date.now()+30000;
      while(Date.now()<deadline){entries=await fs.readdir(directory);const complete=entries.filter(name=>!name.endsWith('.crdownload'));if(complete.length>1)throw Error('Multiple native output files');if(complete.length===1&&!entries.some(name=>name.endsWith('.crdownload'))){const file=path.join(directory,complete[0]);const a=await fs.stat(file);await new Promise(resolve=>setTimeout(resolve,500));const b=await fs.stat(file);if(a.size===b.size&&b.size>0)break;}await new Promise(resolve=>setTimeout(resolve,250));}
      entries=await fs.readdir(directory);if(entries.length!==1||entries[0].endsWith('.crdownload'))throw Error('Native Download did not produce one complete image');
      const original=path.join(directory,entries[0]);const bytes=await fs.readFile(original);const image=validateOriginalPng(bytes,preview);
      await fs.copyFile(original,requested,fs.constants.COPYFILE_EXCL);if(sha(await fs.readFile(requested))!==image.sha256)throw Error('Exclusive output copy mismatch');
      download={originalPath:original,outputPath:requested,...image,click,downloadProof:{viewerReceiptPath:suppliedViewerReceipt,viewerReceiptSha256:suppliedViewerSha}};
    }else if(operation==='inspect-original-viewer'){
      if(viewer.dialogCount!==1)throw Error('Native viewer is not uniquely visible');
    }else{
      const receipt={operation:'inspect-original-viewer',sessionId:state.session_id,conversationId:runtime.conversationId,viewer};receipt.sha256=sha(JSON.stringify(receipt));
      await fs.writeFile(proofPath,JSON.stringify(receipt),{flag:'wx'});
    }
  }finally{await connection.close();}
  const output={operation,download,viewerReceiptPath:operation==='open-original-image'?proofPath:null,viewerReceiptSha256:operation==='open-original-image'?sha(await fs.readFile(proofPath)):null,sessionId:state.session_id,conversationId:runtime.conversationId,journalSha256:sha(raw),metaSha256:sha(metaRaw),receiptSha256:sha(receiptRaw),guardSha256:sha(guardRaw),opened,viewer,sendOperations:0,imageCollectOperations:0,downloadOperations:download?1:0,cleanup:'observer-client-closed'};
  const destination=path.join(home,'native-download-'+Date.now()+'.json');await fs.writeFile(destination,JSON.stringify(output,null,2),{flag:'wx'});process.stdout.write(JSON.stringify({...output,diagnostic_receipt:destination})+'\n');
}

if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))await main();
