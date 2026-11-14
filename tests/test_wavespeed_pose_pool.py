import hashlib
import json
from pathlib import Path

import pytest

from tools import wavespeed_pose_pool as pool
from tools.wavespeed_pose_library import apply_reviews, preserve_reviews, download_original


def setup_pool(tmp_path):
    root = tmp_path/'WAVESPEED_POSES_SOURCE_COLLECTION'
    (root/'images').mkdir(parents=True)
    (tmp_path/'config').mkdir()
    (tmp_path/'config/reference_pools.json').write_text(json.dumps({'pools':{pool.POOL:{
        'root':root.name, 'manifest':root.name+'/POSES.json'}}}))
    image = root/'images/WS_0001_3d_front.webp'
    image.write_bytes(b'fixture-source')
    row = {'id':'WS_0001_3d_front','group_id':'WS_0001','title':'Pose','category':'Standing',
           'representation':'3d','angle':'front','source_url':'https://static.wavespeed.ai/media/images/test.webp',
           'relative_path':'images/'+image.name,'sha256':hashlib.sha256(image.read_bytes()).hexdigest(),
           'subject_label':'UNKNOWN','subject_label_status':'QUARANTINED'}
    data = {'source':'https://wavespeed.ai/free-tools/ai-pose-gallery','records':[row]}
    manifest = root/'POSES.json'
    manifest.write_text(json.dumps(data))
    return root, manifest, image, row, data


def confirm(root,manifest,row,data):
    review = {'id':row['id'],'sha256':row['sha256'],'subject_label':'FEMALE',
              'subject_label_evidence':'Explicit bounded subject review','reviewer':'test-reviewer',
              'viewed':'FULL_RESOLUTION','visual_qa_status':'PASS','anatomy_qa_status':'PASS',
              'visual_review_evidence':'Connected body, supports and projection inspected',
              'review_limitations':'Occluded hand requires scene-specific check'}
    apply_reviews(data,{'reviews':[review]},root,'review-hash')
    manifest.write_text(json.dumps(data))


def test_quarantine_is_metadata_only_and_cannot_select(tmp_path):
    from tools.reference_pool_manager import public_record
    root,manifest,image,row,data=setup_pool(tmp_path)
    record=pool.records(tmp_path)[1][0]
    public=public_record(pool.POOL,record)
    assert 'relative_path' not in public
    assert 'source_url' not in public['provenance']
    with pytest.raises(ValueError,match='classification required'):
        pool.select(tmp_path,row['id'],'AUX_POSE','FEMALE')


def test_selector_enforces_subject_role_current_bytes_and_review(tmp_path):
    root,manifest,image,row,data=setup_pool(tmp_path)
    confirm(root,manifest,row,data)
    choice=pool.select(tmp_path,row['id'],'AUX_POSE','FEMALE')
    assert choice['generation_eligible'] is False
    with pytest.raises(ValueError,match='exactly match'):
        pool.select(tmp_path,row['id'],'AUX_POSE','MALE')
    with pytest.raises(ValueError,match='only AUX_POSE'):
        pool.select(tmp_path,row['id'],'STYLE','FEMALE')
    image.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='hash differs'):
        pool.select(tmp_path,row['id'],'AUX_POSE','FEMALE')


def test_review_requires_exact_hash_and_full_resolution_for_anatomy_pass(tmp_path):
    root,manifest,image,row,data=setup_pool(tmp_path)
    bad={'id':row['id'],'sha256':row['sha256'],'subject_label':'FEMALE',
         'subject_label_evidence':'review','reviewer':'reviewer','anatomy_qa_status':'PASS','viewed':'COLLAGE'}
    with pytest.raises(ValueError,match='full-resolution'):
        apply_reviews(data,{'reviews':[bad]},root,'hash')
    assert row['subject_label']=='UNKNOWN'
    bad['sha256']='0'*64
    with pytest.raises(ValueError,match='hash differs'):
        apply_reviews(data,{'reviews':[bad]},root,'hash')


def test_exact_call_rechecks_metadata_and_role(tmp_path):
    from types import SimpleNamespace
    from tools import style_pack_manager as manager
    root,manifest,image,row,data=setup_pool(tmp_path)
    confirm(root,manifest,row,data)
    target={'target_anatomy':'FEMALE_ANATOMY','anatomy_evidence_source':'explicit target',
            'character_id':'CHAR_TEST'}
    record=pool.plan_references(tmp_path,[row['id']+'=AUX_POSE'],target)[0]
    pool.revalidate(tmp_path,record,target,['AUX_POSE'])
    paths=SimpleNamespace(workspace=tmp_path,pack=tmp_path/'STYLE_PROJECT_PACK',
                          generations=tmp_path/'STYLE_GENERATIONS',slug='style')
    plan={'target_identity_compatibility':{'target':target},'auxiliary_body_references':[record]}
    slot={'active_roles':[row['id']+':AUX_POSE']}
    manager.validate_resolved_slot_compatibility(paths,plan,slot,image.resolve())
    with pytest.raises(ValueError,match='role differs'):
        pool.revalidate(tmp_path,record,target,['AUX_CAMERA'])
    data['records'][0]['subject_label_status']='QUARANTINED'
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='classification required'):
        pool.revalidate(tmp_path,record,target,['AUX_POSE'])
    with pytest.raises(manager.StylePackError,match='classification required'):
        manager.validate_resolved_slot_compatibility(paths,plan,slot,image.resolve())


def test_reviews_cannot_change_source_paths_and_reimport_preserves_bound_reviews(tmp_path):
    root,manifest,image,row,data=setup_pool(tmp_path)
    review={'id':row['id'],'sha256':row['sha256'],'subject_label':'FEMALE',
            'subject_label_evidence':'evidence','reviewer':'reviewer','relative_path':'../private'}
    with pytest.raises(ValueError,match='may not change source'):
        apply_reviews(data,{'reviews':[review]},root,'hash')
    confirm(root,manifest,row,data)
    fresh={k:v for k,v in row.items() if not k.startswith('subject_')}
    preserve_reviews([fresh],data)
    assert fresh['subject_label']=='FEMALE'
    fresh['sha256']='0'*64
    fresh.pop('subject_label')
    preserve_reviews([fresh],data)
    assert 'subject_label' not in fresh


def test_range_download_reassembles_exact_bytes_and_rejects_changed_source(monkeypatch):
    import io
    payload=b'a'*8192+b'last-original-bytes'
    changed=False
    def open_range(request,timeout):
        start,end=map(int,request.get_header('Range').removeprefix('bytes=').split('-'))
        end=min(end,len(payload)-1)
        body=io.BytesIO(payload[start:end+1])
        body.status=206
        body.headers={'Content-Range':f'bytes {start}-{end}/{len(payload)}',
                      'ETag':'changed' if changed and start else 'original'}
        return body
    monkeypatch.setattr('urllib.request.urlopen',open_range)
    assert download_original('https://static.wavespeed.ai/example.webp')==payload
    changed=True
    with pytest.raises(ValueError,match='ETag changed'):
        download_original('https://static.wavespeed.ai/example.webp')
