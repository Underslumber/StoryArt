import { createRequire } from 'node:module';
import { spawn, spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import fs from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { runImageTransport } from './chatgpt_web_image_runtime.js';
export function desktopBrokerConfiguration(isolated) {
  return isolated === true
    ? {relativePath:'../.agent/oracle-hidden-windows/session-broker/DesktopBrokerSession.exe',sha256:'37df09b866684140b9182003e4bdfaa2e21b74717f9cb51f3bbc43db7eb1a0e1',allowDisconnectedSession:true}
    : {relativePath:'../.agent/oracle-hidden-windows/DesktopBroker.exe',sha256:'118da0310cddc22084c3c7eea00a6402549b326e40080da3a0841a670c967ae2',allowDisconnectedSession:false};
}
export function validateDesktopLaunchReceipt(receipt) {
  return Boolean(receipt && Number.isInteger(receipt.workerPid) && receipt.workerPid > 0 &&
    receipt.state === 'RUNNING' && /^OracleIsolated_[a-f0-9]{32}$/.test(receipt.desktop || '') &&
    receipt.inputReadable === true && receipt.inputBefore === 'Default' &&
    receipt.threadDesktop === 'Default' && receipt.allowDisconnectedSession === false &&
    receipt.prelaunch?.inputReadable === true && receipt.prelaunch?.inputName === 'Default');
}
export function validateInheritedDesktopProof(proof, receipt, launchId, operationId) {
  return Boolean(proof && proof.schema_version === 1 && proof.launch_id === launchId &&
    proof.operation_id === operationId && proof.worker_pid === receipt.workerPid &&
    proof.parentPid === receipt.workerPid && Number.isInteger(proof.probePid) && proof.probePid > 0 &&
    proof.sessionId === receipt.sessionId && proof.desktop === receipt.desktop && proof.inputDesktop === 'Default');
}
async function verifyIsolatedDesktop(receiptPath, launchId, operationId) {
  const receipt=JSON.parse(await fs.readFile(receiptPath,'utf8'));
  if (!validateDesktopLaunchReceipt(receipt)) throw Error('Isolated desktop startup receipt invalid; no consult');
  const proof=JSON.parse(await fs.readFile(receiptPath+'.process-proof.json','utf8'));
  if (!validateInheritedDesktopProof(proof,receipt,launchId,operationId)) throw Error('Inherited launch desktop proof invalid; no consult');
  const script=String.raw`$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue'
Add-Type -TypeDefinition @'
using System;using System.Text;using System.Runtime.InteropServices;
public static class CurrentInputDesktop {
 [DllImport("user32.dll",SetLastError=true)] static extern IntPtr OpenInputDesktop(uint f,bool i,uint a);
 [DllImport("user32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool GetUserObjectInformationW(IntPtr h,int i,StringBuilder b,uint s,out uint n);
 [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr h);
 public static string Name(){var h=OpenInputDesktop(0,false,0x0041);uint n;var b=new StringBuilder(512);if(h==IntPtr.Zero)throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());try{if(!GetUserObjectInformationW(h,2,b,1024,out n))throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());return b.ToString();}finally{CloseDesktop(h);}}
}
'@
[CurrentInputDesktop]::Name()`;
  const probe=spawnSync('powershell.exe',['-NoProfile','-NonInteractive','-EncodedCommand',Buffer.from(script,'utf16le').toString('base64')],{encoding:'utf8',windowsHide:true,timeout:15000});
  if (probe.error || probe.status !== 0 || probe.stdout.trim() !== 'Default') throw Error('Current input desktop not Default; no consult');
}
export function nativeImageEnvironment(request){
 if(request.operation!=='submit')return {};
 const route=request.project_binding?.route;
 if(!route||!/^g-p-[a-f0-9]+$/.test(route.project_id)||route.project_url!==`https://chatgpt.com/g/${route.project_id}/project`)throw new Error('Native image requires pinned project route');
 return {ORACLE_REQUIRE_NATIVE_IMAGE_TOOL:'1',ORACLE_IMAGE_PROJECT_ID:route.project_id};
}
export const nativeImagePins={
 'browser/actions/native-image-production.mjs':'2e6f676ce00e58d679a8ad5b1a8481bd7092630b3ac8b53e43bc05cf9830f923',
 'browser/index.js':'1797fbd96a56952298e504ff54469d641de28fff7c241a19c35b164acd77253c',
 'browser/actions/promptComposer.js':'34d37b472722c6a1fa454776176009b367740878761741f4207d66c624a83779',
 'browser/providers/chatgptDomProvider.js':'b0971de1f49de12c34b8324640af2249bb466ba84abbdd70a21b26c22cf30094'
};
export async function verifyRuntimeBinding(binding, installation){
 if(!binding?.manifest_path||!binding.manifest_sha256||!path.isAbsolute(binding.manifest_path))throw Error('Installed runtime binding is incomplete');
 if(installation&&(!binding.installation||path.resolve(binding.installation)!==path.resolve(installation)))throw Error('Installed runtime binding root changed');
 const bytes=await fs.readFile(binding.manifest_path);
 if(createHash('sha256').update(bytes).digest('hex')!==binding.manifest_sha256)throw Error('Installed runtime manifest changed');
 const manifest=JSON.parse(bytes);
 if(!Array.isArray(manifest.files))throw Error('Installed runtime file manifest is invalid');
 for(const item of manifest.files){
  if(!item.path||!path.isAbsolute(item.path)||! /^[a-f0-9]{64}$/.test(item.sha256||''))throw Error('Installed runtime file pin is invalid');
  if(createHash('sha256').update(await fs.readFile(item.path)).digest('hex')!==item.sha256)throw Error('Installed runtime file changed');
 }
}
export async function verifyNativeImageInstallation(base,pins=nativeImagePins){
 for(const [name,pin] of Object.entries(pins)){
  if(!/^[a-f0-9]{64}$/.test(pin))throw new Error('Native image installation not approved; no submit');
  const actual=createHash('sha256').update(await fs.readFile(path.join(base,name))).digest('hex');
  if(actual!==pin)throw new Error('Native image installation changed; no submit');
 }
}
let raw = ''; for await (const chunk of process.stdin) raw += chunk;
const request = JSON.parse(raw);
const installation = request.oracle_root;
if(!installation || !path.isAbsolute(installation))throw new Error('Private runtime installation required');
if(request.operation === 'submit' && !/^[a-f0-9]{32}$/.test(request.submission_operation_id || ''))
  throw new Error('Durable submission operation ID required');
const phasePath = request.operation === 'submit'
  ? path.join(request.oracle_home, `transport-phase-${request.submission_operation_id}.jsonl`) : null;
async function phase(name, extra = {}) {
  if (!phasePath) return;
  const row = {schema_version:1, operation_id:request.submission_operation_id,
    prompt_sha256:request.prompt_sha256, phase:name, at:new Date().toISOString(), ...extra};
  const handle = await fs.open(phasePath, 'a');
  try { await handle.writeFile(JSON.stringify(row) + '\n'); await handle.sync(); }
  finally { await handle.close(); }
}
// Use the same pinned Oracle installation and MCP client that Web Advisor uses.
const advisorRoot = 'D:\\Projects\\Web-Advisor';
const advisorRequire = createRequire(path.join(advisorRoot, 'package.json'));
const advisorPkg = advisorRequire.resolve('@steipete/oracle/package.json');
if (JSON.parse(await fs.readFile(advisorPkg, 'utf8')).version !== '0.21.1') throw new Error('Pinned Advisor Oracle version changed');
const isolated = request.windows_isolated_browser === true;
const recoveryOperations = ['recover','recovery-status','image-tool-inspect'];
if (recoveryOperations.includes(request.operation) && !isolated)
  throw new Error('Legacy recovery requires its original isolated-browser proof');
const isolatedMode = request.browser_mode === 'windows-isolated' && request.windows_isolated_browser === true;
if (!recoveryOperations.includes(request.operation) && request.operation === 'submit' && request.browser_mode !== 'headless' && !isolatedMode)
  throw new Error('Image submit requires headless mode or the receipt-verified isolated desktop route');
if (!recoveryOperations.includes(request.operation) && request.browser_mode && request.browser_mode !== 'headless' && !isolatedMode)
  throw new Error('Pinned browser mode changed; no visible-browser fallback');
if(request.runtime_binding){
 await verifyRuntimeBinding(request.runtime_binding,request.oracle_root);
}else if(request.operation==='submit')await verifyNativeImageInstallation(path.join(path.dirname(advisorPkg),'dist/src'));

if (['recover','recovery-status','image-tool-inspect'].includes(request.operation)) {
  if (!isolated) throw new Error('Recovery requires explicit isolated Windows mode');
  const runtimeRequire = createRequire(path.join(installation, 'package.json'));
  const { StdioClientTransport, getDefaultEnvironment } = await import(pathToFileURL(runtimeRequire.resolve('@modelcontextprotocol/client/stdio')));
  const env = { ...getDefaultEnvironment(), ...nativeImageEnvironment(request), ORACLE_ENGINE:'browser',
    ORACLE_HOME_DIR:request.oracle_home, ORACLE_BROWSER_PROFILE_DIR:request.profile_dir,
    ORACLE_IMAGE_BROWSER_DOWNLOAD_ONLY:'1' };
  if (request.runtime_binding?.chrome_path) env.CHROME_PATH=request.runtime_binding.chrome_path;
  else if (process.env.CHROME_PATH) env.CHROME_PATH=process.env.CHROME_PATH;
  const packet = path.join(request.oracle_home, `recovery-request-${randomUUID()}.json`);
  await fs.writeFile(packet, JSON.stringify(request), {flag:'wx'});
  let workerCommand = process.execPath;
  let workerArgs = [path.join(path.dirname(fileURLToPath(import.meta.url)), 'chatgpt_web_image_recovery.mjs'), packet];
  const brokerConfig = request.runtime_binding ? JSON.parse(await fs.readFile(request.runtime_binding.manifest_path,'utf8')).brokers.DesktopBrokerSession : desktopBrokerConfiguration(true);
  if (!/^[a-f0-9]{64}$/.test(brokerConfig.sha256)) throw new Error('Session desktop broker fingerprint not approved; no launch');
  const broker = brokerConfig.path || path.resolve(path.dirname(fileURLToPath(import.meta.url)), brokerConfig.relativePath);
  if (createHash('sha256').update(await fs.readFile(broker)).digest('hex') !== brokerConfig.sha256)
    throw new Error('Missing or changed Windows desktop broker; no foreground fallback');
  const launchId = randomUUID();
  const launchPacket = path.join(request.oracle_home, `desktop-launch-${launchId}.json`);
  workerArgs.push(path.join(request.oracle_home, `desktop-receipt-${launchId}.json`));
  await fs.writeFile(launchPacket, JSON.stringify({executable:workerCommand,argv:workerArgs,cwd:request.oracle_home,
    expectedInputDesktop:'Default',allowDisconnectedSession:true,
    receipt:path.join(request.oracle_home,`desktop-receipt-${launchId}.json`)}), {flag:'wx'});
  const child = spawn(broker,[launchPacket],{cwd:request.oracle_home,env,windowsHide:true,stdio:['ignore','pipe','pipe']});
  child.stdout.pipe(process.stdout); child.stderr.pipe(process.stderr);
  const code=await new Promise((resolve,reject)=>{child.once('error',reject);child.once('exit',resolve);});
  if(code!==0)throw new Error('Original image recovery failed; no resubmit');
  process.exit(0);
}

const { ProjectLocalOracleMcpClient, resolveOracleEntrypoint } = await import(pathToFileURL(path.join(advisorRoot, 'dist/oracle.js')));
const launchContext = {cwd:request.oracle_home,environment:{
  ORACLE_HOME_DIR:request.oracle_home, ORACLE_BROWSER_PROFILE_DIR:request.profile_dir,
  ORACLE_IMAGE_BROWSER_DOWNLOAD_ONLY:'1', ...nativeImageEnvironment(request),
  ...(request.runtime_binding?.chrome_path ? {CHROME_PATH:request.runtime_binding.chrome_path} : {})
}};
let desktopReceiptPath, desktopLaunchId;
if (isolatedMode && !recoveryOperations.includes(request.operation)) {
  if (!request.runtime_binding?.manifest_path) throw new Error('Pinned runtime manifest required for isolated desktop launch; no consult');
  const manifest = JSON.parse(await fs.readFile(request.runtime_binding.manifest_path, 'utf8'));
  const brokerConfig = manifest.brokers?.DesktopBrokerSession;
  if (!brokerConfig?.path || !/^[a-f0-9]{64}$/.test(brokerConfig.sha256 || '') ||
      createHash('sha256').update(await fs.readFile(brokerConfig.path)).digest('hex') !== brokerConfig.sha256)
    throw new Error('Manifest-pinned DesktopBrokerSession changed; no consult');
  const launchId = randomUUID();
  desktopLaunchId=launchId;
  const launchPacket = path.join(request.oracle_home, `advisor-desktop-launch-${launchId}.json`);
  desktopReceiptPath = path.join(request.oracle_home, `advisor-desktop-receipt-${launchId}.json`);
  await fs.writeFile(launchPacket, JSON.stringify({executable:process.execPath,
    argv:['--require',path.join(path.dirname(fileURLToPath(import.meta.url)),'chatgpt_web_image_desktop_proof.js'),resolveOracleEntrypoint(advisorRoot,'oracle-mcp.js')],cwd:request.oracle_home,
    launchId,submissionOperationId:request.submission_operation_id,
    expectedInputDesktop:'Default',allowDisconnectedSession:false,receipt:desktopReceiptPath}), {flag:'wx'});
  launchContext.launcher = {command:brokerConfig.path,args:[launchPacket]};
}
const client = new ProjectLocalOracleMcpClient(advisorRoot,launchContext);
const envKeys = ['ORACLE_HOME_DIR','ORACLE_BROWSER_PROFILE_DIR','CHROME_PATH'];
const originalEnv = new Map(envKeys.map(key => [key, process.env[key]]));
process.env.ORACLE_HOME_DIR = request.oracle_home;
process.env.ORACLE_BROWSER_PROFILE_DIR = request.profile_dir;
if (request.runtime_binding?.chrome_path) process.env.CHROME_PATH = request.runtime_binding.chrome_path;
try {
  // Load only our tool-owned configuration and fail closed on project inheritance.
  const { loadUserConfig } = await import(pathToFileURL(path.join(path.dirname(advisorPkg), 'dist/src/config.js')));
  const { config } = await loadUserConfig({ cwd: request.oracle_home });
  const expected = JSON.parse(await fs.readFile(path.join(request.oracle_home, 'config.json'), 'utf8'));
  if (JSON.stringify(config) !== JSON.stringify(expected)) throw new Error('Inherited Oracle configuration differs from private configuration');
  if (config.engine !== 'browser' || config.remote?.host || config.browser?.cookieSync !== false ||
      config.browser?.manualLoginCookieSync !== false || config.browser?.captureProviderNative !== false ||
      config.browser?.manualLogin !== true || config.browser?.headless !== (isolatedMode ? false : true)) throw new Error('Unsafe effective Oracle configuration; headless requires a verified isolated desktop route');
  if (request.operation === "submit") {
    const project = request.project_binding?.route;
    if (!project || !/^g-p-[a-f0-9]+$/.test(project.project_id) ||
        project.project_url !== `https://chatgpt.com/g/${project.project_id}/project` ||
        config.browser.chatgptUrl !== project.project_url || config.browser.url !== project.project_url)
      throw new Error("Missing or changed dedicated project URL; no home fallback");
    const portFile = path.join(request.profile_dir,'DevToolsActivePort');
    try {
      const port = Number((await fs.readFile(portFile,'utf8')).split(/\r?\n/)[0]);
      if (!Number.isInteger(port) || port < 1 || port > 65535) throw Error('Invalid profile DevTools port');
      let response;
      try { response = await fetch(`http://127.0.0.1:${port}/json/version`,{signal:AbortSignal.timeout(2000)}); }
      catch { response = null; } // Oracle handles stale, unreachable port metadata itself.
      if (response && (!response.ok || !String((await response.json())['User-Agent'] || '').includes('HeadlessChrome')))
        throw Error('Existing profile Chrome is not verified headless; no visible reuse');
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  if (request.operation === 'submit') await phase('CLIENT_CONNECTING', {provider_call_started:false});
  try {
    await client.connect();
    if (desktopReceiptPath) await verifyIsolatedDesktop(desktopReceiptPath,desktopLaunchId,request.submission_operation_id);
  } catch (error) {
    if (request.operation === 'submit') await phase('CONNECT_FAILED', {
      provider_call_started:false, error_code:typeof error?.code === 'string' ? error.code : 'CONNECT_FAILED'});
    throw error;
  }
  if (request.operation === 'submit') await phase('CLIENT_CONNECTED', {provider_call_started:false});
  await runImageTransport(request, async (name, args) => {
    return await client.callTool(name, args);
  }, async value => {
    if (value.type === 'receipt') {
      const handle = await fs.open(path.join(request.oracle_home, 'receipt.json'), 'wx');
      try { await handle.writeFile(JSON.stringify(value)); await handle.sync(); } finally { await handle.close(); }
      await phase('RECEIPT_DURABLE', {provider_call_started:true, session_id:value.session_id});
    }
    process.stdout.write(JSON.stringify(value) + '\n');
  }, async ({name}) => {
    if (name === 'consult') await phase('PROVIDER_CALL_MAY_HAVE_BEEN_SENT', {provider_call_started:true});
  });
} finally {
  await client.close().catch(() => {});
  for (const key of envKeys) {
    const value = originalEnv.get(key);
    if (value === undefined) delete process.env[key]; else process.env[key] = value;
  }
}
