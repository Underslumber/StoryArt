"""Deterministic discovery sheets of accepted singles; never generator inputs."""
from __future__ import annotations
import argparse
import collections
import hashlib
import html
import json
import math
import shutil
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'SODABABYX_SOURCE_COLLECTION'
OUT = BASE / 'collages'
MANIFEST = BASE / 'SINGLES_MANIFEST.json'
SEX = {'MALE': 'Мужские', 'FEMALE': 'Женские', 'NEUTRAL': 'Нейтральные'}

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def font(size, bold=False):
    names = [Path('C:/Windows/Fonts') / ('arialbd.ttf' if bold else 'arial.ttf'),
             Path('/usr/share/fonts/truetype/dejavu') / ('DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf')]
    return ImageFont.truetype(str(next(p for p in names if p.is_file())), size)

def lines(draw, text, xy, width, face, fill, maximum=3):
    words = text.split(); rows=[]; line=''
    for word in words:
        trial=(line+' '+word).strip()
        if draw.textlength(trial,font=face)>width and line:
            rows.append(line);line=word
        else: line=trial
    rows.append(line)
    for n, row in enumerate(rows[:maximum]):
        draw.text((xy[0],xy[1]+n*(face.size+5)),row,font=face,fill=fill)

def build():
    manifest=json.loads(MANIFEST.read_text('utf-8')); rows=manifest['records']
    assert manifest.get('collection_acceptance'), 'Collection acceptance required'
    groups=collections.defaultdict(list)
    for row in rows:
        label=row['subject_label']
        assert label in SEX and row['subject_label_status']=='CONFIRMED',row['id']
        assert row['subject_label_sha256']==row['sha256'] and row.get('collection_accepted'),row['id']
        path=(BASE/row['local_path']).resolve();path.relative_to(BASE.resolve())
        assert sha(path)==row['sha256'],row['id']
        groups[(row['priority'],row['subcategory'],label)].append(row)
    OUT.mkdir(exist_ok=True)
    index={'schema_version':1,'purpose':'DISCOVERY_ONLY','manifest_sha256':sha(MANIFEST),
           'source_manifest':'SINGLES_MANIFEST.json','records':[]}
    for (priority,topic,subject), members in sorted(groups.items()):
        limit=18 if topic=='mouth' else 12
        for offset in range(0,len(members),limit):
            batch=members[offset:offset+limit];page=offset//limit+1
            cols=min(4,len(batch));cw=400;ch=300 if topic=='mouth' else 470
            canvas=Image.new('RGB',(cols*cw,110+math.ceil(len(batch)/cols)*ch+40),'#f0f1ed')
            draw=ImageDraw.Draw(canvas)
            title=f"{batch[0]['subcategory_ru']} · {SEX[subject]} · {page}"
            draw.text((18,12),title,font=font(30,True),fill='#18252b')
            draw.text((18,57),'ПОИСК / DISCOVERY ONLY — в генерацию передавать одиночник',font=font(19),fill='#35464c')
            cells=[]
            for j,row in enumerate(batch):
                x=j%cols*cw;y=110+j//cols*ch;cell=offset+j+1
                draw.rounded_rectangle((x+7,y+5,x+cw-7,y+ch-5),radius=8,fill='white',outline='#c3cbc8')
                heading=f"{cell:02d}  {row['label']}"
                heading_font=font(20,True)
                while draw.textlength(heading,font=heading_font)>cw-36 and heading_font.size>12:
                    heading_font=font(heading_font.size-1,True)
                assert draw.textlength(heading,font=heading_font)<=cw-36,row['id']
                draw.text((x+18,y+13),heading,font=heading_font,fill='#172c3a')
                im=Image.open(BASE/row['local_path']).convert('RGBA')
                preview=ImageOps.contain(im,(cw-32,ch-135),Image.Resampling.LANCZOS)
                canvas.paste(preview,(x+(cw-preview.width)//2,y+44+(ch-135-preview.height)//2),preview)
                lines(draw,row['name_ru'],(x+17,y+ch-84),cw-34,font(18,True),'#172c3a',2)
                # Exact ID is also in INDEX.json; split on source/variant boundary.
                a,b=row['id'].split('__',1)
                draw.text((x+17,y+ch-40),a,font=font(14),fill='#48575b')
                draw.text((x+17,y+ch-23),'__'+b,font=font(14),fill='#48575b')
                cells.append({'cell':cell,'id':row['id'],'name_ru':row['name_ru'],'label':row['label'],
                    'variant_id':row['variant_id'],'single_path':row['local_path'],'sha256':row['sha256'],
                    'subject_label':subject,'tags_ru':row['tags_ru']})
            draw.text((18,canvas.height-29),f"{len(batch)} карточек · {subject} · оригиналы и пропорции сохранены",font=font(17),fill='#48575b')
            name=f'{topic}__{subject}__p{page:02}.jpg';path=OUT/name
            canvas.save(path,quality=94,subsampling=0)
            index['records'].append({'topic':topic,'topic_ru':batch[0]['subcategory_ru'],'priority':priority,
                'subject_label':subject,'page':page,'path':'collages/'+name,'sha256':sha(path),
                'width':canvas.width,'height':canvas.height,'cells':cells})
    ids=[c['id'] for p in index['records'] for c in p['cells']]
    assert len(ids)==len(set(ids))==len(rows) and set(ids)=={r['id'] for r in rows}
    (OUT/'INDEX.json').write_text(json.dumps(index,ensure_ascii=False,indent=2),encoding='utf-8')
    topics=['# Темы для быстрого поиска','', '| Тема | Название | Пол | Карточек | Коллаж |', '|---|---|---|---:|---|']
    for p in index['records']:
        topics.append(f"| {p['topic']} | {p['topic_ru']} | {p['subject_label']} | {len(p['cells'])} | [{p['page']}]({Path(p['path']).name}) |")
    (OUT/'TOPICS.md').write_text('\n'.join(topics)+'\n',encoding='utf-8')
    cards=[]
    for p in index['records']:
        name=Path(p['path']).name; label=f"{p['topic_ru']} · {SEX[p['subject_label']]}"
        entries=''.join(f'<li><a href="../{html.escape(c["single_path"])}">{c["cell"]:02} · {html.escape(c["name_ru"])}</a><br><small>{html.escape(c["id"])}</small></li>' for c in p['cells'])
        cards.append(f'<article><h2>{html.escape(label)}</h2><a href="{name}"><img loading="lazy" src="{name}" alt="{html.escape(label)}"></a><details><summary>Одиночники и ID ({len(p["cells"])})</summary><ol>{entries}</ol></details></article>')
    page='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SODABABYX · Тематические коллажи</title><style>body{font:16px Segoe UI,Arial;background:#111719;color:#e9ede7;margin:24px}a{color:#c8dfaa}header{max-width:1000px;margin-bottom:24px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:20px}article{background:#20292b;padding:14px;border-radius:12px}h2{font-size:20px}img{width:100%;height:420px;object-fit:contain;background:#e6e9e3}li{margin:12px 0;overflow-wrap:anywhere}summary{cursor:pointer;margin-top:12px}</style><header><h1>Тематические коллажи SODABABYX</h1><p>273 одиночных референса. Тема → пол → номер ячейки → одиночник. Коллажи предназначены только для поиска, не для вложения в генератор.</p><a href="../singles.html">Одиночные карточки</a> · <a href="INDEX.json">Индекс для агента</a> · <a href="AGENT_GUIDE.md">Порядок поиска</a></header><main class="grid">'''+''.join(cards)+'</main></html>'
    (OUT/'index.html').write_text(page,encoding='utf-8')
    # Existing local album server serves this directory; ordinary copies work
    # without junction privileges and keep the static album usable offline.
    served=ROOT/'work/sodababyx-20261113/album/collages'
    served.mkdir(parents=True,exist_ok=True)
    for artifact in OUT.iterdir():
        if artifact.is_file():
            shutil.copy2(artifact,served/artifact.name)
    print(json.dumps({'collages':len(index['records']),'topics':len({r['topic'] for r in index['records']}),'singles':len(ids)},ensure_ascii=False))

def find(args):
    index=json.loads((OUT/'INDEX.json').read_text('utf-8'))
    if index['manifest_sha256']!=sha(MANIFEST):
        raise ValueError('Collage index is stale; rebuild before use')
    pages=[p for p in index['records'] if p['subject_label']==args.subject and
           (args.topic.casefold() in p['topic'].casefold() or args.topic.casefold() in p['topic_ru'].casefold())]
    result=[]
    for p in pages:
        assert sha(BASE/p['path'])==p['sha256'],p['path']
        cells=[c for c in p['cells'] if args.cell is None or c['cell']==args.cell]
        if not cells: continue
        result.append({'topic':p['topic'],'subject':p['subject_label'],'collage':str(BASE/p['path']),
            'purpose':'DISCOVERY_ONLY','cells':cells if args.cell is not None else len(cells)})
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('build');f=sub.add_parser('find');f.add_argument('--topic',required=True)
    f.add_argument('--subject',choices=tuple(SEX),required=True);f.add_argument('--cell',type=int)
    args=parser.parse_args()
    build() if args.command=='build' else find(args)
