"""Evidence-derived reference discovery database. Never grants attachment approval."""
from __future__ import annotations
import argparse
import collections
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import reference_pool_manager as pools

DEFAULT = ROOT / 'work/reference-knowledge-20261113'
DESCRIPTIONS = ROOT / 'config/reference_knowledge_descriptions.json'
POLICY = {
 'POSE_ATLAS': ('Позы в нескольких ракурсах', 'Положение тела, опоры и проекция камеры', 'Не переносить личность, телосложение, одежду или стиль; сравнивать ракурсы только одной группы.'),
 'PINTEREST_SKETCH': ('Эскизы поз, жестов и взаимодействий', 'Мягкая подсказка позы, жеста или композиции в пределах исходной роли', 'Не переносить личность, телосложение, стиль, одежду и конструкцию оружия. Фрагменты листа не являются ракурсами одного тела.'),
 'COSTUME_DB_01': ('Каталог одежды', 'Поиск силуэта, состава комплекта и деталей одежды', 'Ключевые слова источника не доказывают посадку, конструкцию или пол. Нужны визуальная классификация и штатный wardrobe/item процесс.'),
 'DRAWING_GUIDES': ('Учебные фрагменты', 'Вопросы к плану и QA по указанной теме', 'Не переносить тело или стиль; сохранить исходные allowed_uses/forbidden_uses. Учебный пример не является утверждённым персонажем.'),
 'SODABABYX': ('Формы тела, посадка одежды, позы', 'Форма тела/части → контакт и посадка одежды → поза', 'Не переносить стиль рисовки. Не менять подтверждённое тело или одежду персонажа без явного решения. Лист и одиночник не два независимых источника.'),
 'SODABABYX_SINGLE': ('Одиночные формы, посадка и позы', 'Точный вариант из принятой коллекции SODABABYX', 'Принятие коллекции не разрешает применение к любому кадру. Нейтральная схема не получает мужскую/женскую метку. Стиль запрещён.'),
}
TERMS = {
 'COMBAT':'бой', 'COMPLEX_POSE':'сложная поза', 'VIEW:DYNAMIC':'динамический ракурс',
 'VIEW:FORESHORTENING':'перспективное сокращение', 'ANATOMY:CONSTRUCTION':'конструкция тела',
 'HAND_GESTURE':'жест кисти', 'FINGER_GESTURE':'жест пальцев', 'HAND:GRIP':'хват',
 'HAND:FIST':'кулак', 'HAND:OPEN_PALM':'открытая ладонь', 'HAND:POINTING':'указательный жест',
 'HAND:WRIST_BEND':'сгиб запястья', 'HAND:TWO_HANDS':'две кисти', 'HAND:PINCH':'щипковый хват',
 'INTERACTION:RIDING':'верхом', 'INTERACTION:EMBRACE':'объятия', 'MARTIAL_ARTS':'единоборства',
 'WEAPON:HAMMER':'молот', 'WEAPON:SWORD':'меч', 'WEAPON:SCYTHE':'коса', 'WEAPON:SPEAR':'копьё',
 'WEAPON:BOW':'лук', 'WEAPON:GUN':'огнестрельное оружие', 'WEAPON:VARIED':'разное оружие',
 'ANIMAL:HORSE':'лошадь', 'ANIMAL:WOLF':'волк', 'ANIMAL:CANID':'псовые', 'ANIMAL':'животное',
 'TECHNIQUE:BLOCK':'блок', 'TECHNIQUE:GRAPPLE':'захват', 'TECHNIQUE:PUNCH':'удар рукой',
 'TECHNIQUE:KICK':'удар ногой', 'POSE:CROUCH':'присед', 'POSE:SITTING':'сидя', 'POSE:GUARD':'защитная стойка',
 'ARMS:OVERHEAD':'руки над головой', 'STANDING':'стоя', 'SITTING':'сидя', 'LYING':'лёжа',
 'front':'спереди', 'back':'сзади', 'left':'слева', 'right':'справа', 'side':'сбоку',
 'high':'сверху', 'low':'снизу', 'oblique':'косой ракурс три четверти',
 'SOLO_FULL_BODY':'одна фигура целиком', 'BODY_PART:HAND':'кисть', 'DETAIL:FIST':'кулак',
 'DETAIL:VIEWS':'разные ракурсы', 'LESSON:ANATOMY':'анатомия',
 'blouse':'блузка', 'pants':'брюки', 'boots':'сапоги', 'skirt':'юбка', 'dress':'платье',
 'jacket':'куртка жакет', 'coat':'пальто', 'shirt':'рубашка', 'shorts':'шорты', 'socks':'носки',
 'heels':'туфли каблук', 'sneakers':'кроссовки', 'swimsuit':'купальник', 'bikini':'бикини купальник',
 'lingerie':'бельё', 'bra':'бюстгальтер', 'sweater':'свитер', 'hoodie':'худи', 'gloves':'перчатки',
 'hat':'шляпа', 'bag':'сумка', 'necklace':'ожерелье', 'belt':'ремень', 'denim':'джинсовая ткань',
 'leather':'кожа', 'lace':'кружево', 'sleeveless':'без рукавов', 'pleated':'складки плиссе',
 '秋':'осень', '夏':'лето', '冬':'зима', '春':'весна',
}

def dump(path, data):
 path.parent.mkdir(parents=True, exist_ok=True)
 path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

def strings(value):
 if isinstance(value,dict): return ' '.join(strings(v) for v in value.values())
 if isinstance(value,list): return ' '.join(strings(v) for v in value)
 return str(value or '')

def aliases(text):
 tokens=set(re.findall(r'[\w:]+',text))
 return [ru for en,ru in TERMS.items() if en in tokens or en.lower() in {t.lower() for t in tokens}]

def input_paths():
 out=[pools.REGISTRY,pools.CATEGORY_REGISTRY,Path(__file__)]
 for c in pools.cfg().values():
  out.extend(ROOT/c[k] for k in ('manifest','candidate_manifest','source_manifest') if c.get(k))
  if c['adapter']=='pose_atlas': out.append(ROOT/c['root']/'POSE_GROUPS.json')
 out += [ROOT/'tools/reference_pool_manager.py',ROOT/'tools/pose_reference_manager.py',ROOT/'tools/shape_fit_reference_pool.py',ROOT/'tools/reference_knowledge_ui.html',ROOT/'OLDMANLUX_SOURCE_COLLECTION/POSITIVE_EXAMPLES.json',ROOT/'SODABABYX_SOURCE_COLLECTION/SINGLES_MANIFEST.json',ROOT/'SODABABYX_SOURCE_COLLECTION/collages/INDEX.json',DESCRIPTIONS]
 out += [pools.safe(ROOT,r['evidence']['sheet']) for r in pools.read_json(DESCRIPTIONS)['records']]
 return list(dict.fromkeys(out))

def snapshot():
 return {str(p.relative_to(ROOT)).replace('\\','/'):pools.sha(p) if p.is_file() else None for p in input_paths()}

def normalize(pool,base,r):
 raw=r.get('raw',{}); prov=r.get('provenance',{})
 key=pool+':'+r['id']; tags=list(dict.fromkeys(r.get('tags',[])+r.get('groups',[])))
 categories=r.get('categories',[])
 category_names=[pools.category_cfg()['categories'].get(c,{}).get('label_ru',c) for c in categories]
 title=r.get('title') or r['id']; angle=r.get('angle','')
 translated=aliases(' '.join([title,angle]+tags))
 observation=raw.get('summary_ru') or raw.get('review',{}).get('observations') or ''
 basis='Существующее визуальное описание' if observation else 'Метаданные источника; новый визуальный осмотр не заявлен'
 if pool=='PINTEREST_SKETCH':
  title={'SINGLE_POSE':'Одиночная поза','GROUP_SCENE':'Групповая сцена','HAND_GESTURE':'Жест кисти'}.get(title,title)
 if pool=='COSTUME_DB_01': observation='Ключевые слова исходного каталога: '+title
 if not observation: observation='Тема: '+title+'. '+('Признаки: '+', '.join(dict.fromkeys(translated+tags))+'. ' if tags else '')
 description=observation
 if angle: description+=' Ракурс: '+TERMS.get(angle,angle)+'.'
 if pool=='POSE_ATLAS': description+=' Группа одной позы: '+r.get('group_id','')+'.'
 limitations=[POLICY[pool][2]]
 if raw.get('review',{}).get('limitations'): limitations.append(raw['review']['limitations'])
 for field in ('forbidden_influence','forbidden_uses','allowed_uses','allowed_influence','curation_reason'):
  if raw.get(field): limitations.append(field+': '+strings(raw[field]))
 if prov.get('review_notes') and prov['review_notes'] not in ('PASS','UNKNOWN'): limitations.append(strings(prov['review_notes']))
 confirmed=r.get('subject_label') in ('MALE','FEMALE','NO_SUBJECT') and r.get('subject_label_status') in ('CONFIRMED','NO_SUBJECT') and r.get('record_binding_status','CURRENT')=='CURRENT'
 missing=[]
 if not confirmed: missing.append('Пол/субъект не подтверждён для открытия и вложения; только метаданные.')
 if not angle: missing.append('Ракурс отдельно не описан; проверить оригинал перед выбором.')
 if basis.startswith('Метаданные'): missing.append('Описание не заменяет визуальную проверку содержания.')
 roles=raw.get('allowed_roles') or ([r['role']] if r.get('role') else pools.cfg()[pool]['roles'])
 rec={'key':key,'pool':pool,'id':r['id'],'title':title,'description':description,'description_basis':basis,
      'categories':categories,'category_names':category_names,'tags':tags,'aliases_ru':translated,
      'subject':r.get('subject_label','UNKNOWN'),'subject_status':r.get('subject_label_status','UNKNOWN'),
      'angle':angle,'group':r.get('group_id') or r.get('parent_id') or '', 'sha256':r.get('sha256',''),
      'use_for':POLICY[pool][1], 'roles':roles,'limitations':limitations,'missing_checks':missing,
      'source_review':r.get('review_state','NOT_CHECKED'),'description_evidence':{'fields':['title','tags','angle','raw.summary_ru','raw.review.observations'],'source_sha256':r.get('sha256','')},
      'generation_eligible':False,'status':'DISCOVERY_ONLY','preview_allowed':False,
      'post_url':prov.get('post_url') or prov.get('source_pin_url') or '',
      'verification':['Сопоставить с буквальным ТЗ: роль, пол, анатомия, видимые области и ракурс.','Проверить полный выбранный файл и текущий SHA256.','Записать что подходит, что не подходит и почему; при несовместимости выбрать другой ID.','Выполнить штатный select/prepare-generation и exact-call validation.']}
 if confirmed and r.get('relative_path'):
  p=pools.safe(base,r['relative_path'])
  if p.is_file() and pools.sha(p)==r.get('sha256'):
   rec.update(preview_allowed=True,path=str(p.relative_to(ROOT)).replace('\\','/'))
  else: rec['missing_checks'].append('Текущий файл отсутствует или хеш не совпадает; просмотр закрыт.')
 return rec

def collect():
 data=[]
 for pool in pools.cfg():
  base,rs=pools.records(pool)
  data.extend(normalize(pool,base,r) for r in rs)
 parent={r['id']:r for r in data if r['pool']=='SODABABYX'}
 b=ROOT/'SODABABYX_SOURCE_COLLECTION'
 singles=pools.read_json(b/'SINGLES_MANIFEST.json')
 for r in singles['records']:
  src=parent[r['source_id']]
  rec={**src,'key':'SODABABYX_SINGLE:'+r['id'],'pool':'SODABABYX_SINGLE','id':r['id'],
       'title':r['name_ru']+' · '+r['title_ru'],'group':r['source_id'],'variant':r['variant_id'],
       'description':r['name_ru']+'. Отдельный вариант из листа «'+r['title_ru']+'». '+r.get('subject_label_evidence',''),
       'description_basis':'Название варианта и существующий осмотр исходного листа; описание листа не выдано за индивидуальные измерения.',
       'tags':r['tags_ru'],'aliases_ru':aliases(strings(r['tags_ru'])),'subject':r['subject_label'],
       'subject_status':r['subject_label_status'],'sha256':r['sha256'],'topic':r['subcategory'],
       'use_for':POLICY['SODABABYX_SINGLE'][1],'limitations':[POLICY['SODABABYX_SINGLE'][2],r.get('limitations',''),r.get('subject_usage_rule','')],
       'missing_checks':['Проверить отдельный вариант в полном разрешении под текущий запрос.'],
       'description_evidence':{'fields':['name_ru','title_ru','subject_label_evidence'],'source_sha256':r['source_sha256'],'single_sha256':r['sha256']}}
  rec.pop('path',None)
  p=pools.safe(b,r['local_path'])
  neutral_mouth=(r['subject_label']=='NEUTRAL' and r['subcategory']=='mouth' and r.get('generation_subject_gate')=='NEUTRAL_REQUIRES_SCOPED_REVIEW')
  if neutral_mouth:
   rec['roles']=['MOUTH_EXPRESSION_REFERENCE']
   rec['missing_checks'].append('Только выражение рта: штатный selector требует явный пол целевого персонажа и отдельную проверку роли; NEUTRAL не меняется.')
  confirmed=(r['subject_label'] in ('MALE','FEMALE') or neutral_mouth) and r.get('subject_label_sha256')==r['sha256'] and r['subject_label_status']=='CONFIRMED'
  rec['preview_allowed']=bool(confirmed and p.is_file() and pools.sha(p)==r['sha256'])
  if rec['preview_allowed']: rec['path']=str(p.relative_to(ROOT)).replace('\\','/')
  else: rec['missing_checks'].append('NEUTRAL/неподтверждённый субъект: не является разрешением вложения; автоматический просмотр в общей базе закрыт.')
  data.append(rec)
 reviewed=pools.read_json(DESCRIPTIONS)['records']
 descriptions={r['key']:r for r in reviewed}
 checked_sheets=set()
 for item in reviewed:
  evidence=item['evidence'];sheet=pools.safe(ROOT,evidence['sheet'])
  if sheet not in checked_sheets:
   if sheet.is_file() and pools.sha(sheet)!=evidence['sheet_sha256']: raise ValueError('Description evidence sheet changed')
   checked_sheets.add(sheet)
 for r in data:
  r['selection']={'tool':'reference_pool_manager select','pool':'SODABABYX' if r['pool']=='SODABABYX_SINGLE' else r['pool'],
                  'id':r['id'],'allowed_roles':r['roles'],'subject':r['subject'] if r['subject'] in ('MALE','FEMALE') else 'EXPLICIT_TARGET_SEX_REQUIRED',
                  'variant':r.get('variant','EXPLICIT_SHEET_VARIANT_REQUIRED' if r['pool']=='SODABABYX' else '')}
  if r['key'] in descriptions:
   item=descriptions[r['key']]
   if item['sha256']!=r['sha256']: raise ValueError('Description source changed: '+r['key'])
   r['source_title']=r['title']
   r['description']=item['description']
   r['title']=item['description'].split(':')[0].split('.')[0]
   r['description_basis']='Индивидуальное визуальное описание по обзорному листу; полный оригинал проверяется под конкретное ТЗ.'
   r['description_evidence']=item['evidence']|{'source_sha256':r['sha256']}
   if not pools.safe(ROOT,item['evidence']['sheet']).is_file():
    r['description_evidence']['local_sheet_status']='NOT_INSTALLED'
    r['missing_checks'].append('Обзорный лист исторического описания не установлен; сохранённая запись осмотра не заменяет проверку оригинала.')
   r['missing_checks']=[x for x in r['missing_checks'] if x!='Описание не заменяет визуальную проверку содержания.']
   r['missing_checks'].append('Проверить точность анатомии и применимость выбранной позы на оригинале; обзорное описание этого не утверждает.')
  r['search_text']=' '.join([r['key'],r['title'],r['description'],strings(r['tags']),strings(r['aliases_ru']),strings(r['category_names']),r.get('topic','')]).casefold()
 return data

def make_collages(data,out):
 from PIL import Image,ImageDraw,ImageOps
 from tools.build_shape_fit_collages import font,lines
 groups=collections.defaultdict(list)
 # Existing SODA collages stay in their original accepted collection; new overview pages
 # include only confirmed matching subjects and current source hashes.
 for r in data:
  if not r['preview_allowed'] or r['pool']=='SODABABYX': continue
  topic=r.get('topic') or (r['tags'][0] if r['tags'] else r['categories'][0])
  groups[(r['pool'],topic,r['subject'])].append(r)
 pages=[]
 for (pool,topic,sex),rows in sorted(groups.items()):
  page_size=18 if pool=='SODABABYX_SINGLE' and topic=='mouth' else 12
  for start in range(0,len(rows),page_size):
   chunk=rows[start:start+page_size]; ident=hashlib.sha256(f'{pool}/{topic}/{sex}/{start}'.encode()).hexdigest()[:16]
   rel='collages/'+ident+'.jpg'; canvas=Image.new('RGB',(1440,110+450*((len(chunk)+3)//4)), '#e8edf3'); draw=ImageDraw.Draw(canvas)
   lines(draw,f'{pool} · {topic} · {sex} · {start//12+1}',(18,10),1400,font(24,True),'#172235',2)
   cells=[]
   for i,r in enumerate(chunk):
    x=(i%4)*360;y=100+(i//4)*450
    with Image.open(ROOT/r['path']) as im:
     im=ImageOps.contain(im.convert('RGBA'),(344,337))
     canvas.paste(im,(x+(360-im.width)//2,y),im)
    lines(draw,f'{i+1:02d} '+r['title'],(x+8,y+342),344,font(17,True),'#172235',3)
    draw.text((x+8,y+417),r['id'][-36:],font=font(12),fill='#34425c')
    r['overview']={'path':rel,'cell':i+1}
    cells.append({'cell':i+1,'key':r['key'],'sha256':r['sha256']})
   path=out/rel;path.parent.mkdir(exist_ok=True,parents=True);canvas.save(path,quality=90)
   pages.append({'path':rel,'pool':pool,'topic':topic,'subject':sex,'sha256':pools.sha(path),'cells':cells})
 return pages

def build(out):
 out.mkdir(parents=True,exist_ok=True)
 before=snapshot();data=collect(); pages=make_collages(data,out)
 assert before==snapshot(),'Sources changed during build; rebuild required'
 summary=[]
 for pool,policy in POLICY.items():
  rows=[r for r in data if r['pool']==pool]
  summary.append({'pool':pool,'name':policy[0],'use_for':policy[1],'limits':policy[2],'count':len(rows),'preview_count':sum(r['preview_allowed'] for r in rows),'metadata_only':sum(not r['preview_allowed'] for r in rows)})
 bundle={'schema_version':1,'status':'DISCOVERY_ONLY','built_at':datetime.now(timezone.utc).isoformat(),'inputs':before,'summary':summary,'records':data,'collages':pages}
 dump(out/'DATABASE.json',bundle)
 db=sqlite3.connect(out/'references.sqlite3')
 with db:
  db.execute('DROP TABLE IF EXISTS refs');db.execute('DROP TABLE IF EXISTS search')
  db.execute('CREATE TABLE refs (key TEXT PRIMARY KEY,pool TEXT,subject TEXT,description TEXT,payload TEXT)')
  db.execute('CREATE VIRTUAL TABLE search USING fts5(key UNINDEXED, content, tokenize="unicode61")')
  db.executemany('INSERT INTO refs VALUES (?,?,?,?,?)',[(r['key'],r['pool'],r['subject'],r['description'],json.dumps(r,ensure_ascii=False)) for r in data])
  db.executemany('INSERT INTO search VALUES (?,?)',[(r['key'],r['search_text']) for r in data])
 db.close()
 template=(ROOT/'tools/reference_knowledge_ui.html').read_text('utf-8')
 (out/'index.html').write_text(template.replace('__DATABASE__',json.dumps(bundle,ensure_ascii=False).replace('<','\\u003c')),encoding='utf-8')
 return {'records':len(data),'collages':len(pages),'summary':summary,'index':str(out/'index.html')}

def load(out):
 data=pools.read_json(out/'DATABASE.json')
 if data['inputs']!=snapshot(): raise ValueError('База устарела: выполнить build до поиска или сборки набора')
 return data

def search(data,query='',pool='',subject='',limit=12):
 terms=query.casefold().split()
 return [r for r in data['records'] if (not pool or r['pool']==pool) and (not subject or r['subject']==subject) and all(t in r['search_text'] for t in terms)][:limit]

def candidate_set(data,keys,brief):
 by={r['key']:r for r in data['records']};chosen=[]
 for key in dict.fromkeys(keys):
  if key not in by: raise ValueError('Unknown key: '+key)
  r=by[key]
  if r.get('path'):
   source=pools.safe(ROOT,r['path'])
   if not source.is_file() or pools.sha(source)!=r['sha256']: raise ValueError('Выбранный оригинал изменён: '+key)
  chosen.append({k:r[k] for k in ('key','sha256','title','description','subject','roles','limitations','missing_checks')})
  if r.get('selection'): chosen[-1]['selection']=r['selection']
  chosen[-1].update(reason='REQUIRED: указать соответствие ТЗ',visual_review='NOT_CHECKED',role_review='NOT_CHECKED',attachment_allowed=False)
 return {'status':'CANDIDATES_NOT_APPROVED','brief':brief,'inputs':data['inputs'],'items':chosen,'next':'Проверить выбранные оригиналы и штатно подготовить набор. Этот файл не является exact call.'}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--directory',type=Path,default=DEFAULT)
 sp=ap.add_subparsers(dest='command',required=True);sp.add_parser('build');sp.add_parser('overview')
 se=sp.add_parser('search');se.add_argument('--query',default='');se.add_argument('--pool',default='');se.add_argument('--subject',default='');se.add_argument('--limit',type=int,default=12)
 show=sp.add_parser('show');show.add_argument('--key',required=True)
 pack=sp.add_parser('set');pack.add_argument('--key',action='append',required=True);pack.add_argument('--brief',required=True);pack.add_argument('--output',type=Path,required=True)
 args=ap.parse_args()
 if args.command=='build': result=build(args.directory)
 else:
  data=load(args.directory)
  if args.command=='overview': result=data['summary']
  elif args.command=='search':
   result=[{k:r[k] for k in ('key','title','description','description_basis','subject','roles','selection','preview_allowed','missing_checks')}|({'overview':r['overview']} if r.get('overview') else {}) for r in search(data,args.query,args.pool,args.subject,args.limit)]
  elif args.command=='show': result=next(r for r in data['records'] if r['key']==args.key)
  else: result=candidate_set(data,args.key,args.brief);dump(args.output,result)
 print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
 if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
 try: main()
 except (ValueError,KeyError,StopIteration) as e: print(str(e),file=sys.stderr);sys.exit(2)
