import json
from pathlib import Path
import pytest
from tools import reference_pool_manager as rpm

def test_search_hides_paths_for_unresolved_subject(monkeypatch, tmp_path):
    monkeypatch.setattr(rpm, "cfg", lambda: {"P": {"adapter":"x", "root":"."}})
    monkeypatch.setattr(rpm, "records", lambda pool: (tmp_path, [{"id":"g1","title":"group sheet","tags":["group"],"subject_label":"UNKNOWN","subject_label_status":"UNKNOWN","relative_path":"secret.jpg","sha256":"0"*64,"role":"HUMAN_POSE_SOFT","provenance":{}}]))
    got=rpm.search(["P"], limit=5)["records"][0]
    assert "relative_path" not in got


@pytest.mark.parametrize('label,status,has_overview', [('MALE','CONFIRMED',True),('UNKNOWN','UNKNOWN',False)])
def test_scenario_search_uses_descriptions_without_promoting_source(monkeypatch,tmp_path,label,status,has_overview):
    from tools import reference_knowledge as knowledge
    directory=tmp_path/'work/reference-knowledge-20261113'
    directory.mkdir(parents=True);(directory/'DATABASE.json').write_text('{}')
    monkeypatch.setattr(rpm,'ROOT',tmp_path)
    row={'id':'p1','title':'SINGLE_POSE','tags':['WEAPON:HAMMER'],'subject_label':label,
         'subject_label_status':status,'sha256':'a'*64,'relative_path':'p.png',
         'role':'HUMAN_POSE_SOFT','provenance':{}}
    monkeypatch.setattr(rpm,'records',lambda pool:(tmp_path,[row]))
    enriched={'key':'P:p1','sha256':'a'*64,'search_text':'молот на плече',
              'description':'Молот лежит на плече','description_basis':'visual overview',
              'use_for':'pose','limitations':['no body transfer'],'missing_checks':['fullres'],
              'verification':['check current brief'],'preview_allowed':True,'subject':'MALE',
              'overview':{'path':'collages/test.jpg','cell':4}}
    monkeypatch.setattr(knowledge,'load',lambda path:{'records':[enriched]})
    result=rpm.search(['P'],query='молот плече')
    assert result['knowledge_status']=='CURRENT' and result['total']==1
    actual=result['records'][0]
    assert actual['id']=='p1' and actual['role']=='HUMAN_POSE_SOFT'
    assert actual['subject_label_status']==status
    assert actual['discovery']['description']=='Молот лежит на плече'
    assert ('overview' in actual['discovery'])==has_overview
    assert ('relative_path' in actual)==has_overview

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

def test_atlas_select_checks_the_requested_source_for_the_given_subject(monkeypatch, tmp_path):
    from tools import pose_reference_manager as prm
    f=tmp_path/"a.png"; f.write_bytes(b"atlas")
    row={"id":"v1","sha256":rpm.sha(f),"relative_path":"a.png","provenance":{}}
    monkeypatch.setattr(rpm,"cfg",lambda:{"POSE_ATLAS":{"root":".","roles":["POSE_SOFT"],"adapter":"x"}})
    monkeypatch.setattr(rpm,"records",lambda p:(tmp_path,[row]))
    calls=[]
    def fake_context(base, anatomy="ANY", source_id="", **kwargs):
        calls.append((anatomy, source_id))
        if anatomy=="ANY": raise prm.PoseReferenceError("target_anatomy must be MALE_ANATOMY or FEMALE_ANATOMY.")
        return {"groups":[{"views":[{"source_id":source_id}]}]}
    monkeypatch.setattr(prm,"reference_context",fake_context)
    assert rpm.select("POSE_ATLAS","v1","POSE_SOFT","MALE")["generation_eligible"] is False
    assert calls==[("MALE_ANATOMY","v1")]
    with pytest.raises(rpm.PoolError, match="not selectable"):
        rpm.select("POSE_ATLAS","v1","POSE_SOFT")


def _shape_fit_workspace(tmp_path, *, subject="FEMALE", status="CONFIRMED"):
    import hashlib
    from tools import shape_fit_reference_pool as adapter

    pool_root = tmp_path / "SODABABYX_SOURCE_COLLECTION"
    (pool_root / "originals").mkdir(parents=True)
    media = pool_root / "originals" / "sheet.jpg"
    media.write_bytes(b"registered source bytes")
    digest = hashlib.sha256(media.read_bytes()).hexdigest()
    row = {
        "id": "SODA_TEST", "title_ru": "Набор форм", "local_path": "originals/sheet.jpg",
        "sha256": digest, "subject_label": subject, "subject_label_status": status,
        "subject_label_sha256": digest, "review_binding_sha256": digest,
        "subject_label_evidence": "Reviewed sheet subject",
        "review_status": "REVIEWED_WITH_LIMITATIONS", "review": {"observations": "reviewed", "limitations": "limited"},
        "categories": ["BODY_SHAPE"], "allowed_roles": ["BODY_SHAPE_REFERENCE"],
        "forbidden_roles": ["STYLE", "IDENTITY", "BODY_BUILD_TARGET"], "style_influence": "FORBIDDEN",
        "generation_eligible": False, "source_character_id": "UNKNOWN", "rights_status": "UNVERIFIED_LOCAL_REFERENCE_ONLY",
        "source_profile": "https://x.com/sodababyx", "post_url": "https://x.com/sodababyx/status/1",
        "source_image_url": "https://pbs.twimg.com/media/sheet.jpg", "tags": ["body"],
        "variants": [{"id": "A", "label": "Variant A", "position": 1, "location_rule": "left"}],
    }
    source = {k: row[k] for k in ("id", "local_path", "sha256", "subject_label", "review_binding_sha256", "review_status")}
    (pool_root / "CURATED.json").write_text(json.dumps({"records": [row]}), encoding="utf-8")
    (pool_root / "SOURCE.json").write_text(json.dumps({"records": [source]}), encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config/reference_pools.json").write_text(json.dumps({"pools": {"SODABABYX": {
        "root": "SODABABYX_SOURCE_COLLECTION", "manifest": "SODABABYX_SOURCE_COLLECTION/CURATED.json",
        "source_manifest": "SODABABYX_SOURCE_COLLECTION/SOURCE.json", "adapter": "shape_fit",
        "count": 1, "roles": ["BODY_SHAPE_REFERENCE", "CLOTHING_FIT_REFERENCE", "POSE_REFERENCE"]
    }}}), encoding="utf-8")
    return adapter, row, source


def test_shape_fit_selection_requires_explicit_variant_and_exports_unapproved_candidate(tmp_path):
    from tools import shape_fit_reference_pool as adapter

    adapter, _, _ = _shape_fit_workspace(tmp_path)
    with pytest.raises(adapter.ShapeFitError, match="explicit valid --variant"):
        adapter.select_candidate(tmp_path, "SODABABYX", "SODA_TEST", "BODY_SHAPE_REFERENCE", "FEMALE", "")
    result = adapter.select_candidate(tmp_path, "SODABABYX", "SODA_TEST", "BODY_SHAPE_REFERENCE", "FEMALE", "A")
    assert result["type"] == "CANDIDATE_SELECTION"
    assert result["variant"]["id"] == "A"
    assert result["path"].endswith("sheet.jpg")
    assert result["restrictions"]["generation_eligible"] is False
    assert result["restrictions"]["approval_status"] == "NOT_APPROVED"
    assert result["restrictions"]["style_influence"] == "FORBIDDEN"


@pytest.mark.parametrize("subject,status", [("UNKNOWN", "QUARANTINED"), ("MIXED", "QUARANTINED")])
def test_shape_fit_unknown_or_quarantined_subject_never_exports_path(tmp_path, subject, status):
    from tools import shape_fit_reference_pool as adapter

    adapter, _, _ = _shape_fit_workspace(tmp_path, subject=subject, status=status)
    _, rows = adapter.catalog_records(tmp_path, "SODABABYX")
    assert rows[0]["relative_path"] == ""
    assert "source_image_url" not in rows[0]["provenance"]
    with pytest.raises(adapter.ShapeFitError, match="unknown or quarantined"):
        adapter.select_candidate(tmp_path, "SODABABYX", "SODA_TEST", "BODY_SHAPE_REFERENCE", subject, "A")


def test_shape_fit_enforces_exact_subject_and_finite_roles(tmp_path):
    from tools import shape_fit_reference_pool as adapter

    adapter, _, _ = _shape_fit_workspace(tmp_path)
    with pytest.raises(adapter.ShapeFitError, match="exactly match"):
        adapter.select_candidate(tmp_path, "SODABABYX", "SODA_TEST", "BODY_SHAPE_REFERENCE", "MALE", "A")
    with pytest.raises(adapter.ShapeFitError, match="not supported"):
        adapter.select_candidate(tmp_path, "SODABABYX", "SODA_TEST", "STYLE", "FEMALE", "A")


@pytest.mark.parametrize("mutation", ["source_hash", "review_binding", "duplicate", "file_hash"])
def test_shape_fit_rejects_stale_or_duplicate_source_bindings(tmp_path, mutation):
    from tools import shape_fit_reference_pool as adapter

    adapter, row, source = _shape_fit_workspace(tmp_path)
    if mutation == "source_hash":
        source["sha256"] = "0" * 64
    elif mutation == "review_binding":
        source["review_binding_sha256"] = "0" * 64
    elif mutation == "duplicate":
        source["duplicate_of"] = "SODA_OTHER"
    elif mutation == "file_hash":
        (tmp_path / "SODABABYX_SOURCE_COLLECTION/originals/sheet.jpg").write_bytes(b"changed")
    (tmp_path / "SODABABYX_SOURCE_COLLECTION/SOURCE.json").write_text(json.dumps({"records": [source]}), encoding="utf-8")
    with pytest.raises(adapter.ShapeFitError):
        adapter.select_candidate(tmp_path, "SODABABYX", "SODA_TEST", "BODY_SHAPE_REFERENCE", "FEMALE", "A")


def test_shape_fit_variants_are_searchable_and_shape_categories_exist(monkeypatch, tmp_path):
    adapter, _, _ = _shape_fit_workspace(tmp_path)
    monkeypatch.setattr(rpm, "ROOT", tmp_path)
    monkeypatch.setattr(rpm, "cfg", lambda: json.loads((tmp_path / "config/reference_pools.json").read_text())["pools"])
    monkeypatch.setattr(rpm, "category_cfg", lambda: {"categories": {"BODY_SHAPE": {"label_ru": "Формы тела"}, "BODY_PART_SHAPE": {"label_ru": "Формы частей тела"}}})
    got = rpm.search(["SODABABYX"], query="Variant A")
    assert got["total"] == 1
    assert {x["category"] for x in rpm.category_summary(["SODABABYX"])} == {"BODY_SHAPE", "BODY_PART_SHAPE"}


def test_shape_fit_plan_keeps_physical_source_variant_and_requires_call_review(tmp_path):
    from tools.style_pack_plans import build_attachment_plan
    from tools.style_pack_manager import parse_selected_source_reviews, StylePackError

    adapter, _, _ = _shape_fit_workspace(tmp_path)
    target = {"character_id": "CHAR_TEST", "target_anatomy": "FEMALE_ANATOMY", "anatomy_evidence_source": "confirmed profile"}
    refs = adapter.plan_references(tmp_path, ["SODA_TEST=BODY_SHAPE_REFERENCE=A"], target)
    assert refs[0]["variant"]["id"] == "A"
    assert refs[0]["style_influence"] == "FORBIDDEN"
    slots = build_attachment_plan({}, refs, 5)
    assert slots[0]["physically_attach"] is True
    assert slots[0]["path"] == refs[0]["path"]
    assert slots[0]["active_roles"] == ["SODA_TEST:AUX_BODY_SHAPE"]
    with pytest.raises(StylePackError):
        parse_selected_source_reviews([], {}, {}, refs)
    adapter.revalidate_plan_reference(tmp_path, refs[0], target, ["AUX_BODY_SHAPE"])
    from types import SimpleNamespace
    from tools.style_pack_manager import validate_resolved_slot_compatibility
    plan = {"target_identity_compatibility": {"target": target}, "auxiliary_body_references": refs}
    validate_resolved_slot_compatibility(SimpleNamespace(workspace=tmp_path, pack=tmp_path / "style"), plan, slots[0], Path(refs[0]["path"]))
    with pytest.raises(adapter.ShapeFitError, match="role differs"):
        adapter.revalidate_plan_reference(tmp_path, refs[0], target, ["STYLE"])
    refs[0]["variant"]["label"] = "Unselected variant"
    with pytest.raises(adapter.ShapeFitError, match="stale: variant"):
        adapter.revalidate_plan_reference(tmp_path, refs[0], target, ["AUX_BODY_SHAPE"])


def _accepted_mouth_single(tmp_path):
    import hashlib
    adapter, parent, _ = _shape_fit_workspace(tmp_path)
    root = tmp_path / "SODABABYX_SOURCE_COLLECTION"
    (root / "singles").mkdir()
    (root / "singles/mouth.png").write_bytes(b"isolated mouth crop")
    digest = hashlib.sha256(b"isolated mouth crop").hexdigest()
    row = {"id": "SODA_TEST-EXPR__01_MOUTH", "source_id": "SODA_TEST", "variant_id": "MOUTH",
           "source_path": parent["local_path"], "source_sha256": parent["sha256"],
           "local_path": "singles/mouth.png", "sha256": digest, "subcategory": "mouth",
           "collection_accepted": True, "visual_review": "Reviewed isolated mouth crop",
           "subject_label": "NEUTRAL", "subject_label_status": "CONFIRMED",
           "subject_label_sha256": digest, "subject_label_evidence": "Reviewed sex-neutral lips only",
           "generation_subject_gate": "NEUTRAL_REQUIRES_SCOPED_REVIEW",
           "style_influence": "FORBIDDEN", "generation_eligible": False}
    (root / "SINGLES_MANIFEST.json").write_text(json.dumps({"collection_acceptance": {"quote": "accepted"}, "records": [row]}))
    return adapter, root, row


def test_neutral_single_mouth_preserves_label_and_revalidates_scope(tmp_path):
    adapter, root, row = _accepted_mouth_single(tmp_path)
    target = {"target_anatomy": "FEMALE_ANATOMY", "anatomy_evidence_source": "explicit request"}
    refs = adapter.plan_references(tmp_path, [row["id"] + "=MOUTH_EXPRESSION_REFERENCE=MOUTH"], target)
    assert refs[0]["subject_label"] == "NEUTRAL"
    assert refs[0]["compatibility"]["compatible"] is True
    adapter.revalidate_plan_reference(tmp_path, refs[0], target, ["AUX_MOUTH_EXPRESSION"])
    from tools.style_pack_manager import parse_selected_source_reviews, validate_resolved_slot_compatibility, compatibility_target
    from tools.style_pack_plans import build_attachment_plan
    from tools.generation_risk_assessor import _reference_bindings
    from types import SimpleNamespace
    slot = build_attachment_plan({}, refs, 5)[0]
    assert _reference_bindings([slot])[0]["active_roles"] == slot["active_roles"]
    parse_selected_source_reviews([json.dumps({"role": "FACE", "slot_role": slot["active_roles"][0],
        "path": refs[0]["path"], "view": "FULL_RESOLUTION", "outcome": "PASS", "applicability": "mouth expression only",
        "findings": "isolated lips", "limitations": "no identity or sex transfer"})], {}, {}, refs)
    validate_resolved_slot_compatibility(SimpleNamespace(workspace=tmp_path, pack=tmp_path / "style"),
        {"target_identity_compatibility": {"target": target}, "auxiliary_body_references": refs}, slot, Path(refs[0]["path"]))
    resolved = compatibility_target(None, SimpleNamespace(character_id="NONE", target_prompt="One adult woman"))
    assert resolved["target_anatomy"] == "FEMALE_ANATOMY"
    refs[0]["subject_label"] = "FEMALE"
    with pytest.raises(adapter.ShapeFitError, match="stale: subject_label"):
        adapter.revalidate_plan_reference(tmp_path, refs[0], target, ["AUX_MOUTH_EXPRESSION"])


@pytest.mark.parametrize("role", ["AUX_POSE", "STAGING_ONLY", "STYLE", "FACE", "BODY", "AUX_BODY_SHAPE"])
def test_neutral_mouth_cannot_be_repurposed(tmp_path, role):
    from tools.reference_compatibility import validate_reference_compatibility
    adapter, _, row = _accepted_mouth_single(tmp_path)
    target = {"target_anatomy": "MALE_ANATOMY", "anatomy_evidence_source": "explicit request"}
    ref = adapter.plan_references(tmp_path, [row["id"] + "=MOUTH_EXPRESSION_REFERENCE=MOUTH"], target)[0]
    assert validate_reference_compatibility(target, ref, [role])["compatible"] is False
    with pytest.raises(adapter.ShapeFitError):
        adapter.select_candidate(tmp_path, "SODABABYX", row["id"], "POSE_REFERENCE", "MALE", "MOUTH")


@pytest.mark.parametrize("mutation", ["parent_bytes", "crop_bytes", "label_hash", "unaccepted"])
def test_single_mouth_rejects_stale_provenance(tmp_path, mutation):
    adapter, root, row = _accepted_mouth_single(tmp_path)
    if mutation == "parent_bytes":
        (root / row["source_path"]).write_bytes(b"changed")
    elif mutation == "crop_bytes":
        (root / row["local_path"]).write_bytes(b"changed")
    else:
        row["subject_label_sha256" if mutation == "label_hash" else "collection_accepted"] = "wrong" if mutation == "label_hash" else False
        (root / "SINGLES_MANIFEST.json").write_text(json.dumps({"collection_acceptance": {"quote": "accepted"}, "records": [row]}))
    with pytest.raises(adapter.ShapeFitError):
        adapter.select_candidate(tmp_path, "SODABABYX", row["id"], "MOUTH_EXPRESSION_REFERENCE", "FEMALE", "MOUTH")
