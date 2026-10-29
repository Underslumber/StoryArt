import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import {createHash} from 'node:crypto';
import {deflateSync} from 'node:zlib';
import {pathToFileURL} from 'node:url';

const fixtureRoot=process.env.ORACLE_FIXTURE_ROOT||path.resolve('.local/chatgpt-web-image/installation/node_modules/@steipete/oracle');
try{await fs.access(path.join(fixtureRoot,'dist/src/browser/chatgptImages.js'));}catch{throw Error('Oracle integration fixture is missing; set ORACLE_FIXTURE_ROOT or run the project runtime setup first');}
const source=path.resolve('runtime/chatgpt-web-image/oracle-patches/dist/src/browser');
const target=path.join(fixtureRoot,'dist/src/browser');
for(const file of ['index.js','chatgptImages.js','chatgptNativeImageDownload.js'])await fs.copyFile(path.join(source,file),path.join(target,file));
const oracle=await import(pathToFileURL(path.join(target,'chatgptImages.js')).href+`?test=${Date.now()}`);
const oracleIndex=await import(pathToFileURL(path.join(target,'index.js')).href+`?test=${Date.now()}`);
assert.equal(typeof oracle.collectGeneratedImageArtifacts,'function');

function crc32(buf){let c=0xffffffff;for(const b of buf){c^=b;for(let i=0;i<8;i++)c=(c>>>1)^((c&1)?0xedb88320:0);}return (c^0xffffffff)>>>0;}
function chunk(name,data){const type=Buffer.from(name),out=Buffer.alloc(data.length+12);out.writeUInt32BE(data.length,0);type.copy(out,4);data.copy(out,8);out.writeUInt32BE(crc32(Buffer.concat([type,data])),8+data.length);return out;}
function makePng(w,h){const ih=Buffer.alloc(13);ih.writeUInt32BE(w,0);ih.writeUInt32BE(h,4);ih[8]=8;ih[9]=6;const raw=Buffer.alloc((w*4+1)*h);return Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',ih),chunk('IDAT',deflateSync(raw)),chunk('IEND',Buffer.alloc(0))]);}
const png=makePng(64,32),prompt='Create a new image from this exact scene';

class Element {
  constructor(tag,text=''){this.tagName=tag.toUpperCase();this.textContent=text;this.innerText=text;this.children=[];this.parentElement=null;this.attrs={};this.complete=true;this.naturalWidth=0;this.naturalHeight=0;this.currentSrc='';this.disabled=false;this.visible=true;this.clickCount=0;this.nodeType=1;}
  append(child){child.parentElement=this;this.children.push(child);return child;}
  get attributes(){return Object.entries(this.attrs).map(([name])=>({name}));}
  getAttribute(n){return this.attrs[n]??null;} hasAttribute(n){return n in this.attrs;}
  matches(sel){return sel==='main'&&this.tagName==='MAIN';}
  contains(n){for(let p=n;p;p=p.parentElement)if(p===this)return true;return false;}
  compareDocumentPosition(other){return flatten(main).indexOf(this)<flatten(main).indexOf(other)?4:2;}
  querySelectorAll(sel){const all=flatten(this).slice(1);if(sel==='h4')return all.filter(n=>n.tagName==='H4');if(sel.startsWith('[data-testid^="conversation-turn"]')||sel.includes('article[data-testid'))return [];if(sel==='button[data-testid="generated-image-preview"]')return all.filter(n=>n.tagName==='BUTTON'&&n.getAttribute('data-testid')==='generated-image-preview');if(sel==='button[data-testid="generated-image-preview"] img')return all.filter(n=>n.tagName==='IMG'&&n.parentElement?.getAttribute('data-testid')==='generated-image-preview');if(sel==='img')return all.filter(n=>n.tagName==='IMG');if(sel==='button,[role="button"],a[download]')return all.filter(n=>n.tagName==='BUTTON'||n.getAttribute('role')==='button'||(n.tagName==='A'&&n.hasAttribute('download')));return [];}
  querySelector(sel){return this.querySelectorAll(sel)[0]??null;}
  getBoundingClientRect(){return this.visible?{width:800,height:600}:{width:0,height:0};}
  click(){this.clickCount++;this.onClick?.();}
  cloneNode(){const c=new Element(this.tagName,this.textContent);return c;}
}
function flatten(n){return [n,...n.children.flatMap(flatten)];}
const main=new Element('main');
const addTurn=(role,text)=>{const turn=main.append(new Element('div'));const heading=turn.append(new Element('h4',role==='user'?'You said:':'ChatGPT said:'));turn.append(new Element('div',text));turn.textContent=text;turn.innerText=text;turn.attrs['data-message-author-role']=role;return turn;};
addTurn('user',prompt);
const assistant=addTurn('assistant','Image generated');
const previewButton=assistant.children[1].append(new Element('button'));previewButton.attrs['data-testid']='generated-image-preview';
const previewImage=previewButton.append(new Element('img'));previewImage.naturalWidth=64;previewImage.naturalHeight=32;previewImage.currentSrc='blob:exact-preview';
let dialogs=[];let claimId='fixture-claim';const mainDoc={querySelector:sel=>sel==='main'?main:null,querySelectorAll:sel=>sel==='main'?[main]:sel==='[role="dialog"]'?dialogs:[]};
const dialog=new Element('div');dialog.attrs.role='dialog';const viewImage=dialog.append(new Element('img'));Object.assign(viewImage,{naturalWidth:64,naturalHeight:32,currentSrc:'blob:exact-preview'});
const download=dialog.append(new Element('button'));download.attrs['aria-label']='Download image';
let downloadDir=null;let sends=0;let partialDownload=false;
previewButton.onClick=()=>{dialogs=[dialog];};
download.onClick=()=>{void fs.writeFile(path.join(downloadDir,partialDownload?'original.png.crdownload':'original.png'),png);};
const context={document:mainDoc,location:{href:'https://chatgpt.com/g/g-p-test/c/conversation'},sessionStorage:{getItem:()=>JSON.stringify({id:claimId})},getComputedStyle:n=>({display:n.visible?'block':'none',visibility:'visible',opacity:'1'}),URL,Buffer,HTMLElement:Element,Node:{DOCUMENT_POSITION_FOLLOWING:4},setTimeout,clearTimeout};
const Runtime={evaluate:async({expression})=>{try{return {result:{value:vm.runInNewContext(expression,context)}};}catch{return {result:{value:[]}};}}};
const Client={send:async(method,args)=>{sends++;assert.equal(method,'Browser.setDownloadBehavior');downloadDir=args.downloadPath;}};
const temp=await fs.mkdtemp(path.join(os.tmpdir(),'oracle-native-image-'));
try{
 process.env.ORACLE_IMAGE_BROWSER_DOWNLOAD_ONLY='1';
 const output=path.join(temp,'result.png');
 const binding={tabUrl:context.location.href,ownedRecoveryTarget:{claimId:'fixture-claim'}};
 let pollEvalCount=0;let snapshotText='';const pollRuntime={evaluate:async(args)=>{if(pollEvalCount++%2===0)return {result:{value:{text:snapshotText,html:'',turnId:'fixture-turn',messageId:'fixture-message'}}};return Runtime.evaluate(args);}};
 const poll=async(promptText=prompt,nativeRuntime=binding)=>oracleIndex.__test__.pollGeneratedImageOrTextAssistantResponse(pollRuntime,30,0,'conversation',promptText,nativeRuntime);
 const ready=await poll();assert.equal(ready?.nativeImageReady,true);assert.equal(previewButton.clickCount,0);assert.equal(download.clickCount,0);
 for(const chromeText of ['Edit','Stopped thinking','Stopped thinking Edit','Thought for 1s Edit']){snapshotText=chromeText;pollEvalCount=0;assert.equal((await poll())?.nativeImageReady,true,chromeText);assert.equal(previewButton.clickCount,0);assert.equal(download.clickCount,0);}
 snapshotText='';pollEvalCount=0;
 assert.equal(await poll('foreign prompt'),null);assert.equal(await poll(prompt,{...binding,ownedRecoveryTarget:{claimId:'tampered'}}),null);assert.equal(await poll(prompt,{...binding,tabUrl:'https://chatgpt.com/c/foreign'}),null);assert.equal(previewButton.clickCount,0);assert.equal(download.clickCount,0);
 const collect=(target,extra={})=>oracle.collectGeneratedImageArtifacts({Browser:{},Client,Page:{},Runtime,minTurnIndex:0,expectedPrompt:prompt,nativeRuntime:{tabUrl:context.location.href,ownedRecoveryTarget:{claimId:'fixture-claim'}},generateImagePath:target,answerText:'done',...extra});
 const result=await collect(output);
 assert.equal(previewButton.clickCount,1);assert.equal(download.clickCount,1);assert.equal(sends,1);assert.equal(result.savedImages.length,1);assert.deepEqual(await fs.readFile(output),png);assert.equal(result.savedImages[0].url,'browser-native-download');
 const beforePreview=previewButton.clickCount,beforeDownload=download.clickCount;
 const existing=path.join(temp,'occupied.png');await fs.writeFile(existing,png);await assert.rejects(()=>collect(existing),/already exists/);assert.equal(previewButton.clickCount,beforePreview);assert.equal(download.clickCount,beforeDownload);
 claimId='tampered';await assert.rejects(()=>collect(path.join(temp,'claim.png')),/Native preview refused/);assert.equal(previewButton.clickCount,beforePreview);claimId='fixture-claim';
 addTurn('user',prompt);await assert.rejects(()=>collect(path.join(temp,'duplicate.png')),/prompt-ambiguous/);assert.equal(previewButton.clickCount,beforePreview);main.children.pop();
 const extraButton=assistant.children[1].append(new Element('button'));extraButton.attrs['data-testid']='generated-image-preview';extraButton.append(new Element('img'));await assert.rejects(()=>collect(path.join(temp,'multiple.png')),/preview-count/);assert.equal(previewButton.clickCount,beforePreview);assistant.children[1].children.pop();
 viewImage.currentSrc='blob:foreign';dialogs=[];await assert.rejects(()=>collect(path.join(temp,'foreign.png')),/Native viewer image does not match/);assert.equal(download.clickCount,beforeDownload);viewImage.currentSrc='blob:exact-preview';dialogs=[];
 partialDownload=true;await assert.rejects(()=>collect(path.join(temp,'partial.png'),{nativeDownloadTimeoutMs:30}),/did not complete/);assert.equal(download.clickCount,beforeDownload+1);partialDownload=false;
 console.log('actual Oracle collector opened one exact preview and saved its original PNG once');
}finally{await fs.rm(temp,{recursive:true,force:true});}
