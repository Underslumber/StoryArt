"""Local, persistent author classification of hash-bound unresolved pose subjects."""
from __future__ import annotations
import argparse,hashlib,json,secrets,sys
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from threading import Lock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
COLLECTION='WAVESPEED_POSES_SOURCE_COLLECTION'
DEFAULT_STATE=ROOT/'work/wavespeed-subject-correction-20261115/AUTHOR_CHOICES.json'
LABELS={'MALE','FEMALE','NEUTRAL','EXCLUDE'}

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text('utf-8'))
def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n');temp.replace(path)

def pending(workspace):
    manifest=workspace/COLLECTION/'POSES.json'
    result=[]
    for row in read(manifest)['records']:
        people=row.get('participants',[])
        if people:
            slots=[{'participant_id':p['participant_id'],'locator':p['locator']} for p in people if p['subject_label']=='UNKNOWN' or row.get('author_review_requested')]
        else:
            slots=[{'participant_id':'SUBJECT','locator':'Единственный участник'}] if row['subject_label']=='UNKNOWN' or row.get('author_review_requested') else []
        if slots:result.append({'id':row['id'],'group_id':row['group_id'],'sha256':row['sha256'],'title':row['title'],'angle':row['angle'],
                                'representation':row['representation'],'slots':slots,'participants':people,
                                'anatomy':row['anatomy_qa_status'],'relative_path':row['relative_path']})
    return result

def initial(workspace):
    rows=pending(workspace)
    return {'schema_version':1,'kind':'WAVESPEED_AUTHOR_SUBJECT_CHOICES','revision':0,'completed':False,
            'items':rows,'answers':{}}

def validate_answers(workspace,state,answers):
    if not isinstance(answers,dict):raise ValueError('Answers must be an object')
    items={r['id']:r for r in state['items']}
    current={r['id']:r for r in read(workspace/COLLECTION/'POSES.json')['records']}
    for ident,answer in answers.items():
        if ident not in items:raise ValueError('Unknown review item')
        source=items[ident]
        if answer.get('sha256')!=source['sha256'] or current[ident]['sha256']!=source['sha256']:
            raise ValueError('Source hash changed: '+ident)
        path=(workspace/COLLECTION/current[ident]['relative_path']).resolve()
        path.relative_to((workspace/COLLECTION).resolve())
        if sha(path)!=source['sha256']:raise ValueError('Original bytes changed: '+ident)
        expected={s['participant_id'] for s in source['slots']}
        current_people={p['participant_id']:p for p in current[ident].get('participants',[])}
        if current_people and any(s['participant_id'] not in current_people or current_people[s['participant_id']]['locator']!=s['locator'] for s in source['slots']):
            raise ValueError('Participant locator changed: '+ident)
        choices=answer.get('choices',{})
        if not isinstance(choices,dict) or not set(choices)<=expected or any(v not in LABELS for v in choices.values()):
            raise ValueError('Invalid participant choice: '+ident)
    return answers

def complete(state,answers):
    return all(all(s['participant_id'] in answers.get(row['id'],{}).get('choices',{}) for s in row['slots']) for row in state['items'])

def apply(workspace,state,allow_partial=False):
    from tools.wavespeed_pose_library import apply_reviews,finalize_curation,save_json
    from tools.wavespeed_pose_pool import subject_confirmed
    answers=validate_answers(workspace,state,state['answers'])
    if not allow_partial and (not state.get('completed') or not complete(state,answers)):
        raise ValueError('Author classification is not finished; no labels applied')
    root=workspace/COLLECTION;manifest=read(root/'POSES.json');ledger=read(root/'SUBJECT_REVIEWS.json')
    reviews={r['id']:r for r in ledger['reviews']};updated=0
    for row in manifest['records']:
        if row['id'] not in answers:continue
        answer=answers[row['id']];review=reviews[row['id']]
        excluded=False
        for slot,label in answer['choices'].items():
            if label=='EXCLUDE':excluded=True;continue
            evidence='Author selected '+label+' in the per-original review gallery; exact source SHA256 '+row['sha256']
            if slot=='SUBJECT':row['subject_label']=label;row['subject_label_evidence']=evidence
            else:
                person=next(p for p in row['participants'] if p['participant_id']==slot)
                person.update(subject_label=label,evidence=evidence,source_sha256=row['sha256'])
        row['subject_label_status']='CONFIRMED'
        row['curation_status']='READY' if not excluded and subject_confirmed(row) and row['anatomy_qa_status']=='PASS' and row['visual_qa_status']=='PASS' else 'EXCLUDED'
        row['curation_reason']='Author excluded source' if excluded else ('Author classification accepted; preserve recorded anatomy and support limits' if row['curation_status']=='READY' else 'Unresolved participant or existing anatomy failure remains')
        row['author_review_requested']=False
        for key in ('subject_label','subject_label_evidence','participants','curation_status','curation_reason','author_review_requested'):
            if key in row:review[key]=row[key]
        review['reviewer']='Author explicit classification; original connected-anatomy QA preserved'
        updated+=1
    # Apply all receipts atomically only after validating every answer/hash.
    receipt_hash=hashlib.sha256(json.dumps(ledger,sort_keys=True).encode()).hexdigest()
    apply_reviews(manifest,ledger,root,receipt_hash);finalize_curation(manifest,root)
    save_json(root/'SUBJECT_REVIEWS.json',ledger);save_json(root/'POSES.json',manifest)
    cfg=read(workspace/'config/reference_pools.json');cfg['pools']['WAVESPEED_POSES'].update(count=manifest['curation']['ready'],groups=manifest['curation']['ready_groups'])
    save(workspace/'config/reference_pools.json',cfg)
    return {'updated':updated,'curation':manifest['curation']}

def serve(workspace,state_path,port):
    state=read(state_path) if state_path.exists() else initial(workspace)
    groups={r['id']:r['group_id'] for r in read(workspace/COLLECTION/'POSES.json')['records']}
    for item in state['items']:item['group_id']=groups[item['id']]
    if not state_path.exists():save(state_path,state)
    token=secrets.token_urlsafe(24);lock=Lock()
    template=(workspace/'tools/wavespeed_pose_review.html').read_text('utf-8')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,status,payload,kind='application/json; charset=utf-8'):
            body=payload if isinstance(payload,bytes) else json.dumps(payload,ensure_ascii=False).encode()
            self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(body)
        def valid_host(self):return self.headers.get('Host')==f'127.0.0.1:{port}'
        def do_GET(self):
            if not self.valid_host():return self.reply(403,{'error':'Invalid host'})
            if self.path=='/':return self.reply(200,template.replace('__TOKEN__',token).encode(),'text/html; charset=utf-8')
            if self.path=='/api/state':return self.reply(200,state)
            if self.path.startswith('/image/'):
                ident=self.path.removeprefix('/image/')
                item=next((r for r in state['items'] if r['id']==ident),None)
                if not item:return self.reply(404,{'error':'Unknown review source'})
                source=(workspace/COLLECTION/item['relative_path']).resolve()
                source.relative_to((workspace/COLLECTION).resolve())
                if sha(source)!=item['sha256']:return self.reply(409,{'error':'Source hash differs'})
                return self.reply(200,source.read_bytes(),'image/webp')
            return self.reply(404,{'error':'Not found'})
        def do_POST(self):
            if not self.valid_host() or self.path!='/api/save' or self.headers.get('X-Review-Token')!=token:
                return self.reply(403,{'error':'Invalid review request'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<2_000_000:raise ValueError('Invalid payload size')
                request=json.loads(self.rfile.read(size))
                with lock:
                    if request.get('revision')!=state['revision']:return self.reply(409,{'error':'Answers changed in another tab; reload before editing'})
                    answers=validate_answers(workspace,state,request['answers'])
                    done=bool(request.get('completed'))
                    if done and not complete(state,answers):raise ValueError('Not every participant has a choice')
                    candidate={**state,'answers':answers,'completed':done,'revision':state['revision']+1}
                    save(state_path,candidate);state.update(candidate)
                self.reply(200,{'revision':state['revision'],'completed':done})
            except (ValueError,KeyError,OSError) as error:self.reply(400,{'error':str(error)})
    print(json.dumps({'url':f'http://127.0.0.1:{port}/','images':len(state['items']),'choices':sum(len(r['slots']) for r in state['items']),'state':str(state_path)},ensure_ascii=False),flush=True)
    ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['serve','apply']);parser.add_argument('--workspace',type=Path,default=ROOT);parser.add_argument('--state',type=Path,default=DEFAULT_STATE);parser.add_argument('--port',type=int,default=18843);parser.add_argument('--allow-partial',action='store_true');args=parser.parse_args()
    if args.command=='serve':serve(args.workspace.resolve(),args.state.resolve(),args.port)
    else:print(json.dumps(apply(args.workspace.resolve(),read(args.state),args.allow_partial),indent=2))
if __name__=='__main__':main()
