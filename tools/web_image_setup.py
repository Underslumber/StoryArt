"""Install and verify the private, project-local Web image runtime; no implicit browser launch."""
from __future__ import annotations
import argparse, base64, hashlib, json, os, pathlib, re, shutil, subprocess, sys, tarfile, urllib.request, zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
def sha(path): return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding='utf-8')
def settings(root=ROOT):
    file=root/'config/chatgpt_web_image.runtime.local.json'
    return json.loads(file.read_text()) if file.exists() else {}
def download(url, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as response, target.open('wb') as output: shutil.copyfileobj(response,output)
def resolve_node(local, explicit=None):
    candidates=[explicit, shutil.which('node')]
    candidates += [str(p) for p in (pathlib.Path.home()/'.cache/codex-runtimes').glob('*/dependencies/node/bin/node.exe')]
    for value in candidates:
        if value and pathlib.Path(value).is_file():
            version=subprocess.check_output([value,'--version'],text=True).strip()
            if int(version.lstrip('v').split('.')[0])>=24:return str(pathlib.Path(value).resolve())
    if sys.platform!='win32':raise ValueError('Node >=24 required; provide bundled runtime --node-path')
    version='v24.15.0'; name=f'node-{version}-win-x64.zip'; url=f'https://nodejs.org/dist/{version}/'
    archive=local/'prerequisites'/name;download(url+name,archive)
    with urllib.request.urlopen(url+'SHASUMS256.txt') as response: sums=response.read().decode()
    expected=next(line.split()[0] for line in sums.splitlines() if line.endswith(name))
    if sha(archive)!=expected:raise ValueError('Official Node checksum mismatch')
    with zipfile.ZipFile(archive) as package:package.extractall(archive.parent)
    return str(archive.parent/f'node-{version}-win-x64'/'node.exe')
def resolve_chrome(local):
    candidates=[os.environ.get('CHROME_PATH'),shutil.which('google-chrome'),shutil.which('chromium')]
    for base in ('PROGRAMFILES','PROGRAMFILES(X86)','LOCALAPPDATA'):
        if os.environ.get(base):candidates.append(str(pathlib.Path(os.environ[base])/'Google/Chrome/Application/chrome.exe'))
    for item in candidates:
        if item and pathlib.Path(item).is_file():return str(pathlib.Path(item).resolve())
    if sys.platform!='win32':raise ValueError('Supported Chrome executable missing')
    with urllib.request.urlopen('https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json') as response:data=json.load(response)
    url=next(x['url'] for x in data['channels']['Stable']['downloads']['chrome'] if x['platform']=='win64')
    archive=local/'prerequisites/chrome.zip';download(url,archive)
    with zipfile.ZipFile(archive) as package:package.extractall(archive.parent)
    return str(archive.parent/'chrome-win64/chrome.exe')
def bind_project(root,url,name):
    match=re.fullmatch(r'https://chatgpt\.com/g/(g-p-[a-f0-9]+)/project',url)
    if not match or not name or not name.strip():raise ValueError('Dedicated ChatGPT project URL and name required')
    write(root/'config/chatgpt_web_image.local.json',{'project_id':match[1],'project_url':url,'project_name':name})
def verify(root=ROOT):
    config=settings(root); manifest=pathlib.Path(config.get('manifest_path',''))
    if not manifest.is_file():raise ValueError('Runtime not installed; run setup-web-image.ps1')
    data=json.loads(manifest.read_text())
    if sha(manifest)!=config.get('manifest_sha256'):raise ValueError('Installed manifest changed')
    for item in data['files']:
        target=pathlib.Path(item['path'])
        if not target.is_file() or sha(target)!=item['sha256']:
            raise ValueError('Runtime source/dependency changed: '+item['path'])
    tree=pathlib.Path(data.get('dependency_tree_root',''))
    if not tree.is_dir():raise ValueError('Installed dependency tree is missing')
    expected=set(data.get('dependency_tree_files',[]))
    actual={path.relative_to(tree).as_posix() for path in tree.rglob('*') if path.is_file()}
    if actual!=expected:raise ValueError('Installed dependency tree contents changed')
    if int(subprocess.check_output([config['node_path'],'--version'],text=True).lstrip('v').split('.')[0])<24:raise ValueError('Node version changed')
    if not pathlib.Path(config.get('chrome_path','')).is_file():raise ValueError('Supported Chrome executable is unavailable')
    if not pathlib.Path(config.get('profile_dir','')).is_absolute():raise ValueError('Browser profile path must be absolute')
    return config
def doctor(root=ROOT):
    result={'authentication':'NOT_CHECKED','prerequisites':[]}
    try:verify(root);result['runtime']='VERIFIED'
    except (ValueError,OSError,KeyError) as error:result.update(runtime='SETUP_REQUIRED',reason=str(error))
    if not (root/'config/chatgpt_web_image.local.json').is_file():
        result['prerequisites'].append('PROJECT')
    else:
        try:
            route=json.loads((root/'config/chatgpt_web_image.local.json').read_text())
            project_id=route.get('project_id','')
            if (not re.fullmatch(r'g-p-[a-f0-9]+',project_id)
                    or route.get('project_url')!=f'https://chatgpt.com/g/{project_id}/project'
                    or not route.get('project_name','').strip()):
                raise ValueError('Dedicated ChatGPT project URL and name required')
            result['project']='BOUND'
        except (OSError, ValueError, KeyError, TypeError) as error:
            result['project']='INVALID';result['project_reason']=str(error)
    result['prerequisites'].append('LOGIN')
    return result
def install(root=ROOT,node_path=None,profile_dir=None):
    local=root/'.local/chatgpt-web-image';local.mkdir(parents=True,exist_ok=True)
    source=root/'runtime/chatgpt-web-image'; previous=settings(root)
    node=resolve_node(local,node_path or previous.get('node_path')); installation=local/'installation';installation.mkdir(exist_ok=True)
    for name in ('package.json','package-lock.json'):
        if not (source/name).is_file():
            raise ValueError(f'Pinned runtime asset is missing: {source/name}')
        shutil.copyfile(source/name,installation/name)
    npm=pathlib.Path(node).parent/'node_modules/npm/bin/npm-cli.js'
    if not npm.exists():
        archive=local/'prerequisites/npm.tgz';download('https://registry.npmjs.org/npm/-/npm-11.11.0.tgz',archive)
        with urllib.request.urlopen('https://registry.npmjs.org/npm/11.11.0') as response: metadata=json.load(response)
        expected=metadata.get('dist',{}).get('integrity','')
        actual='sha512-'+base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode('ascii')
        if not expected or actual!=expected:raise ValueError('Official npm checksum mismatch')
        target=local/'prerequisites/npm';target.mkdir(exist_ok=True)
        with tarfile.open(archive) as package:package.extractall(target,filter='data')
        npm=target/'package/bin/npm-cli.js'
    env=dict(os.environ,PATH=str(pathlib.Path(node).parent)+os.pathsep+os.environ.get('PATH',''))
    subprocess.run([node,str(npm),'ci','--ignore-scripts','--no-audit','--no-fund'],cwd=installation,env=env,check=True)
    vendor=installation/'node_modules/@steipete/oracle';patch=json.loads((source/'oracle-patches.json').read_text())
    recorded=[]
    for item in patch['files']:
        payload=source/'oracle-patches'/item['path'];destination=vendor/item['path']
        if sha(payload)!=item['patched_sha256']:raise ValueError('Shipped patch changed')
        if item['upstream_sha256'] and sha(destination)!=item['upstream_sha256']:raise ValueError('Oracle upstream differs')
        destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(payload,destination)
        recorded.extend([payload,destination])
    broker={}
    if sys.platform=='win32':
        csc=pathlib.Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        for name in ('DesktopBroker','DesktopBrokerSession'):
            src=source/'broker'/f'{name}.cs';binary=local/f'{name}.exe'
            subprocess.run([str(csc),'/nologo','/target:exe','/reference:System.Web.Extensions.dll',f'/out:{binary}',str(src)],check=True)
            recorded.extend([src,binary]);broker[name]={'path':str(binary),'sha256':sha(binary),'allowDisconnectedSession':name.endswith('Session')}
    chrome=previous.get('chrome_path') or resolve_chrome(local)
    profile=profile_dir or previous.get('profile_dir') or str(local/'profile')
    if not pathlib.Path(profile).is_absolute():raise ValueError('Profile path must be absolute')
    recorded.extend(path for path in source.rglob('*') if path.is_file())
    # Pin the complete executable dependency tree, not only patched Oracle files.
    # This is rechecked before every backend operation by verify().
    recorded.extend(path for path in installation.rglob('*') if path.is_file())
    recorded.extend(path for pattern in ('chatgpt_web_image*.py','chatgpt_web_image*.js','chatgpt_web_image*.mjs','web_image_setup.py')
                    for path in (root/'tools').glob(pattern))
    recorded += [pathlib.Path(node),root/'scripts/setup-web-image.ps1']
    recorded=[p for p in recorded if p.is_file()]
    manifest=local/'installation-manifest.json'
    dependency_tree=installation/'node_modules'
    dependency_files=sorted(path.relative_to(dependency_tree).as_posix() for path in dependency_tree.rglob('*') if path.is_file())
    write(manifest,{'version':1,'oracle':'0.21.1','client':'2.0.0','brokers':broker,
                    'dependency_tree_root':str(dependency_tree.resolve()),'dependency_tree_files':dependency_files,
                    'files':[{'path':str(p.resolve()),'sha256':sha(p)} for p in recorded]})
    config={'installation':str(installation),'node_path':node,'profile_dir':profile,'chrome_path':chrome,'manifest_path':str(manifest),'manifest_sha256':sha(manifest)}
    write(root/'config/chatgpt_web_image.runtime.local.json',config);return config
def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['install','doctor','bind-project','login']);parser.add_argument('--workspace',default=str(ROOT));parser.add_argument('--node-path');parser.add_argument('--profile-dir');parser.add_argument('--project-url');parser.add_argument('--project-name')
    args=parser.parse_args();root=pathlib.Path(args.workspace).resolve()
    if args.command=='doctor':result=doctor(root)
    elif args.command=='bind-project':bind_project(root,args.project_url,args.project_name);result=doctor(root)
    elif args.command=='install':
        install(root,args.node_path,args.profile_dir)
        if args.project_url:bind_project(root,args.project_url,args.project_name)
        result=doctor(root)
    else:
        config=verify(root);route_path=root/'config/chatgpt_web_image.local.json'
        route=json.loads(route_path.read_text()) if route_path.is_file() else {}
        # This command is ONLY invoked after explicit human -Login authorization.
        destination=route.get('project_url','https://chatgpt.com/')
        subprocess.Popen([config['chrome_path'],'--user-data-dir='+config['profile_dir'],destination],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));result={'authentication':'HUMAN_LOGIN_OPENED','destination':'PROJECT' if route.get('project_url') else 'CHATGPT_HOME'}
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
