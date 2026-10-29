import json
from pathlib import Path
import pytest
from tools import reference_pool_manager as rpm

def test_search_hides_paths_for_unresolved_subject(monkeypatch, tmp_path):
    monkeypatch.setattr(rpm, "cfg", lambda: {"P": {"adapter":"x", "root":"."}})
    monkeypatch.setattr(rpm, "records", lambda pool: (tmp_path, [{"id":"g1","title":"group sheet","tags":["group"],"subject_label":"UNKNOWN","subject_label_status":"UNKNOWN","relative_path":"secret.jpg","sha256":"0"*64,"role":"HUMAN_POSE_SOFT","provenance":{}}]))
    got=rpm.search(["P"], limit=5)["records"][0]
    assert "relative_path" not in got

def test_safe_rejects_path_escape(tmp_path):
    with pytest.raises(rpm.PoolError): rpm.safe(tmp_path, "../outside.png")

def test_select_enforces_role_and_subject(monkeypatch, tmp_path):
    f=tmp_path/"p.png"; f.write_bytes(b"current")
    row={"id":"p1","title":"pose","tags":[],"subject_label":"MALE","subject_label_status":"CONFIRMED","sha256":rpm.sha(f),"relative_path":"p.png","role":"HUMAN_POSE_SOFT","kind":"SINGLE_POSE","record_binding_status":"CURRENT","provenance":{},"raw":{"subject_label_sha256":rpm.sha(f),"source_sha256":"binding"}}
    monkeypatch.setattr(rpm,"cfg",lambda:{"PINTEREST_SKETCH":{"root":".","roles":["HUMAN_POSE_SOFT"],"adapter":"x","source_manifest":"unused"}})
    monkeypatch.setattr(rpm,"records",lambda p:(tmp_path,[row]))
    monkeypatch.setattr(rpm,"read_json",lambda p:{"records":[{"id":"p1","sha256":rpm.sha(f),"source_sha256":rpm.sha(f),"subject_label":"MALE","review_binding_sha256":rpm.sha(f),"path":"p.png","proposed_role":"HUMAN_POSE_SOFT","kind":"SINGLE_POSE","delivery_path":"p.png","review_fresh":True,"collection_selected":True,"quarantined":False}]})
    with pytest.raises(rpm.PoolError, match="exactly match"):
        rpm.select("PINTEREST_SKETCH","p1","HUMAN_POSE_SOFT","FEMALE")
    with pytest.raises(rpm.PoolError, match="not allowed"):
        rpm.select("PINTEREST_SKETCH","p1","IDENTITY","MALE")
    assert rpm.select("PINTEREST_SKETCH","p1","HUMAN_POSE_SOFT","MALE")["generation_eligible"] is False

def test_select_rejects_tampered_source(monkeypatch, tmp_path):
    f=tmp_path/"p.png"; f.write_bytes(b"tampered")
    row={"id":"p1","title":"pose","tags":[],"subject_label":"MALE","subject_label_status":"CONFIRMED","sha256":"0"*64,"relative_path":"p.png","role":"HUMAN_POSE_SOFT","kind":"SINGLE_POSE","provenance":{},"raw":{"review_binding_sha256":"binding"}}
    monkeypatch.setattr(rpm,"cfg",lambda:{"PINTEREST_SKETCH":{"root":".","roles":["HUMAN_POSE_SOFT"],"adapter":"x","source_manifest":"unused"}})
    monkeypatch.setattr(rpm,"records",lambda p:(tmp_path,[row]))
    monkeypatch.setattr(rpm,"read_json",lambda p:{"records":[{"id":"p1","sha256":"0"*64,"source_sha256":"0"*64,"subject_label":"MALE","review_binding_sha256":"0"*64,"path":"p.png","proposed_role":"HUMAN_POSE_SOFT","kind":"SINGLE_POSE","delivery_path":"p.png","review_fresh":True,"collection_selected":True,"quarantined":False}]})
    with pytest.raises(rpm.PoolError, match="stale"):
        rpm.select("PINTEREST_SKETCH","p1","HUMAN_POSE_SOFT","MALE")


def test_records_never_confirms_quarantined_human(monkeypatch, tmp_path):
    digest='a'*64
    candidate={'candidate_id':'q','sha256':digest,'subject_label':'MALE','subject_label_sha256':digest,
               'proposed_role':'HUMAN_POSE_SOFT','kind':'SINGLE_POSE','path':'q.png','quarantined':True}
    source={'id':'q','sha256':digest,'subject_label':'MALE','subject_source_eligible':True,
            'proposed_role':'HUMAN_POSE_SOFT','delivery_path':'q.png','review_fresh':True,
            'collection_selected':True,'quarantined':True,'review_binding_sha256':digest}
    definitions=rpm.category_cfg()
    monkeypatch.setattr(rpm,'category_cfg',lambda:definitions)
    monkeypatch.setattr(rpm,'ROOT',tmp_path)
    monkeypatch.setattr(rpm,'cfg',lambda:{'PINTEREST_SKETCH':{'root':'.','adapter':'pinterest',
                        'candidate_manifest':'candidate.json','source_manifest':'source.json'}})
    monkeypatch.setattr(rpm,'read_json',lambda p:{'records':[candidate if p.name=='candidate.json' else source]})
    _,rows=rpm.records('PINTEREST_SKETCH')
    assert rows[0]['record_binding_status']=='CURRENT'
    assert rows[0]['subject_label_status']=='UNKNOWN'
    assert 'relative_path' not in rpm.public_record('PINTEREST_SKETCH',rows[0])


@pytest.mark.parametrize('candidate_quarantine,source_quarantine',[(True,True),(True,False),(False,True)])
def test_confirmed_human_selection_cannot_export_quarantined_path(monkeypatch,tmp_path,candidate_quarantine,source_quarantine):
    file=tmp_path/'q.png'; file.write_bytes(b'pose')
    digest=rpm.sha(file)
    row={'id':'q','subject_label':'MALE','subject_label_status':'CONFIRMED','sha256':digest,
         'relative_path':'q.png','role':'HUMAN_POSE_SOFT','kind':'SINGLE_POSE',
         'record_binding_status':'CURRENT','raw':{'quarantined':candidate_quarantine}}
    source={'id':'q','sha256':digest,'subject_label':'MALE','proposed_role':'HUMAN_POSE_SOFT',
            'kind':'SINGLE_POSE','path':'q.png','delivery_path':'q.png','review_binding_sha256':digest,
            'review_fresh':True,'collection_selected':True,'quarantined':source_quarantine}
    monkeypatch.setattr(rpm,'cfg',lambda:{'PINTEREST_SKETCH':{'roles':['HUMAN_POSE_SOFT'],'source_manifest':'source.json'}})
    monkeypatch.setattr(rpm,'records',lambda p:(tmp_path,[row]))
    monkeypatch.setattr(rpm,'read_json',lambda p:{'records':[source]})
    with pytest.raises(rpm.PoolError):
        rpm.select('PINTEREST_SKETCH','q','HUMAN_POSE_SOFT','MALE')
