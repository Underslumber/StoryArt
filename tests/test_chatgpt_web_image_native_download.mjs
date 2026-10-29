import assert from 'node:assert/strict';
import vm from 'node:vm';
import {deflateSync} from 'node:zlib';
import {buildNativePreviewOpen, buildNativeViewerProbe, buildNativeDownloadClick, validateOriginalPng} from '../tools/chatgpt_web_image_native_download.mjs';

const runtime = {tabUrl:'https://chatgpt.com/g/g-p-abc/c/conv', ownedRecoveryTarget:{claimId:'claim'}};
const prompt = 'Create one image with a changing layout';
const image = {complete:true,naturalWidth:2048,naturalHeight:1024,currentSrc:'blob:generated',getAttribute:()=> 'blob:generated',getBoundingClientRect:()=>({width:100,height:100})};
const preview = {disabled:false,querySelectorAll:selector=>selector==='img'?[image]:[],querySelector:selector=>selector==='img'?image:null,closest:()=>null,getBoundingClientRect:()=>({width:100,height:100}),clickCount:0,click(){this.clickCount++;}};
const assistant = {querySelectorAll:selector=>selector==='button[data-testid="generated-image-preview"]'?[preview]:selector==='button[data-testid="generated-image-preview"] img'?[image]:[]};
const turns = [{role:'user',text:prompt},{role:'assistant',text:'',content:assistant}];
const download = {tagName:'BUTTON',disabled:false,getAttribute:name=>name==='aria-label'?'Download':null,textContent:'',clickCount:0,click(){this.clickCount++;}};
download.getBoundingClientRect=()=>({width:30,height:30});
let dialogs = [];
const dialog = {
  tagName:'DIV',
  getBoundingClientRect:()=>({width:800,height:700}),
  querySelectorAll:selector=>selector==='img'?[image]:selector==='button,[role="button"],a[download]'?[download]:[],
};
const main = {querySelectorAll:()=>[]};
const context = {
  window:{turns},
  location:{href:runtime.tabUrl},
  sessionStorage:{getItem:()=>JSON.stringify({id:'claim',retiring:false})},
  document:{querySelectorAll:selector=>selector==='main'?[main]:selector==='[role="dialog"]'?dialogs:[]},
  getComputedStyle:()=>({display:'block',visibility:'visible',opacity:'1'}),
};
const semantics = 'const oracleReadTurnRole=t=>t.role;const oracleReadTurnText=t=>t.text;const oracleReadTurnContentRoot=t=>t.content;';
function evaluate(expression) { return vm.runInNewContext(expression, context); }

const opened = evaluate(buildNativePreviewOpen(runtime,prompt,semantics,'window.turns'));
assert.deepEqual({...opened}, {ok:true,opened:true,width:2048,height:1024,src:'blob:generated'});
assert.equal(preview.clickCount,1);
const beforeViewer = evaluate(buildNativeViewerProbe(runtime,prompt,semantics,'window.turns'));
assert.equal(beforeViewer.allowed,true);
assert.equal(beforeViewer.previews[0].width,2048);
dialogs=[dialog];
const viewer = evaluate(buildNativeViewerProbe(runtime,prompt,semantics,'window.turns'));
assert.equal(viewer.dialogCount,1);
const clicked = evaluate(buildNativeDownloadClick(runtime,prompt,semantics,'window.turns'));
assert.equal(clicked.clicked,true);
assert.equal(clicked.width,2048);
assert.equal(download.clickCount,1);

function crc32(buf){let c=0xffffffff;for(const b of buf){c^=b;for(let i=0;i<8;i++)c=(c>>>1)^((c&1)?0xedb88320:0);}return (c^0xffffffff)>>>0;}
function chunk(name,data){const type=Buffer.from(name),out=Buffer.alloc(data.length+12);out.writeUInt32BE(data.length,0);type.copy(out,4);data.copy(out,8);out.writeUInt32BE(crc32(Buffer.concat([type,data])),8+data.length);return out;}
const ihdr=Buffer.alloc(13);ihdr.writeUInt32BE(2048,0);ihdr.writeUInt32BE(1024,4);ihdr[8]=8;ihdr[9]=6;
const png=Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',ihdr),chunk('IDAT',deflateSync(Buffer.alloc((2048*4+1)*1024))),chunk('IEND',Buffer.alloc(0))]);
assert.deepEqual(validateOriginalPng(png,{width:2048,height:1024}).width,2048);
assert.throws(()=>validateOriginalPng(png,{width:1448,height:1086}),/dimensions/);
assert.throws(()=>validateOriginalPng(png.subarray(0,png.length-4),{width:2048,height:1024}),/Truncated|end|structure/);

turns[0].text='A different request';
assert.equal(evaluate(buildNativePreviewOpen(runtime,prompt,semantics,'window.turns')).reason,'prompt-ambiguous');
turns[0].text=prompt;turns.push({role:'user',text:prompt},{role:'assistant',text:'duplicate',content:assistant});
assert.equal(evaluate(buildNativePreviewOpen(runtime,prompt,semantics,'window.turns')).reason,'prompt-ambiguous');
console.log('generic native preview, viewer, download and variable-size PNG fixtures passed');
