"""Hash-bound discovery and soft-pose selection for the WaveSpeed library."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

POOL = 'WAVESPEED_POSES'
ROLES = {'AUX_POSE', 'AUX_CAMERA'}
CATEGORY_RU = {
    'Standing':'стоя', 'Fashion':'мода одежда', 'Glamour & pin-up':'гламур пинап',
    'Boudoir':'будуар', 'Couples':'парные позы', 'Everyday':'бытовые повседневные',
    'Sitting on a chair':'сидя на стуле', 'Sitting on the floor':'сидя на полу',
    'Kneeling & squatting':'на коленях присед', 'Lying down':'лёжа',
    'Upper body':'верх тела', 'Two people':'два человека взаимодействие',
    'Sports & action':'спорт действие', 'Dance':'танец',
    'Heroic & cinematic':'героические кинематографичные',
}
LIMITS = ('Only pose/supports/camera may transfer. Preserve target sex, identity, '
          'body proportions, clothing and rendering style. Publisher angles are '
          'separate reference images, not proof of a consistent 3D skeleton. '
          'Front/side/3-4 are publisher camera labels, not anatomical facing: '
          'a Front image can show the back of a turned figure.')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def locations(workspace):
    workspace = Path(workspace).resolve()
    config = json.loads((workspace/'config/reference_pools.json').read_text('utf-8'))['pools'][POOL]
    root = (workspace/config['root']).resolve()
    manifest = (workspace/config['manifest']).resolve()
    root.relative_to(workspace); manifest.relative_to(root)
    return root, manifest


def subject_confirmed(row):
    return (row.get('subject_label') in {'MALE', 'FEMALE'}
            and row.get('subject_label_status') == 'CONFIRMED'
            and row.get('subject_label_sha256') == row.get('sha256')
            and bool(row.get('subject_label_evidence'))
            and bool(row.get('subject_review_id')))


def ready(row):
    return (row.get('review_complete') is True and row.get('curation_status') == 'READY'
            and bool(row.get('curation_reason'))
            and subject_confirmed(row) and row.get('visual_qa_status') == 'PASS'
            and row.get('anatomy_qa_status') == 'PASS'
            and row.get('visual_review_sha256') == row.get('sha256')
            and row.get('viewed') == 'FULL_RESOLUTION'
            and bool(row.get('visual_review_evidence')) and bool(row.get('review_limitations')))


def records(workspace, include_excluded=False):
    root, manifest = locations(workspace)
    data = json.loads(manifest.read_text('utf-8'))
    result = []
    for row in data['records']:
        if not include_excluded and not ready(row):
            continue
        confirmed = subject_confirmed(row)
        result.append({
            'id': row['id'], 'title': row['title'], 'group_id': row['group_id'],
            'group_display': row['group_id']+' · '+row['title'],
            'angle': row['angle'], 'tags': [row['category'], CATEGORY_RU.get(row['category'],row['category']),
                                         row['representation'], '3D-манекен' if row['representation']=='3d' else 'контур'],
            'categories': ['POSE_ACTION'], 'category_basis': {'POSE_ACTION':['PUBLISHER_POSE_GALLERY']},
            'relative_path': row['relative_path'], 'sha256': row['sha256'],
            'subject_label': row.get('subject_label','UNKNOWN'),
            'subject_label_status': 'CONFIRMED' if confirmed else 'QUARANTINED',
            'record_binding_status': 'CURRENT',
            'role': '', 'review_state': row.get('visual_qa_status','NOT_CHECKED'),
            'provenance': {'post_url': data['source'], 'source_url': row['source_url'],
                           'review_notes': LIMITS, 'subject_label_evidence': row.get('subject_label_evidence',''),
                           'representation': row['representation']},
            'raw': row,
        })
    return root, result


def select(workspace, ident, role, subject):
    if role not in ROLES:
        raise ValueError('WaveSpeed permits only AUX_POSE or AUX_CAMERA; identity/body/style transfer forbidden')
    root, manifest = locations(workspace)
    data = json.loads(manifest.read_text('utf-8'))
    found = [r for r in data['records'] if r['id'] == ident]
    if len(found) != 1:
        raise ValueError('missing or duplicate pose ID')
    row = found[0]
    if not subject_confirmed(row):
        raise ValueError('subject classification required; quarantined image path withheld')
    if subject != row['subject_label']:
        raise ValueError('target subject must exactly match confirmed source label')
    if (row.get('visual_qa_status') != 'PASS' or row.get('anatomy_qa_status') != 'PASS'
            or not row.get('visual_review_evidence') or not row.get('review_limitations')
            or row.get('visual_review_sha256') != row['sha256']):
        raise ValueError('hash-bound visual and connected-body pose review required')
    if not ready(row):
        raise ValueError('pose excluded or completed library curation receipt missing')
    relative = Path(row['relative_path'].replace('\\','/'))
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('unsafe pose image path')
    path = (root/relative).resolve(); path.relative_to(root)
    if not path.is_file() or sha(path) != row['sha256']:
        raise ValueError('pose image missing or current hash differs')
    return {'schema_version':1, 'type':'CANDIDATE_SELECTION', 'pool':POOL,
            'candidate_id':ident, 'role':role, 'path':str(path), 'sha256':row['sha256'],
            'source_manifest_sha256':sha(manifest), 'subject_label':row['subject_label'],
            'subject_label_evidence':row['subject_label_evidence'],
            'review':{'observations':row['visual_review_evidence'], 'limitations':row['review_limitations']},
            'generation_eligible':False, 'approval_status':'NOT_APPROVED',
            'restrictions':{'transfer_contract':LIMITS,'style_influence':'FORBIDDEN'},
            'provenance':{'source_url':row['source_url'],'post_url':data['source']}}


def plan_references(workspace, values, target):
    try:
        from tools.reference_compatibility import validate_reference_compatibility
    except ModuleNotFoundError:
        from reference_compatibility import validate_reference_compatibility
    subject = {'MALE_ANATOMY':'MALE','FEMALE_ANATOMY':'FEMALE'}.get(target.get('target_anatomy'),'')
    result = []
    for spec in values:
        parts = spec.split('=')
        if len(parts) != 2:
            raise ValueError('--pose-pool-reference requires ID=ROLE')
        ident, role = parts
        choice = select(workspace, ident, role, subject)
        if any(r['path'] == choice['path'] for r in result):
            raise ValueError('duplicate pose image in call')
        record = {'ref_id':ident,'pool':POOL,'mode':role,'selection_spec':spec,
                  'path':choice['path'],'sha256':choice['sha256'],
                  'source_manifest_sha256':choice['source_manifest_sha256'],
                  'active_roles':[role], 'source_character_id':'UNKNOWN',
                  'subject_label':subject,'subject_label_status':'CONFIRMED',
                  'subject_label_sha256':choice['sha256'],
                  'subject_label_evidence':choice['subject_label_evidence'],
                  'style_influence':'FORBIDDEN','review':choice['review'],
                  'transfer_contract':LIMITS}
        record['compatibility'] = validate_reference_compatibility(target,record,[role])
        if not record['compatibility']['compatible']:
            raise ValueError(record['compatibility']['reason'])
        result.append(record)
    return result


def revalidate(workspace, record, target, roles):
    fresh = plan_references(workspace,[record['selection_spec']],target)[0]
    for key in ('path','sha256','source_manifest_sha256','active_roles','subject_label',
                'subject_label_sha256','subject_label_evidence','transfer_contract','review'):
        if record.get(key) != fresh.get(key):
            raise ValueError('stale WaveSpeed planned reference: '+key)
    if set(roles) != set(fresh['active_roles']):
        raise ValueError('WaveSpeed attachment role differs from selected role')
