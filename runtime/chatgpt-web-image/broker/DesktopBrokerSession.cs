using System;
using System.IO;
using System.Text;
using System.Runtime.InteropServices;
using System.Collections.Generic;
using System.Web.Script.Serialization;
using System.Diagnostics;
using System.Threading;

class DesktopBrokerSession {
 [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] struct SI { public uint cb; public string reserved,desktop,title; public uint x,y,xsize,ysize,xchars,ychars,fill,flags; public ushort show,reserved2; public IntPtr reservedPtr,input,output,error; }
 [StructLayout(LayoutKind.Sequential)] struct PI {public IntPtr process,thread;public uint pid,tid;}
 [StructLayout(LayoutKind.Sequential)] struct SIX {public SI info;public IntPtr attributes;}
 [StructLayout(LayoutKind.Sequential)] struct SA {public uint length;public IntPtr descriptor;[MarshalAs(UnmanagedType.Bool)]public bool inherit;}
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr CreateFileW(string path,uint access,uint share,ref SA sa,uint creation,uint flags,IntPtr template);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern uint GetFinalPathNameByHandleW(IntPtr h,StringBuilder path,uint capacity,uint flags);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true,EntryPoint="CreateProcessW")] static extern bool CreateProcessEx(string app,StringBuilder cmd,IntPtr pa,IntPtr ta,bool inherit,uint flags,IntPtr env,string cwd,ref SIX si,out PI pi);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool InitializeProcThreadAttributeList(IntPtr list,int count,int flags,ref IntPtr size);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool UpdateProcThreadAttribute(IntPtr list,uint flags,IntPtr key,IntPtr val,IntPtr size,IntPtr prev,IntPtr returned);
 [DllImport("kernel32.dll")] static extern void DeleteProcThreadAttributeList(IntPtr list);
 [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
 [DllImport("kernel32.dll")] static extern bool GetProcessHandleCount(IntPtr p,out uint count);
 [DllImport("kernel32.dll")] static extern IntPtr GetStdHandle(int n);
 [DllImport("kernel32.dll")] static extern bool SetStdHandle(int n,IntPtr h);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetHandleInformation(IntPtr h,out uint f);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool DuplicateHandle(IntPtr source,IntPtr h,IntPtr target,out IntPtr duplicate,uint access,bool inherit,uint options);
 [DllImport("user32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr CreateDesktopW(string n,IntPtr d,IntPtr m,uint f,uint a,IntPtr s);
 [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr h);
 [DllImport("user32.dll")] static extern IntPtr GetThreadDesktop(uint t);
 [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
 [DllImport("user32.dll",SetLastError=true)] static extern IntPtr OpenInputDesktop(uint f,bool inherit,uint access);
 [DllImport("user32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool GetUserObjectInformationW(IntPtr h,int i,StringBuilder b,uint size,out uint needed);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool CreateProcessW(string app,StringBuilder cmd,IntPtr pa,IntPtr ta,bool inherit,uint flags,IntPtr env,string cwd,ref SI si,out PI pi);
 [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr h,uint ms);
 [DllImport("kernel32.dll")] static extern bool GetExitCodeProcess(IntPtr h,out uint c);
 [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
 [DllImport("user32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr CreateWindowExW(uint ex,string cls,string title,uint style,int x,int y,int w,int h,IntPtr parent,IntPtr menu,IntPtr instance,IntPtr param);
 [DllImport("user32.dll")] static extern bool DestroyWindow(IntPtr h);
 delegate bool EnumProc(IntPtr h,IntPtr p);
 [DllImport("user32.dll")] static extern bool EnumDesktopWindows(IntPtr d,EnumProc cb,IntPtr p);
 static JavaScriptSerializer json=new JavaScriptSerializer();
 static string Name(IntPtr h){uint n;var b=new StringBuilder(512);if(h==IntPtr.Zero||!GetUserObjectInformationW(h,2,b,1024,out n))throw new Exception("desktop name: "+Marshal.GetLastWin32Error());return b.ToString();}
 public static string Quote(string s){if(s.Length>0&&s.IndexOfAny(new[]{' ','\t','\n','\v','"'})<0)return s;var b=new StringBuilder("\"");int sl=0;foreach(char c in s){if(c=='\\'){sl++;continue;}if(c=='"'){b.Append('\\',sl*2+1);b.Append(c);}else{b.Append('\\',sl);b.Append(c);}sl=0;}b.Append('\\',sl*2);return b.Append('"').ToString();}
 static PI Start(string exe,string[] args,string cwd,string desktop){
  if(String.IsNullOrEmpty(desktop))throw new Exception("explicit desktop required");
  var handles=new List<IntPtr>();IntPtr attrs=IntPtr.Zero,values=IntPtr.Zero;bool initialized=false;
  try{
   foreach(int n in new[]{-10,-11,-12}){var h=GetStdHandle(n);uint f;IntPtr du;if(h==IntPtr.Zero||h==new IntPtr(-1)||!GetHandleInformation(h,out f))throw new Exception("invalid standard handle "+n);if(!DuplicateHandle(GetCurrentProcess(),h,GetCurrentProcess(),out du,0,true,2))throw new Exception("DuplicateHandle failed");handles.Add(du);}
   IntPtr size=IntPtr.Zero;InitializeProcThreadAttributeList(IntPtr.Zero,1,0,ref size);if(size==IntPtr.Zero)throw new Exception("attribute sizing failed");attrs=Marshal.AllocHGlobal(size);if(!InitializeProcThreadAttributeList(attrs,1,0,ref size))throw new Exception("attribute init failed");initialized=true;
   values=Marshal.AllocHGlobal(IntPtr.Size*3);for(int i=0;i<3;i++)Marshal.WriteIntPtr(values,i*IntPtr.Size,handles[i]);if(!UpdateProcThreadAttribute(attrs,0,new IntPtr(0x20002),values,new IntPtr(IntPtr.Size*3),IntPtr.Zero,IntPtr.Zero))throw new Exception("handle allowlist failed");
   var sx=new SIX();sx.info.cb=(uint)Marshal.SizeOf(sx);sx.info.desktop=desktop;sx.info.flags=0x100;sx.info.input=handles[0];sx.info.output=handles[1];sx.info.error=handles[2];sx.attributes=attrs;PI p;var cmd=new StringBuilder(Quote(exe));foreach(var a in args)cmd.Append(' ').Append(Quote(a));if(cmd.Length>=32767)throw new Exception("command line exceeds limit");if(!CreateProcessEx(exe,cmd,IntPtr.Zero,IntPtr.Zero,true,0x08080000,IntPtr.Zero,cwd,ref sx,out p))throw new Exception("CreateProcess: "+Marshal.GetLastWin32Error());CloseHandle(p.thread);return p;
  }finally{if(initialized)DeleteProcThreadAttributeList(attrs);if(attrs!=IntPtr.Zero)Marshal.FreeHGlobal(attrs);if(values!=IntPtr.Zero)Marshal.FreeHGlobal(values);foreach(var h in handles)CloseHandle(h);}
 }
 [DllImport("wtsapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool WTSQuerySessionInformationW(IntPtr server,uint session,int info,out IntPtr buffer,out uint bytes);
 [DllImport("wtsapi32.dll")] static extern void WTSFreeMemory(IntPtr buffer);
 [DllImport("user32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr OpenDesktopW(string name,uint flags,bool inherit,uint access);
 class Snapshot {public bool inputReadable;public string inputName;public int inputError;public string threadDesktop;public int sessionId;public bool sessionQuerySucceeded;public int? connectState;public int? sessionQueryError;}
 static Snapshot Observe(){
  var s=new Snapshot();s.threadDesktop=Name(GetThreadDesktop(GetCurrentThreadId()));s.sessionId=Process.GetCurrentProcess().SessionId;
  var h=OpenInputDesktop(0,false,0x0041);s.inputError=h==IntPtr.Zero?Marshal.GetLastWin32Error():0;
  try{if(h!=IntPtr.Zero){s.inputName=Name(h);s.inputReadable=true;}}finally{if(h!=IntPtr.Zero)CloseDesktop(h);}
  IntPtr b=IntPtr.Zero;uint bytes;bool ok=WTSQuerySessionInformationW(IntPtr.Zero,(uint)s.sessionId,8,out b,out bytes);int err=ok?0:Marshal.GetLastWin32Error();
  try{if(ok&&b!=IntPtr.Zero&&bytes==4){s.sessionQuerySucceeded=true;s.connectState=Marshal.ReadInt32(b);}else{s.sessionQueryError=ok?-1:err;}}finally{if(b!=IntPtr.Zero)WTSFreeMemory(b);}return s;
 }
 // Pure fail-closed decision. This method never creates a desktop or a process.
 static void Check(Snapshot s,bool allow,string expected,string created){
  if(s.inputReadable){if(s.inputName!=expected || (created!=null&&s.inputName==created))throw new Exception("input desktop mismatch");return;}
  if(!allow || s.inputError!=5 || !s.sessionQuerySucceeded || s.connectState!=4 || expected!="Default" || (s.threadDesktop!="Default" && (created==null || s.threadDesktop!=created)))throw new Exception("unreadable input desktop rejected before launch/postcheck");
 }
 static bool IsIsolatedName(string name){Guid g;return name!=null&&name.StartsWith("OracleIsolated_",StringComparison.Ordinal)&&name.Length==47&&Guid.TryParseExact(name.Substring(15),"N",out g);}
 static string FixtureDesktop(){string desktop=Name(GetThreadDesktop(GetCurrentThreadId()));if(!IsIsolatedName(desktop))throw new Exception("fixture refuses non-isolated desktop before any GUI or child launch");return desktop;}
 static int Decisions(){
  int rejected=0;var good=new Snapshot{inputReadable=false,inputError=5,threadDesktop="Default",sessionQuerySucceeded=true,connectState=4};Check(good,true,"Default",null);
  Action<Snapshot,bool,string> reject=(s,a,e)=>{try{Check(s,a,e,null);}catch{rejected++;return;}throw new Exception("unsafe decision accepted");};
  reject(good,false,"Default");reject(good,true,"Winlogon");
  reject(new Snapshot{inputError=5,threadDesktop="Other",sessionQuerySucceeded=true,connectState=4},true,"Default");
  reject(new Snapshot{inputError=5,threadDesktop="Default",sessionQuerySucceeded=false,connectState=4},true,"Default");
  reject(new Snapshot{inputError=6,threadDesktop="Default",sessionQuerySucceeded=true,connectState=4},true,"Default");
  for(int state=0;state<=9;state++)if(state!=4)reject(new Snapshot{inputError=5,threadDesktop="Default",sessionQuerySucceeded=true,connectState=state},true,"Default");
  Check(new Snapshot{inputReadable=true,inputName="Default"},false,"Default",null);
  reject(new Snapshot{inputReadable=true,inputName="Winlogon"},true,"Default");
  string isolated="OracleIsolated_"+Guid.NewGuid().ToString("N");Check(new Snapshot{inputError=5,threadDesktop=isolated,sessionQuerySucceeded=true,connectState=4},true,"Default",isolated);
  Check(new Snapshot{inputReadable=true,inputName="Default",threadDesktop=isolated},true,"Default",isolated);
  if(!IsIsolatedName(isolated)||IsIsolatedName("Default")||IsIsolatedName("OracleIsolated_not-a-guid"))throw new Exception("name decision failed");
  Console.WriteLine(json.Serialize(new{status="PASS",rejected=rejected,windowsCreated=0,processesCreated=0}));return 0;
 }
 static string Input(){var h=OpenInputDesktop(0,false,0x0041);try{return Name(h);}finally{if(h!=IntPtr.Zero)CloseDesktop(h);}}
 static void Write(string p,object v){File.WriteAllText(p,json.Serialize(v),new UTF8Encoding(false));}
 static int ExtraFixture(string[] args){string mode=args[0],receipt=args[1],desktop=FixtureDesktop();
  if(mode=="fixture-stdio"){var input=Console.OpenStandardInput();var output=Console.OpenStandardOutput();byte[] b=new byte[4096];int n;while((n=input.Read(b,0,b.Length))>0)output.Write(b,0,n);output.Flush();byte[] e=Encoding.UTF8.GetBytes("stderr:Ж雪\n");var err=Console.OpenStandardError();err.Write(e,0,e.Length);err.Flush();Write(receipt,new{desktop=desktop,argv=args,pid=Process.GetCurrentProcess().Id});return 0;}
  if(mode=="fixture-exit")return 7;
  if(mode=="fixture-foreign-child"){var path=new StringBuilder(2048);uint n=GetFinalPathNameByHandleW(new IntPtr(Int64.Parse(args[2])),path,2048,0);bool same=n>0&&n<2048&&String.Equals(path.ToString().Replace("\\\\?\\", ""),Path.GetFullPath(args[3]),StringComparison.OrdinalIgnoreCase);Write(receipt,new{foreignExactFileInherited=same,resolved=n>0?path.ToString():null,desktop=desktop});if(same)throw new Exception("foreign handle inherited");return 0;}
  if(mode=="fixture-foreign"){string path=Path.GetFullPath(receipt+".foreign-file");var sa=new SA();sa.length=(uint)Marshal.SizeOf(sa);sa.inherit=true;var file=CreateFileW(path,0x80000000,7,ref sa,2,0x80,IntPtr.Zero);if(file==new IntPtr(-1))throw new Exception("foreign file open failed");try{var p=Start(Process.GetCurrentProcess().MainModule.FileName,new[]{"fixture-foreign-child",receipt+".child",file.ToInt64().ToString(),path},Environment.CurrentDirectory,desktop);try{if(WaitForSingleObject(p.process,5000)!=0)throw new Exception("foreign child timeout");uint c;GetExitCodeProcess(p.process,out c);if(c!=0)throw new Exception("foreign child failed");Write(receipt,new{foreignInheritableHandleExcluded=true,desktop=desktop});return 0;}finally{CloseHandle(p.process);}}finally{CloseHandle(file);}}
  if(mode=="fixture-handles"){var warm=Start(Process.GetCurrentProcess().MainModule.FileName,new[]{"fixture-exit",receipt+".unused"},Environment.CurrentDirectory,desktop);WaitForSingleObject(warm.process,5000);CloseHandle(warm.process);uint before,after;GetProcessHandleCount(GetCurrentProcess(),out before);for(int i=0;i<10;i++){var p=Start(Process.GetCurrentProcess().MainModule.FileName,new[]{"fixture-exit",receipt+".unused"},Environment.CurrentDirectory,desktop);try{if(WaitForSingleObject(p.process,5000)!=0)throw new Exception("handle fixture timeout");}finally{CloseHandle(p.process);}}GetProcessHandleCount(GetCurrentProcess(),out after);if(after!=before)throw new Exception("handle count changed "+before+" -> "+after);Write(receipt,new{before=before,after=after,iterations=10});return 0;}
  if(mode=="fixture-invalid-handles"){CloseHandle(GetStdHandle(-10));SetStdHandle(-10,IntPtr.Zero);bool rejected=false;try{var p=Start(Process.GetCurrentProcess().MainModule.FileName,new[]{"fixture-exit",receipt+".unused"},Environment.CurrentDirectory,desktop);CloseHandle(p.process);}catch(Exception e){rejected=e.Message.StartsWith("invalid standard handle");}if(!rejected)throw new Exception("invalid stdin accepted");Write(receipt,new{rejectedBeforeLaunch=true});return 0;}
  if(mode=="fixture-detached-child"){Thread.Sleep(1500);Write(receipt,new{desktop=desktop,pid=Process.GetCurrentProcess().Id});return 0;}
  if(mode=="fixture-detach"){var p=Start(Process.GetCurrentProcess().MainModule.FileName,new[]{"fixture-detached-child",receipt+".detached"},Environment.CurrentDirectory,desktop);Write(receipt,new{desktop=desktop,childPid=p.pid});CloseHandle(p.process);return 0;}
  throw new Exception("unknown fixture");
 }
 static int Main(string[] args){try{
  if(args.Length==1&&args[0]=="decision-tests")return Decisions();
  if(args.Length>=2&&args[0].StartsWith("fixture-")&&args[0]!="fixture-child")return ExtraFixture(args);
  if(args.Length>=2&&args[0]=="fixture")return Fixture(args[1],false);
  if(args.Length>=2&&args[0]=="fixture-child")return Fixture(args[1],true);
  if(args.Length!=1)throw new Exception("Usage: DesktopBrokerSession request.json");
  var r=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(args[0]));
  string exe=Path.GetFullPath((string)r["executable"]),cwd=Path.GetFullPath((string)r["cwd"]),receipt=Path.GetFullPath((string)r["receipt"]),expected=(string)r["expectedInputDesktop"];
  if(!File.Exists(exe)||!Directory.Exists(cwd))throw new Exception("invalid executable/cwd");
  bool allow=false;if(r.ContainsKey("allowDisconnectedSession")){if(!(r["allowDisconnectedSession"] is bool))throw new Exception("allowDisconnectedSession must be boolean");allow=(bool)r["allowDisconnectedSession"];}
  var before=Observe();Check(before,allow,expected,null);
  var av=new List<string>();foreach(var a in (System.Collections.ArrayList)r["argv"])av.Add((string)a);
  string name="OracleIsolated_"+Guid.NewGuid().ToString("N");
  var d=CreateDesktopW(name,IntPtr.Zero,IntPtr.Zero,0,0x01FF,IntPtr.Zero);if(d==IntPtr.Zero)throw new Exception("CreateDesktop: "+Marshal.GetLastWin32Error());
  try{
   string actual=Name(d);if(actual!=name || !IsIsolatedName(actual) || actual=="Default" || actual==before.inputName)throw new Exception("created desktop identity mismatch");
   // Recheck immediately before launch, including a possible session transition during creation.
   var prelaunch=Observe();Check(prelaunch,allow,expected,name);
   var p=Start(exe,av.ToArray(),cwd,name);try{
    Write(receipt,new{workerPid=p.pid,desktop=name,inputBefore=before.inputName,inputReadable=before.inputReadable,inputError=before.inputError,threadDesktop=before.threadDesktop,sessionId=before.sessionId,sessionQuerySucceeded=before.sessionQuerySucceeded,connectState=before.connectState,sessionQueryError=before.sessionQueryError,allowDisconnectedSession=allow,prelaunch=prelaunch,state="RUNNING",inheritedHandleCount=3});
    uint timeout=r.ContainsKey("timeoutMs")?Convert.ToUInt32(r["timeoutMs"]):0xffffffff;
    uint wait=WaitForSingleObject(p.process,timeout);if(wait!=0){Write(receipt+".exit",new{state=wait==258?"TIMEOUT_PROCESS_PRESERVED":"WAIT_FAILED",workerPid=p.pid});throw new Exception("wait incomplete; process preserved, no fallback");}
    uint c;if(!GetExitCodeProcess(p.process,out c))throw new Exception("exit code unavailable");
    var after=Observe();string failure=null;try{Check(after,allow,expected,name);}catch(Exception e){failure=e.Message;}
    Write(receipt+".exit",new{workerPid=p.pid,desktop=name,inputBefore=before.inputName,inputAfter=after.inputName,inputReadable=after.inputReadable,observation=after,exitCode=c,state=failure==null?"EXITED":"POSTCHECK_FAILED",postcheckError=failure});
    if(failure!=null)throw new Exception(failure);return (int)c;
   }finally{CloseHandle(p.process);}
  }finally{CloseDesktop(d);}
 }catch(Exception e){Console.Error.WriteLine(e.Message);return 1;}}
 static bool Contains(IntPtr d,IntPtr window){bool yes=false;EnumProc cb=(h,p)=>{if(h==window)yes=true;return true;};if(!EnumDesktopWindows(d,cb,IntPtr.Zero))throw new Exception("EnumDesktopWindows failed");return yes;}
 static int Fixture(string receipt,bool child){
  string desktop=FixtureDesktop();
  var observed=Observe();Check(observed,true,"Default",desktop);
  // Opening Default read-only is a prerequisite for membership proof when input is unreadable.
  var defaultDesk=OpenDesktopW("Default",0,false,0x0041);if(defaultDesk==IntPtr.Zero)throw new Exception("fixture cannot inspect Default before GUI: "+Marshal.GetLastWin32Error());
  try{
   var w=CreateWindowExW(0,"STATIC","Neutral desktop fixture",0x10000000,0,0,100,100,IntPtr.Zero,IntPtr.Zero,IntPtr.Zero,IntPtr.Zero);if(w==IntPtr.Zero)throw new Exception("fixture window failed");
   try{
    var own=GetThreadDesktop(GetCurrentThreadId());if(!Contains(own,w)||Contains(defaultDesk,w))throw new Exception("window isolation failed");
    bool? inputMember=null;var input=OpenInputDesktop(0,false,0x0041);int inputError=input==IntPtr.Zero?Marshal.GetLastWin32Error():0;
    try{if(input!=IntPtr.Zero){if(desktop==Name(input))throw new Exception("fixture active desktop");inputMember=Contains(input,w);if(inputMember==true)throw new Exception("window is on input desktop");}else if(inputError!=5)throw new Exception("fixture input query failed");}finally{if(input!=IntPtr.Zero)CloseDesktop(input);}
    if(!child){
     var exe=Process.GetCurrentProcess().MainModule.FileName;var psi=new ProcessStartInfo(exe,Quote("fixture-child")+" "+Quote(receipt+".child"));psi.UseShellExecute=false;psi.CreateNoWindow=true;
     var automatic=Process.Start(psi);try{if(!automatic.WaitForExit(7000))throw new Exception("automatic child timeout");if(automatic.ExitCode!=0)throw new Exception("automatic child failure");}finally{automatic.Dispose();}
     var cr=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(receipt+".child"));if((string)cr["desktop"]!=desktop)throw new Exception("child inheritance failed");
    }
    Write(receipt,new{desktop=desktop,hwnd=w.ToInt64(),ownDesktopMember=true,defaultDesktopMember=false,inputDesktopMember=inputMember,inputReadable=input!=IntPtr.Zero,inputError=inputError,child=child});Thread.Sleep(1000);return 0;
   }finally{DestroyWindow(w);}
  }finally{CloseDesktop(defaultDesk);}
 }
}
