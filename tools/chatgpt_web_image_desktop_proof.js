/* Synchronous startup proof for the Oracle MCP process launched on the isolated desktop. No browser or provider call. */
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
try {
  const home = process.env.ORACLE_HOME_DIR;
  if (!home || !path.isAbsolute(home)) throw Error('Private Oracle home missing');
  let launch, receipt;
  for (let attempt = 0; attempt < 60 && !receipt; attempt++) {
    for (const name of fs.readdirSync(home).filter(n => /^advisor-desktop-launch-[a-f0-9-]+\.json$/.test(n))) {
      const item = JSON.parse(fs.readFileSync(path.join(home,name),'utf8'));
      try {
        const candidate = JSON.parse(fs.readFileSync(item.receipt,'utf8'));
        if (candidate.workerPid === process.pid) { launch=item; receipt=candidate; break; }
      } catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
    if (!receipt) Atomics.wait(new Int32Array(new SharedArrayBuffer(4)),0,0,50);
  }
  if (!receipt || receipt.state !== 'RUNNING' || !/^OracleIsolated_[a-f0-9]{32}$/.test(receipt.desktop) ||
      !/^[a-f0-9-]{36}$/.test(launch.launchId) || !/^[a-f0-9]{32}$/.test(launch.submissionOperationId))
    throw Error('Isolated launch identity missing');
  const script = String.raw`$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
Add-Type -TypeDefinition @'
using System;using System.Text;using System.Runtime.InteropServices;
public static class OwnDesktop {
 [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
 [DllImport("user32.dll",SetLastError=true)] public static extern IntPtr GetThreadDesktop(uint id);
 [DllImport("user32.dll",SetLastError=true)] public static extern IntPtr OpenInputDesktop(uint flags,bool inherit,uint access);
 [DllImport("user32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool GetUserObjectInformationW(IntPtr h,int index,StringBuilder b,uint size,out uint needed);
 [DllImport("user32.dll")] public static extern bool CloseDesktop(IntPtr h);
 public static string Name(IntPtr h){uint n;var b=new StringBuilder(512);if(h==IntPtr.Zero||!GetUserObjectInformationW(h,2,b,1024,out n))throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());return b.ToString();}
}
'@
$ownName=[OwnDesktop]::Name([OwnDesktop]::GetThreadDesktop([OwnDesktop]::GetCurrentThreadId()))
$inputHandle=[OwnDesktop]::OpenInputDesktop(0,$false,0x0041)
try{$inputName=[OwnDesktop]::Name($inputHandle)}finally{if($inputHandle -ne [IntPtr]::Zero){[OwnDesktop]::CloseDesktop($inputHandle)|Out-Null}}
$selfInfo=Get-CimInstance Win32_Process -Filter ('ProcessId='+$PID)
[pscustomobject]@{probePid=$PID;parentPid=$selfInfo.ParentProcessId;sessionId=(Get-Process -Id $PID).SessionId;desktop=$ownName;inputDesktop=$inputName}|ConvertTo-Json -Compress`;
  const probe=spawnSync('powershell.exe',['-NoProfile','-NonInteractive','-EncodedCommand',Buffer.from(script,'utf16le').toString('base64')],{encoding:'utf8',windowsHide:true,timeout:15000});
  if (probe.error || probe.status !== 0) throw Error(`Inherited desktop probe failed: ${probe.stderr || probe.error?.message}`);
  const observed=JSON.parse(probe.stdout.trim());
  if (observed.probePid !== probe.pid || observed.parentPid !== process.pid || observed.sessionId !== receipt.sessionId ||
      observed.desktop !== receipt.desktop || observed.inputDesktop !== 'Default') throw Error('Inherited desktop proof mismatch');
  const proof={schema_version:1,launch_id:launch.launchId,operation_id:launch.submissionOperationId,worker_pid:process.pid,...observed};
  const fd=fs.openSync(launch.receipt+'.process-proof.json','wx');
  try {fs.writeFileSync(fd,JSON.stringify(proof));fs.fsyncSync(fd);} finally {fs.closeSync(fd);}
} catch (error) {console.error(String(error.stack || error));process.exit(1);}
