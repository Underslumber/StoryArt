"""Strict retirement of the known pre-consult remote-desktop probe failure."""
import json
from pathlib import Path
KNOWN_BACKEND_SHA256='24b0d1ca70be55f74b6683723d2f7148333948d86990656ddd0c8f924b5934a7'

def verify_no_send(web,journal,manifest_snapshot,backend_snapshot):
    journal=Path(journal).resolve();state=web.load(journal);scope=journal.parent
    manifest_snapshot=Path(manifest_snapshot).resolve();backend_snapshot=Path(backend_snapshot).resolve()
    if not manifest_snapshot.is_relative_to(scope) or not backend_snapshot.is_relative_to(scope):
        raise ValueError('Snapshots leave current request')
    manifest_raw=manifest_snapshot.read_bytes();backend_raw=backend_snapshot.read_bytes()
    if (state['status']!='UNKNOWN' or state.get('session_id') or state.get('output')
        or web.digest(manifest_raw)!=state.get('runtime_binding',{}).get('manifest_sha256')
        or web.digest(backend_raw)!=KNOWN_BACKEND_SHA256):
        raise ValueError('Known pre-consult source/binding not proved')
    manifest=json.loads(manifest_raw)
    backend_path=Path(web.__file__).with_name('chatgpt_web_image_backend.mjs').resolve()
    runtime_path=Path(web.__file__).with_name('chatgpt_web_image_runtime.js').resolve()
    pins={Path(row['path']).resolve():row['sha256'] for row in manifest['files']}
    if pins.get(backend_path)!=KNOWN_BACKEND_SHA256 or pins.get(runtime_path)!=web.digest(runtime_path.read_bytes()):
        raise ValueError('Reviewed consult fence source changed')
    home=Path(state['oracle_home']).resolve();op=state.get('submission_operation_id')
    if home.parent!=scope or not isinstance(op,str) or not web.re.fullmatch('[a-f0-9]{32}',op):
        raise ValueError('Original operation binding invalid')
    phase_path=home/f'transport-phase-{op}.jsonl';phase_raw=phase_path.read_bytes()
    phases=[json.loads(line) for line in phase_raw.decode().splitlines() if line]
    if (len(phases)!=2 or [r.get('phase') for r in phases]!=['CLIENT_CONNECTING','CLIENT_CONNECTED']
        or any(r.get('operation_id')!=op or r.get('prompt_sha256')!=state['prompt_sha256']
               or r.get('provider_call_started') is not False for r in phases)):
        raise ValueError('No-send fence history not proved')
    log_raw=(home/'transport.log').read_bytes();log=log_raw.decode('utf-8')
    if 'Error: Win32 desktop membership probe failed; no consult:' not in log or 'at verifyIsolatedDesktop' not in log:
        raise ValueError('Known pre-consult failure absent')
    if (home/'receipt.json').exists() or (home/'generated/result.png').exists() or Path(state['requested_output']).exists():
        raise ValueError('Provider receipt/output exists; reconcile original session')
    sessions=home/'sessions'
    if sessions.exists() and any(sessions.rglob('meta.json')):
        raise ValueError('Provider session exists; no replacement')
    receipts=list(home.glob('advisor-desktop-receipt-*.json'))
    if len(receipts)!=1:raise ValueError('Original broker receipt ambiguous')
    receipt=json.loads(receipts[0].read_bytes());exit_path=Path(str(receipts[0])+'.exit')
    exit_raw=exit_path.read_bytes();closed=json.loads(exit_raw)
    if (receipt.get('state')!='RUNNING' or closed.get('state')!='EXITED'
        or closed.get('workerPid')!=receipt.get('workerPid') or closed.get('desktop')!=receipt.get('desktop')
        or closed.get('exitCode')!=0 or closed.get('inputAfter')!='Default'):
        raise ValueError('Original MCP termination not proved')
    return {'schema_version':1,'status':'NO_SEND_VERIFIED','journal':str(journal),'operation_id':op,
      'attempt_id':state['scenario_evidence']['attempt']['attempt_id'],'prompt_sha256':state['prompt_sha256'],
      'manifest_sha256':web.digest(manifest_raw),'backend_sha256':web.digest(backend_raw),
      'phase_sha256':web.digest(phase_raw),'transport_sha256':web.digest(log_raw),'exit_sha256':web.digest(exit_raw),
      'snapshots':{'manifest':str(manifest_snapshot),'backend':str(backend_snapshot)}}

def record_no_send(web,journal,manifest_snapshot,backend_snapshot):
    proof=verify_no_send(web,journal,manifest_snapshot,backend_snapshot)
    state=web.load(journal);p=Path(state['oracle_home'])/'no-send-verified.json'
    if p.exists():
        if json.loads(p.read_bytes())!=proof:raise ValueError('Existing verdict changed')
    else:
        with p.open('x',encoding='utf-8') as f:
            json.dump(proof,f,ensure_ascii=False,indent=2);f.flush();web.os.fsync(f.fileno())
    state['no_send_verdict']={'path':str(p),'sha256':web.digest(p.read_bytes())}
    web.event(state,'UNKNOWN',reconciliation='NO_SEND_VERIFIED; original status/history preserved')
    web.save(journal,state)
    return proof

def validate_new_handoff(web,old_path,new_path,state,evidence):
    binding=state['no_send_verdict'];p=Path(binding['path'])
    if web.digest(p.read_bytes())!=binding['sha256']:raise ValueError('No-send verdict changed')
    proof=json.loads(p.read_bytes())
    verified=verify_no_send(web,old_path,proof['snapshots']['manifest'],proof['snapshots']['backend'])
    if proof!=verified or Path(old_path).resolve().parent!=Path(new_path).resolve().parent:
        raise ValueError('No-send handoff scope mismatch')
    old=state['scenario_evidence'];guard_path=Path(evidence['guard']['path'])
    if guard_path.resolve()!=Path(old['guard']['path']).resolve():raise ValueError('Original guard changed')
    guard=json.loads(guard_path.read_bytes())
    retired=next((a for a in guard['attempts'] if a['attempt_id']==proof['attempt_id']),None)
    active=guard.get('active_attempt') or {}
    if (not retired or retired['status']!='REFUSED' or not retired.get('refusal_receipt')
        or Path(retired['refusal_receipt']).resolve()!=p.resolve()
        or active.get('attempt_id')!=evidence['attempt']['attempt_id']
        or guard['request_id']!=old['attempt']['request_id']):
        raise ValueError('Known no-send operator retirement not proved')
