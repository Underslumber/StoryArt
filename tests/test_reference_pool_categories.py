import json
from pathlib import Path
import pytest
from tools import reference_pool_manager as rpm

def test_category_summary_unifies_sources_without_false_costume_accessories():
    summary={x['category']:x for x in rpm.category_summary()}
    assert summary['CLOTHING']['by_source']['COSTUME_DB_01']==1407
    assert summary['CLOTHING']['by_source']['DRAWING_GUIDES']>0
    assert 'COSTUME_DB_01' not in summary['ACCESSORIES']['by_source']
    _, guides=rpm.records('DRAWING_GUIDES')
    assert all('ACCESSORIES' not in r['categories'] for r in guides if r.get('raw',{}).get('category')=='ANATOMY')
    assert summary['POSE_ACTION']['by_source']['POSE_ATLAS']==1439
    assert len(summary['POSE_ACTION']['by_source'])>=2
    assert sum(summary['GROUP_COMPOSITION']['by_source'].values())>=142
    assert 'PINTEREST_SKETCH' in summary['HANDS_FINGERS']['by_source']
    assert 'DRAWING_GUIDES' in summary['HANDS_FINGERS']['by_source']

def test_guides_reviewed_hand_tags_join_hand_category_and_derived_other_is_removed():
    _, rows=rpm.records('DRAWING_GUIDES')
    hand_rows=[r for r in rows if 'BODY_PART:HAND' in r.get('tags',[])]
    assert len(hand_rows)==22
    assert all('HANDS_FINGERS' in r['categories'] for r in hand_rows)
    assert all('ANATOMY' in r['categories'] for r in hand_rows if r['raw'].get('category')=='ANATOMY')
    animal=[r for r in rows if r.get('raw',{}).get('category')=='OTHER' and 'CONTENT:CREATURE' in r.get('tags',[])]
    assert animal and all('ANIMALS' in r['categories'] and 'OTHER' not in r['categories'] for r in animal)
    siren=next(r for r in rows if r.get('source_asset_id')=='OLX_2084217923149717998_01')
    assert 'ANATOMY' in siren['categories']
    assert 'SOURCE_REVIEWED_TITLE_TOPIC_ONLY' in siren['category_basis']['ANATOMY']
    costume=next(r for r in rows if r.get('source_asset_id')=='OLX_2086855166372774146_01')
    assert {'CLOTHING','DRAWING_TECHNIQUE'} <= set(costume['categories'])
    assert 'OTHER' not in costume['categories']

def test_categories_keep_group_hand_and_animal_subjects_separate():
    _, rows=rpm.records('PINTEREST_SKETCH')
    group=next(r for r in rows if r['role']=='GROUP_COMPOSITION_SOFT')
    hand=next(r for r in rows if r['role']=='HAND_GESTURE_ONLY')
    assert 'GROUP_COMPOSITION' in group['categories']
    assert 'HANDS_FINGERS' not in group['categories']
    assert 'HANDS_FINGERS' in hand['categories']
    assert 'GROUP_COMPOSITION' not in hand['categories']
    assert len(rpm.records('POSE_ATLAS')[1])==1439

def test_curated_guide_uses_crop_id_and_retains_crop_metadata():
    _, rows=rpm.records('DRAWING_GUIDES')
    row=next(r for r in rows if r.get('raw',{}).get('crop_id'))
    assert row['id']==row['raw']['crop_id']
    assert row['provenance']['crop_bounds']
    assert row['provenance']['source_sha256']

def test_guide_selection_delegates_exact_curated_crop_only(monkeypatch,tmp_path):
    source=tmp_path/'candidate.png'; source.write_bytes(b'crop')
    digest=rpm.sha(source)
    row={'id':'CROP_1','source_asset_id':'SOURCE_1','role':'DRAWING_TECHNIQUE','relative_path':'candidate.png','sha256':digest,'raw':{'crop_id':'CROP_1'},'provenance':{}}
    monkeypatch.setattr(rpm,'ROOT',tmp_path)
    monkeypatch.setattr(rpm,'cfg',lambda:{'DRAWING_GUIDES':{'root':'.','roles':['DRAWING_TECHNIQUE'],'adapter':'curated'}})
    monkeypatch.setattr(rpm,'records',lambda pool:(tmp_path,[row]))
    from tools import external_reference_catalog as ext
    seen=[]
    monkeypatch.setattr(ext,'build_catalog',lambda root:{})
    monkeypatch.setattr(ext,'select_positive_candidates',lambda root,catalog,ids,role:(seen.append((ids,role)) or {'kind':'POSITIVE_FRAGMENT_CANDIDATE_SELECTION','approval_status':'NOT_APPROVED'}))
    monkeypatch.setattr(ext,'select_candidates',lambda *args:pytest.fail('crop must not fall back to source selection'))
    result=rpm.select('DRAWING_GUIDES','CROP_1','DRAWING_TECHNIQUE')
    assert seen==[(['CROP_1'],'DRAWING_TECHNIQUE')]
    assert result['candidate_id']=='CROP_1'
    with pytest.raises(rpm.PoolError,match='not a current curated'):
        rpm.select('DRAWING_GUIDES','SOURCE_1','DRAWING_TECHNIQUE')
    with pytest.raises(rpm.PoolError,match='not a current curated'):
        rpm.select('DRAWING_GUIDES','UNLISTED_CROP','DRAWING_TECHNIQUE')

def test_validate_returns_nonzero_for_issues(monkeypatch,capsys):
    monkeypatch.setattr(rpm,'validate_pool',lambda pool,deep=False:{'pool':pool,'status':'ISSUES','records':0,'expected':1,'issues':['missing']})
    monkeypatch.setattr('sys.argv',['reference_pool_manager','validate','--pool','POSE_ATLAS'])
    assert rpm.main()==1
    assert 'ISSUES' in capsys.readouterr().out

@pytest.mark.parametrize('role,kind',[('HAND_GESTURE_ONLY','SINGLE_POSE'),('GROUP_COMPOSITION_SOFT','GROUP_SCENE'),('ANIMAL_OR_DETAIL_SOFT','SINGLE_POSE')])
def test_special_pinterest_roles_select_metadata_only_after_current_bindings(monkeypatch,tmp_path,role,kind):
    delivery=tmp_path/'ready.png'; delivery.write_bytes(b'candidate')
    source=tmp_path/'source.png'; source.write_bytes(b'candidate')
    digest=rpm.sha(delivery)
    candidate={'id':'C1','role':role,'kind':kind,'subject_label':'UNKNOWN','subject_label_status':'UNKNOWN','record_binding_status':'CURRENT','relative_path':'ready.png','sha256':digest,'categories':['ANIMALS' if role.startswith('ANIMAL') else 'GROUP_COMPOSITION' if role.startswith('GROUP') else 'HANDS_FINGERS'],'tags':['HAND_GESTURE'],'groups':[],'provenance':{'source_pin_url':'https://example.test/pin/1','source_image_url':'https://example.test/image.png','parent_id':'P1','bbox':[1,2,3,4],'notes':'review note'},'raw':{'quarantined':True}}
    manifest={'id':'C1','sha256':digest,'subject_label':'UNKNOWN','proposed_role':role,'kind':kind,'delivery_path':'ready.png','review_binding_sha256':digest,'review_fresh':True,'collection_selected':True,'quarantined':True,'path':'source.png','source_sha256':digest,'parent_id':'P1','parent_sha256':'parenthash','parent_review_binding_sha256':'parenthash','bbox':[1,2,3,4],'review_status':'PASS','review_evidence':'current','notes':'review note','rights_status':'UNVERIFIED_LOCAL_REFERENCE_ONLY'}
    monkeypatch.setattr(rpm,'ROOT',tmp_path)
    monkeypatch.setattr(rpm,'cfg',lambda:{'PINTEREST_SKETCH':{'root':'.','roles':['HUMAN_POSE_SOFT','HAND_GESTURE_ONLY','GROUP_COMPOSITION_SOFT','ANIMAL_OR_DETAIL_SOFT'],'adapter':'pinterest','source_manifest':'manifest.json'}})
    monkeypatch.setattr(rpm,'records',lambda pool:(tmp_path,[candidate]))
    monkeypatch.setattr(rpm,'read_json',lambda p:{'records':[manifest]})
    result=rpm.select('PINTEREST_SKETCH','C1',role)
    assert result['type']=='CANDIDATE_SELECTION'
    assert result['classification_status']=='CLASSIFICATION_REQUIRED'
    assert result['role_review_status']=='ROLE_REVIEW_REQUIRED'
    assert result['generation_eligible'] is False and result['approval_status']=='NOT_APPROVED'
    assert result['image_attachment_allowed'] is False
    assert result['provenance']['parent_id']=='P1' and result['provenance']['bbox']==[1,2,3,4]
    assert not any(k in result for k in ('path','relative_path','source_image_url','source_pin_url'))
    assert not any(k.endswith('path') or k.endswith('url') for k in result['provenance'])
    manifest['review_binding_sha256']=''
    with pytest.raises(rpm.PoolError,match='binding is stale'):
        rpm.select('PINTEREST_SKETCH','C1',role)

def test_build_index_is_category_first_and_contains_no_image_elements(tmp_path):
    out=tmp_path/'catalog.html'
    result=rpm.build_index(out)
    doc=out.read_text(encoding='utf-8')
    assert result['records']>3000
    assert 'Категория' in doc and 'Источник' in doc
    assert 'Поза / лист' in doc and 'Ракурс' in doc
    assert '<img' not in doc.lower()
    packed=doc.split('const data=',1)[1].split(',cats=',1)[0]
    entries=json.loads(packed)
    atlas=next(x for x in entries if x['pool']=='POSE_ATLAS')
    assert atlas['group_display'] and atlas['angle']
    unknown=next(x for x in entries if x['pool']=='PINTEREST_SKETCH' and x['subject_label']=='UNKNOWN')
    assert 'relative_path' not in unknown
    assert 'source_image_url' not in unknown.get('provenance',{})
    costume=next(x for x in entries if x['pool']=='COSTUME_DB_01')
    assert 'relative_path' not in costume
    assert 'source_url' not in costume.get('provenance',{})
    guide=next(x for x in entries if x['pool']=='DRAWING_GUIDES' and x['subject_label']=='UNKNOWN')
    assert 'relative_path' not in guide
    assert all(not k.endswith('path') for k in guide.get('provenance',{}))


def test_no_subject_preview_requires_exact_hash_bound_evidence(monkeypatch, tmp_path):
    row={'asset_id':'guide','sha256':'a'*64,'local_path':'guide.png',
         'subject_label':'NO_SUBJECT','subject_label_evidence':'Reviewed isolated object',
         'subject_label_sha256':'b'*64,'category':'OTHER'}
    monkeypatch.setattr(rpm,'ROOT',tmp_path)
    monkeypatch.setattr(rpm,'cfg',lambda:{'DRAWING_GUIDES':{'root':'.','adapter':'curated','manifest':'manifest.json'}})
    definitions=rpm.category_cfg()
    monkeypatch.setattr(rpm,'category_cfg',lambda:definitions)
    monkeypatch.setattr(rpm,'read_json',lambda p:{'items':[row]} if p.name=='manifest.json' else {'crops':[]})
    _, records=rpm.records('DRAWING_GUIDES')
    assert records[0]['subject_label_status']=='UNKNOWN'
    assert 'relative_path' not in rpm.public_record('DRAWING_GUIDES',records[0])
    row['subject_label_sha256']=row['sha256']
    _, records=rpm.records('DRAWING_GUIDES')
    assert records[0]['subject_label_status']=='NO_SUBJECT'
