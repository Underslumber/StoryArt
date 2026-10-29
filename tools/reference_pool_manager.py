#!/usr/bin/env python3
"""Bounded discovery of local reference pools. Selections remain unapproved candidates."""
from __future__ import annotations
import argparse, csv, hashlib, json, sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
REGISTRY = ROOT / "config/reference_pools.json"
CATEGORY_REGISTRY = ROOT / "config/reference_categories.json"
class PoolError(ValueError): pass

def read_json(p: Path): return json.loads(p.read_text(encoding="utf-8-sig"))
def sha(p: Path):
 h=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
 return h.hexdigest()
def safe(root: Path, rel: str) -> Path:
 p=Path(rel.replace("\\","/"))
 if p.is_absolute() or ".." in p.parts: raise PoolError("path escape rejected")
 out=(root/p).resolve(); out.relative_to(root.resolve()); return out
def cfg(): return read_json(REGISTRY)["pools"]
def category_cfg(): return read_json(CATEGORY_REGISTRY)
def categorize(pool, r):
 c=category_cfg(); categories=[]; source_map=c["source_category_map"]
 if pool=="POSE_ATLAS": categories=["POSE_ACTION"]
 elif pool=="COSTUME_DB_01": categories=["CLOTHING"]
 elif pool=="PINTEREST_SKETCH":
  role=c["pinterest_role_map"].get(r.get("role"));
  if role: categories.append(role)
  tags=" ".join(map(str,r.get("tags",[]))).upper(); groups=" ".join(map(str,r.get("groups",[]))).upper()
  if r.get("kind")=="GROUP_SCENE" or "MULTI_FIGURE" in groups: categories.append("GROUP_COMPOSITION")
  if any(x in tags+" "+groups for x in ("HAND", "FINGER")): categories.append("HANDS_FINGERS")
  if "ANIMAL" in tags+" "+groups: categories.append("ANIMALS")
  if "ANATOMY" in tags+" "+groups: categories.append("ANATOMY")
  if "CLOTHING" in tags+" "+groups: categories.append("CLOTHING")
 elif pool=="DRAWING_GUIDES":
  category=source_map.get(str((r.get("raw") or {}).get("category","")))
  if category: categories.append(category)
 tag_rules=c.get("tag_category_map",{}); tags=[str(x).upper() for x in r.get("tags",[])+r.get("groups",[])]
 for tag in tags:
  exact=tag_rules.get("exact",{}).get(tag)
  if exact:
   categories.append(exact)
   r.setdefault("category_basis",{}).setdefault(exact,[]).append("SOURCE_REVIEWED_TAG")
  for prefix,category in tag_rules.get("prefix",{}).items():
   if tag.startswith(prefix):
    categories.append(category)
    r.setdefault("category_basis",{}).setdefault(category,[]).append("SOURCE_REVIEWED_TAG")
 title=tag_rules.get("title_exact",{}).get(r.get("title",""))
 if title:
  categories.append(title["category"])
  r.setdefault("category_basis",{}).setdefault(title["category"],[]).append(title["basis"])
 if pool=="POSE_ATLAS": r.setdefault("category_basis",{}).setdefault("POSE_ACTION",[]).append("POSE_ATLAS_SOURCE_GROUP")
 elif pool=="COSTUME_DB_01": r.setdefault("category_basis",{}).setdefault("CLOTHING",[]).append("COSTUME_DATABASE_ITEM")
 elif pool=="PINTEREST_SKETCH":
  for category in categories:
   if category==c["pinterest_role_map"].get(r.get("role")): r.setdefault("category_basis",{}).setdefault(category,[]).append("SOURCE_REVIEWED_PROPOSED_ROLE")
 elif pool=="DRAWING_GUIDES":
  source_category=source_map.get(str((r.get("raw") or {}).get("category","")))
  if source_category: r.setdefault("category_basis",{}).setdefault(source_category,[]).append("SOURCE_REVIEWED_CURATED_CATEGORY")
 if "OTHER" in categories and any(x!="OTHER" for x in categories):
  categories.remove("OTHER"); r.get("category_basis",{}).pop("OTHER",None)
 return list(dict.fromkeys(x for x in categories if x in c["categories"])) or ["OTHER"]
def records(pool: str):
 c=cfg()[pool]; base=(ROOT/c["root"]).resolve(); kind=c["adapter"]
 if kind=="pose_atlas":
  from tools.pose_reference_manager import reference_context
  data=read_json(base/"POSE_GROUPS.json"); out=[]
  for g in data["groups"]:
   for v in g["views"]:
    display=" · ".join(str(x) for x in (g.get("system"),g.get("pose_number"),g.get("pose_name_ru") or g.get("pose_name_ja")) if x)
    out.append({"id":v["source_id"],"title":g.get("pose_name_ru") or g.get("pose_name_ja",""),"group_display":display,"angle":v.get("angle",""),"tags":[g.get("category_ru",""),g.get("pose_family","")],"subject_label":v.get("subject_label","UNKNOWN"),"subject_label_status":v.get("subject_label_status","UNKNOWN"),"sha256":v.get("sha256",""),"relative_path":v.get("relative_path",v.get("reference_path","")),"group_id":g["group_id"],"source_id":v["source_id"],"role":"","review_state":v.get("visual_qa_status","NOT_CHECKED"),"provenance":{"source_id":v["source_id"],"group_id":g["group_id"],"review_notes":v.get("review_notes",""),"review_evidence":v.get("review_evidence","")}})
  for r in out: r["categories"]=categorize(pool,r)
  return base,out
 if kind=="pinterest":
  p=read_json(ROOT/c["candidate_manifest"]); source_rows=read_json(ROOT/c["source_manifest"]).get("records",[]); source_by={x.get("id"):x for x in source_rows}; out=[]
  for r in p["records"]:
   s=source_by.get(r["candidate_id"],{}); bound=bool(s and s.get("sha256")==r.get("sha256") and s.get("proposed_role")==r.get("proposed_role") and s.get("delivery_path")==r.get("path") and s.get("review_fresh") and s.get("collection_selected") and s.get("review_binding_sha256")== (s.get("source_sha256") or s.get("sha256")) and (not s.get("parent_sha256") or s.get("parent_review_binding_sha256")==s.get("parent_sha256")) and bool(s.get("quarantined"))==bool(r.get("quarantined")))
   label=r.get("subject_label","UNKNOWN"); confirmed=bound and not s.get("quarantined") and not r.get("quarantined") and label in ("MALE","FEMALE") and s.get("subject_label")==label and s.get("subject_source_eligible") and r.get("subject_label_sha256")==r.get("sha256")
   provenance={k:r.get(k) for k in ("source_pin_url","source_image_url","parent_id","parent_sha256","bbox","source_catalog_sha256","subject_label_evidence","contact_evidence","review_status","review_evidence","notes","group_id") if k in r}
   provenance.update({k:s.get(k) for k in ("parent_id","parent_sha256","parent_review_binding_sha256","review_binding_sha256","bbox","review_evidence","review_status","source_pin_url","source_image_url") if k in s})
   parent_id=s.get("parent_id") or r.get("parent_id","")
   row={"id":r["candidate_id"],"title":r.get("title",r.get("kind","")),"group_display":parent_id or r.get("title") or r["candidate_id"],"parent_id":parent_id,"angle":"","tags":r.get("tags",[]),"subject_label":label,"subject_label_status":"CONFIRMED" if confirmed else "UNKNOWN","sha256":r.get("sha256",""),"relative_path":r.get("path",""),"role":r.get("proposed_role","") if bound else "","kind":r.get("kind"),"groups":r.get("groups",[]),"review_state":"SOURCE_REVIEW_CURRENT" if bound else "SOURCE_BINDING_STALE","record_binding_status":"CURRENT" if bound else "STALE","provenance":provenance,"raw":r}
   row["categories"]=categorize(pool,row); out.append(row)
  return base,out
 if kind=="csv":
  with (ROOT/c["manifest"]).open(encoding="utf-8-sig",newline="") as f: rows=list(csv.DictReader(f))
  out=[{"id":r["id"],"title":r.get("keywords",""),"tags":[r.get("category","")],"subject_label":"UNKNOWN","subject_label_status":"UNKNOWN","sha256":r.get("sha256",""),"relative_path":r.get("path",""),"role":"CLOTHING_REFERENCE","review_state":r.get("status","NOT_CHECKED"),"provenance":{"source_url":r.get("source_url"),"source_category":r.get("category")},"raw":r} for r in rows]
  for r in out: r["categories"]=categorize(pool,r)
  return base,out
 p=read_json(ROOT/c["manifest"]); out=[]
 crop_meta={x.get("crop_id"):x for x in read_json(ROOT/"OLDMANLUX_SOURCE_COLLECTION/POSITIVE_EXAMPLES.json").get("crops",[])}
 for r in p["items"]:
  label=r.get("subject_label","UNKNOWN"); bound=(label in ("MALE","FEMALE","NO_SUBJECT") and r.get("subject_label_sha256")==r.get("sha256") and bool(r.get("subject_label_evidence")) and r.get("subject_label_evidence")!="UNKNOWN")
  crop=crop_meta.get(r.get("crop_id"),{})
  out.append({"id":r.get("crop_id") or r["asset_id"],"source_asset_id":r["asset_id"],"title":r.get("title_ru") or r.get("text",r.get("alt","")),"tags":r.get("tags",[r.get("category","")]),"subject_label":label,"subject_label_status":"NO_SUBJECT" if label=="NO_SUBJECT" and bound else ("CONFIRMED" if bound else "UNKNOWN"),"sha256":r.get("sha256",""),"relative_path":r.get("local_path",""),"role":"DRAWING_TECHNIQUE","review_state":r.get("status","NOT_CHECKED"),"provenance":{"source_url":r.get("source_url"),"post_url":r.get("post_url"),"duplicate_of":r.get("duplicate_of"),"crop_bounds":r.get("crop_bounds") or crop.get("box"),"declared_sha256":r.get("declared_sha256"),"source_asset_id":r["asset_id"],"source_path":r.get("source_local_path"),"source_sha256":r.get("source_sha256"),"source_review":r.get("source_clean_qa"),"review_notes":r.get("visual_review") or r.get("advice") or r.get("classification_basis"),"crop_id":r.get("crop_id"),"crop_review":crop.get("qa"),"crop_clean_review":crop.get("clean_qa"),"crop_review_evidence":crop.get("qa_evidence"),"crop_review_binding":crop.get("qa_bound_sha256")},"raw":r})
  normalized={**out[-1],"raw":r}
  out[-1]["categories"]=categorize(pool,normalized)
  out[-1]["category_basis"]=normalized.get("category_basis",{})
 return base,out

def validate_pool(pool, deep=False, loaded=None):
 base,rs=loaded or records(pool); ids=[r["id"] for r in rs]; issues=[]
 if len(ids)!=len(set(ids)): issues.append("duplicate_ids")
 for r in rs:
  try: p=safe(base,r["relative_path"])
  except Exception: issues.append("unsafe_path:"+str(r["id"])); continue
  if not p.is_file(): issues.append("missing_file:"+str(r["id"]))
  elif sha(p)!=r["sha256"]: issues.append("stale_hash:"+str(r["id"]))
 if pool=="PINTEREST_SKETCH":
  c=cfg()[pool]; source=read_json(ROOT/c["source_manifest"]).get("records",[]); byid={r.get("id"):r for r in source}
  if len(byid)!=len(source) or set(ids)!=set(byid): issues.append("source_manifest_id_mismatch")
  for r in rs:
   s=byid.get(r["id"])
   if (not s or s.get("sha256")!=r["sha256"] or s.get("subject_label")!=r["subject_label"]
       or s.get("proposed_role")!=r.get("role") or s.get("review_binding_sha256")!=(s.get("source_sha256") or s.get("sha256"))
       or (s.get("parent_sha256") and s.get("parent_review_binding_sha256")!=s.get("parent_sha256"))
       or not s.get("review_fresh") or not s.get("collection_selected") or bool(s.get("quarantined"))!=bool(r.get("raw",{}).get("quarantined"))):
    issues.append("source_review_binding:"+str(r["id"])); continue
   try: source_path=safe(base,s.get("path",""))
   except Exception: issues.append("unsafe_source_path:"+str(r["id"])); continue
   if not source_path.is_file() or sha(source_path)!=s.get("sha256"): issues.append("stale_source_manifest:"+str(r["id"]))
 c=cfg()[pool]
 if pool=="POSE_ATLAS":
  try:
   from tools.pose_reference_manager import validate_library
   atlas=validate_library(base)
   if atlas.get("status")!="CATALOG_COMPLETE": issues.append("atlas_manager_validation:"+str(atlas.get("status")))
  except Exception as e: issues.append("atlas_manager_validation:"+str(e))
 if pool=="DRAWING_GUIDES" and deep:
  try:
   from tools.external_reference_catalog import build_catalog, validate_positive_library
   issues.extend("external_catalog:"+str(x) for x in validate_positive_library(base,build_catalog(base)))
  except Exception as e: issues.append("external_catalog_validation:"+str(e))
 return {"pool":pool,"status":"VALID" if not issues and len(rs)==c["count"] else "ISSUES","records":len(rs),"expected":c["count"],"issues":issues}
def search(pools,query="",tag="",subject="",limit=20,category=""):
 result={}; q=query.casefold(); wanted=category.upper()
 for pool in pools:
  _,rs=records(pool); hits=[]
  for r in rs:
   if wanted and wanted not in r.get("categories",[]): continue
   if q and q not in (r["id"]+" "+r["title"]+" "+" ".join(map(str,r.get("tags",[])))).casefold(): continue
   if tag and tag.casefold() not in " ".join(map(str,r.get("tags",[])+r.get("groups",[]))).casefold(): continue
   if subject and r["subject_label"]!=subject: continue
   hits.append(public_record(pool,r))
  result[pool]=hits
 all_hits=[r for rows in result.values() for r in rows]
 return {"category":wanted or "ALL","total":len(all_hits),"by_source":{p:len(rows) for p,rows in result.items()},"records":all_hits[:limit]}

def category_summary(pools=None):
 pools=pools or list(cfg()); definitions=category_cfg()["categories"]
 counts={name:{"total":0,"by_source":{}} for name in definitions}
 for pool in pools:
  _,rs=records(pool)
  for r in rs:
   for category in r.get("categories",[]):
    counts[category]["total"]+=1; counts[category]["by_source"][pool]=counts[category]["by_source"].get(pool,0)+1
 return [{"category":name,"label_ru":meta["label_ru"],**counts[name]} for name,meta in definitions.items()]

def public_record(pool,r):
 unresolved=r.get("subject_label") not in ("MALE","FEMALE","NO_SUBJECT") or r.get("subject_label_status") not in ("CONFIRMED","NO_SUBJECT") or r.get("record_binding_status","CURRENT")!="CURRENT"
 item={k:v for k,v in r.items() if k!="raw" and not (unresolved and k=="relative_path")}
 if unresolved:
  page_urls={"post_url","source_pin_url","source_post_url","media_source_post_url","media_context_post_url","discovered_via"}
  item["provenance"]={k:v for k,v in r.get("provenance",{}).items() if not k.endswith("path") and (not k.endswith("url") or k in page_urls)}
 item["pool"]=pool
 return item

def build_index(output: Path):
 definitions=category_cfg()["categories"]; data=[]
 for pool in cfg():
  _,rs=records(pool)
  for r in rs:
   item=public_record(pool,r)
   item.update({"kind":r.get("kind",""),"groups":r.get("groups",[])})
   data.append(item)
 counts={name:0 for name in definitions}
 for row in data:
  for name in row.get("categories",[]): counts[name]=counts.get(name,0)+1
 safe_json=json.dumps(data,ensure_ascii=False).replace("<","\\u003c")
 cats=json.dumps([{"id":k,"label":v["label_ru"],"count":counts[k]} for k,v in definitions.items()],ensure_ascii=False)
 html_doc='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Каталог референсов</title><style>body{font:15px system-ui;margin:2rem;color:#20232a;background:#f6f7f9}h1{margin-bottom:.3rem}p{color:#586174}input,select{padding:.55rem;margin:.25rem;border:1px solid #b7bfce;border-radius:6px}table{width:100%;border-collapse:collapse;background:white;margin-top:1rem}th,td{text-align:left;padding:.55rem;border-bottom:1px solid #e4e7ec;vertical-align:top}th{position:sticky;top:0;background:#eef1f6}small{color:#586174}.count{margin:.7rem}</style><h1>Каталог референсов</h1><p>Категории объединяют материалы разных источников; источник остаётся фильтром. Записи не утверждают пригодность для генерации.</p><label>Категория <select id="cat"></select></label><label>Источник <select id="pool"><option value="">Все источники</option></select></label><input id="q" placeholder="Поиск по названию, тегам, ID"><div class="count" id="count"></div><table><thead><tr><th>Категория</th><th>Референс</th><th>Поза / лист</th><th>Ракурс</th><th>Источник</th><th>Теги и фасеты</th><th>Субъект</th><th>Проверка</th><th>Происхождение и заметки</th></tr></thead><tbody id="rows"></tbody></table><script>const data=__DATA__,cats=__CATS__;const cat=document.querySelector('#cat'),pool=document.querySelector('#pool');cat.innerHTML='<option value="">Все категории</option>'+cats.map(x=>`<option value="${x.id}">${x.label} (${x.count})</option>`).join('');[...new Set(data.map(x=>x.pool))].sort().forEach(x=>pool.add(new Option(x,x)));function esc(x){return String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function render(){let c=cat.value,p=pool.value,q=document.querySelector('#q').value.toLowerCase();let a=data.filter(x=>(!c||x.categories.includes(c))&&(!p||x.pool===p)&&(!q||(x.id+' '+x.title+' '+x.tags.concat(x.groups||[]).join(' ')).toLowerCase().includes(q)));document.querySelector('#count').textContent=`${a.length} записей`;document.querySelector('#rows').innerHTML=a.slice(0,5000).map(x=>`<tr><td>${x.categories.map(y=>esc(cats.find(z=>z.id===y)?.label||y)).join(', ')}</td><td><b>${esc(x.title||x.id)}</b><br><small>${esc(x.id)}</small></td><td>${esc(x.group_display||x.parent_id||'')}</td><td>${esc(x.angle||'')}</td><td>${esc(x.pool)}</td><td>${esc(x.tags.concat(x.groups||[]).join(', '))}</td><td>${esc(x.subject_label)}</td><td>${esc(x.review_state)}</td><td><small>${esc(JSON.stringify(x.provenance))}</small></td></tr>`).join('')}[cat,pool,document.querySelector('#q')].forEach(x=>x.addEventListener('input',render));render();</script></html>'''.replace("__DATA__",safe_json).replace("__CATS__",cats)
 html_doc=html_doc.replace("Происхождение и заметки", "Примечания").replace("esc(JSON.stringify(x.provenance))", "esc(x.provenance.review_notes || x.provenance.contact_evidence || 'Происхождение сохранено')")
 output.parent.mkdir(parents=True,exist_ok=True); output.write_text(html_doc,encoding="utf-8")
 return {"path":str(output.resolve()),"records":len(data),"categories":len(definitions),"embedded_images":False}

def select(pool,ident,role,subject=""):
 c=cfg()[pool]; base,rs=records(pool); matches=[r for r in rs if r["id"]==ident]
 if pool=="DRAWING_GUIDES" and not matches:
  raise PoolError("candidate is not a current curated source/crop ID")
 if len(matches)!=1: raise PoolError("candidate ID missing or duplicated")
 r=matches[0]
 if role not in c["roles"] or (r.get("role") and role!=r["role"]): raise PoolError("role is not allowed by source contract")
 if pool=="PINTEREST_SKETCH":
  if r.get("record_binding_status")!="CURRENT": raise PoolError("candidate/source/review binding is stale")
  special=role!="HUMAN_POSE_SOFT"
  if not special:
   if r["kind"]!="SINGLE_POSE": raise PoolError("human pose role requires a single pose source")
   if r["subject_label"] not in ("MALE","FEMALE") or r["subject_label_status"]!="CONFIRMED": raise PoolError("classification-required; image path withheld")
   if subject!=r["subject_label"]: raise PoolError("--subject must exactly match confirmed subject label")
  source_rows=read_json(ROOT/c["source_manifest"]).get("records",[])
  source=[x for x in source_rows if x.get("id")==ident]
  if (len(source)!=1 or source[0].get("sha256")!=r["sha256"] or source[0].get("subject_label")!=r["subject_label"]
      or source[0].get("proposed_role")!=role or source[0].get("kind")!=r.get("kind")
      or source[0].get("delivery_path")!=r.get("relative_path") or source[0].get("review_binding_sha256")!=(source[0].get("source_sha256") or source[0].get("sha256"))
      or (source[0].get("parent_sha256") and source[0].get("parent_review_binding_sha256")!=source[0].get("parent_sha256"))
      or not source[0].get("review_fresh") or not source[0].get("collection_selected")
      or bool(source[0].get("quarantined"))!=bool(r.get("raw",{}).get("quarantined"))):
   raise PoolError("candidate/source/review binding is stale")
  if not special and (source[0].get("quarantined") or r.get("raw",{}).get("quarantined")):
   raise PoolError("quarantined human pose; image path withheld")
  sp=safe(base,source[0].get("path",""))
  if not sp.is_file() or sha(sp)!=source[0].get("sha256"):
   raise PoolError("source manifest file/hash is stale")
  if special:
   dp=safe(base,r["relative_path"])
   if not dp.is_file() or sha(dp)!=r["sha256"]: raise PoolError("candidate delivery file/hash is stale")
   meta={k:v for k,v in r.get("provenance",{}).items() if not k.endswith("path") and not k.endswith("url")}
   meta.update({"source_id":ident,"candidate_sha256":r["sha256"],"source_sha256":source[0].get("source_sha256"),"group_id":source[0].get("parent_id"),"parent_id":source[0].get("parent_id"),"parent_sha256":source[0].get("parent_sha256"),"bbox":source[0].get("bbox"),"review_binding_sha256":source[0].get("review_binding_sha256"),"review_status":source[0].get("review_status"),"review_evidence":source[0].get("review_evidence"),"notes":source[0].get("notes")})
   return {"schema_version":1,"type":"CANDIDATE_SELECTION","pool":pool,"candidate_id":ident,"role":role,"categories":r.get("categories",[]),"tags":r.get("tags",[]),"kind":r.get("kind"),"subject_label":r.get("subject_label","UNKNOWN"),"classification_status":"CLASSIFICATION_REQUIRED","role_review_status":"ROLE_REVIEW_REQUIRED","generation_eligible":False,"approval_status":"NOT_APPROVED","rights_status":source[0].get("rights_status","UNVERIFIED_LOCAL_REFERENCE_ONLY"),"image_attachment_allowed":False,"provenance":meta,"notice":"Metadata-only candidate. Subject/role review and later source-specific preparation remain required."}
 p=safe(base,r["relative_path"])
 if not p.is_file() or sha(p)!=r["sha256"]: raise PoolError("candidate source hash is stale or file missing")
 if pool=="POSE_ATLAS":
  from tools.pose_reference_manager import PoseReferenceError, reference_context
  anatomy={"MALE":"MALE_ANATOMY","FEMALE":"FEMALE_ANATOMY"}.get(subject.upper(),"ANY")
  try: fresh=reference_context(base,anatomy,source_id=ident)
  except PoseReferenceError as e: raise PoolError(f"atlas source not selectable: {e}") from e
  v=[v for g in fresh["groups"] for v in g["views"] if v["source_id"]==ident]
  if len(v)!=1: raise PoolError("atlas source not selectable")
 if pool=="DRAWING_GUIDES":
  from tools.external_reference_catalog import build_catalog, select_candidates, select_positive_candidates
  catalog=build_catalog(base)
  if r.get("raw",{}).get("crop_id")==ident:
   try:
    delegated=select_positive_candidates(base,catalog,[ident],role)
    return {**delegated,"pool":pool,"candidate_id":ident,"generation_eligible":False,"approval_status":"NOT_APPROVED","provenance":r.get("provenance",{})}
   except ValueError as e: raise PoolError(str(e)) from e
  try: delegated=select_candidates(base,catalog,[ident],role)
  except ValueError as e: raise PoolError(str(e)) from e
  return {**delegated,"pool":pool,"candidate_id":ident,"generation_eligible":False,"approval_status":"NOT_APPROVED"}
 payload={"schema_version":1,"type":"CANDIDATE_SELECTION","pool":pool,"candidate_id":ident,"role":role,"generation_eligible":False,"approval_status":"NOT_APPROVED","sha256":r["sha256"],"path":str(p),"provenance":r.get("provenance",{}),"restrictions":{"source_pool_roles":c["roles"],"visual_validation_required":pool in ("COSTUME_DB_01","DRAWING_GUIDES"),"rights_status":"UNVERIFIED_LOCAL_REFERENCE_ONLY"}}
 return payload

def main():
 ap=argparse.ArgumentParser(); sp=ap.add_subparsers(dest="cmd",required=True)
 li=sp.add_parser("list"); li.add_argument("--json",action="store_true")
 ca=sp.add_parser("categories"); ca.add_argument("--pool",action="append",choices=cfg().keys())
 se=sp.add_parser("search"); se.add_argument("--pool",action="append",choices=cfg().keys()); se.add_argument("--category",choices=category_cfg()["categories"]); se.add_argument("--query",default=""); se.add_argument("--tag",default=""); se.add_argument("--subject",choices=["MALE","FEMALE"]); se.add_argument("--limit",type=int,default=20)
 va=sp.add_parser("validate"); va.add_argument("--pool",choices=cfg().keys())
 ss=sp.add_parser("select"); ss.add_argument("--pool",required=True,choices=cfg().keys()); ss.add_argument("--id",required=True); ss.add_argument("--role",required=True); ss.add_argument("--subject",default=""); ss.add_argument("--output",type=Path,required=True)
 bi=sp.add_parser("build-index"); bi.add_argument("--output",type=Path,required=True)
 a=ap.parse_args()
 try:
  if a.cmd=="list":
   out={}
   for p in cfg():
    loaded=records(p); report=validate_pool(p,loaded=loaded)
    out[p]={"source_count":len(loaded[1]),"status":report["status"]}
  elif a.cmd=="categories": out=category_summary(a.pool)
  elif a.cmd=="search": out=search(a.pool or list(cfg()),a.query,a.tag,a.subject or "",a.limit,a.category or "")
  elif a.cmd=="validate":
   out=[validate_pool(a.pool,deep=True)] if a.pool else [validate_pool(p,deep=True) for p in cfg()]
   print(json.dumps(out,ensure_ascii=False,indent=2)); return 1 if any(x["status"]!="VALID" for x in out) else 0
  elif a.cmd=="build-index": out=build_index(a.output)
  else:
   out=select(a.pool,a.id,a.role,a.subject); a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
  print(json.dumps(out,ensure_ascii=False,indent=2)); return 0
 except Exception as e: print(json.dumps({"error":str(e)},ensure_ascii=False)); return 2
if __name__=="__main__": sys.exit(main())



