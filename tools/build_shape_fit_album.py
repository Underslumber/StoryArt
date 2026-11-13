"""Build the local visual album from current reviewed catalog records."""
from pathlib import Path
import base64
import hashlib
import json
import mimetypes
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.shape_fit_reference_pool import catalog_records


def build():
    root, records = catalog_records(ROOT, "SODABABYX")
    images, unconfirmed = [], 0
    for item in records:
        if item["record_binding_status"] != "CURRENT":
            raise ValueError(f"Stale catalog binding: {item['id']}")
        if item["raw"].get("subject_label") in {"UNKNOWN", "MIXED"}:
            unconfirmed += 1
        row = item["raw"]
        # Author explicitly requested all curated sheets for viewing (2026-11-13).
        # This album does not change selection or generation eligibility.
        file = (root / row["local_path"]).resolve()
        file.relative_to(root.resolve())
        content = file.read_bytes()
        if hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError(f"Stale source: {item['id']}")
        images.append({"id": item["id"], "title": item["title"], "group": row["priority"],
            "date": row["published_on"], "subject": row["subject_label"], "requested": bool(row.get("user_supplied_link")),
            "variants": [v["label"] for v in row["variants"]],
            "description": row["review"]["observations"], "limitations": row["review"]["limitations"],
            "post": row["post_url"], "width": row["width"], "height": row["height"],
            "image": "data:" + (mimetypes.guess_type(file.name)[0] or "image/jpeg") + ";base64," + base64.b64encode(content).decode()})
    template = r'''<!doctype html>
<html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SODABABY · Альбом референсов</title>
<style>
:root{color-scheme:dark;font-family:Inter,Segoe UI,Arial,sans-serif;background:#101214;color:#f2f0e9}*{box-sizing:border-box}body{margin:0}header{padding:32px 4vw 20px;border-bottom:1px solid #35383c;background:#171a1c}small,.muted{color:#a8aaa7}h1{font-size:clamp(26px,4vw,42px);margin:7px 0 12px;font-weight:600;letter-spacing:-1px}header p{margin:0;color:#b9bab5;line-height:1.6}.toolbar{position:sticky;top:0;z-index:2;background:#101214f5;padding:18px 4vw;display:flex;gap:8px;flex-wrap:wrap;align-items:center;backdrop-filter:blur(12px)}button,input,select{font:inherit;border:1px solid #424644;background:#202427;color:#edece5;border-radius:9px;padding:10px 14px}button{cursor:pointer}button:hover{border-color:#cfdbad}button[aria-pressed=true]{background:#d6e4b4;color:#172012;border-color:#d6e4b4}input{min-width:180px;flex:1}select{max-width:170px}main{padding:0 4vw 32px}#count{font-size:13px;color:#aaa;margin:0 0 16px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:22px}.card{border:1px solid #33383a;border-radius:13px;overflow:hidden;background:#1b1e20}.preview{display:block;width:100%;padding:0;border:0;border-radius:0;background:#e9e7e3;height:340px}.preview img{width:100%;height:100%;object-fit:contain;display:block}.info{padding:16px}.info h2{font-size:18px;margin:8px 0 10px}.info p{font-size:13px;line-height:1.55;color:#babdb8;margin:0}.tag{font-size:11px;color:#d6e4b4;text-transform:uppercase;letter-spacing:1px}.meta{display:flex;justify-content:space-between;font-size:12px;color:#949c99;margin-top:13px}a{color:#d6e4b4}footer{margin:24px 0;font-size:13px;color:#959c97;line-height:1.5}dialog{padding:0;border:1px solid #626963;border-radius:12px;background:#171a1b;color:#eee;width:96vw;max-width:1600px;height:94vh;max-height:94vh}dialog::backdrop{background:#000d}.viewer{height:100%;display:flex;flex-direction:column}.viewerbar{padding:12px 16px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;border-bottom:1px solid #414643}.viewerbar strong{flex:1}.canvas{flex:1;overflow:auto;text-align:center;min-height:0;background:#292c2c}.canvas img{display:block;max-width:100%;max-height:100%;width:auto;height:auto;object-fit:contain;margin:auto}.canvas.zoom img{max-width:none;max-height:none}.caption{padding:12px 20px;line-height:1.5;font-size:13px;color:#b6bdb7;max-height:150px;overflow:auto}.caption b{color:#e8eae5}.empty{padding:60px;text-align:center}@media(max-width:600px){header{padding-top:20px}.grid{grid-template-columns:1fr}.preview{height:380px}.toolbar button{font-size:13px;padding:9px}dialog{width:100vw;height:100dvh;max-height:100dvh;border-radius:0}.viewerbar strong{width:100%;flex:auto}}
</style>
<header><small>STORYART / КОЛЛЕКЦИЯ РЕФЕРЕНСОВ</small><h1>SODABABY</h1><p>Формы тела и частей тела · Посадка одежды · Позы<br>Нажмите на изображение, чтобы рассмотреть исходный лист.</p><p><a href="singles.html">Одиночные референсы с тегами</a> · <a href="collages/index.html">Тематические коллажи</a></p></header>
<div class="toolbar"><button data-group="0" aria-pressed="true">Все</button><button data-group="1" aria-pressed="false">Формы тела</button><button data-group="2" aria-pressed="false">Посадка одежды</button><button data-group="3" aria-pressed="false">Позы</button><button data-group="4" aria-pressed="false">По вашим ссылкам</button><input id="search" aria-label="Поиск по альбому" placeholder="Торс, купальник, ноги…"><select id="subject" aria-label="Пол"><option value="">Все</option><option value="FEMALE">Женские</option><option value="MALE">Мужские</option><option value="NEUTRAL">Нейтральные</option><option value="MIXED">Смешанный лист</option></select></div>
<main><div id="count"></div><div class="grid" id="grid"></div><footer>__WITHHELD__ листов пока не показаны: маркировка пола источника не подтверждена. Устаревшие и исключённые версии в альбом не включены. Исходники сохранены без изменений.</footer></main>
<dialog id="viewer" aria-label="Просмотр исходного листа"><div class="viewer"><div class="viewerbar"><button id="prev" aria-label="Предыдущий лист">←</button><button id="next" aria-label="Следующий лист">→</button><strong id="title"></strong><button id="zoom">Масштаб 100%</button><a id="original" target="_blank">Оригинал</a><button id="close" aria-label="Закрыть просмотр">✕</button></div><div class="canvas" id="canvas"><img id="full" alt=""></div><div class="caption" id="caption"></div></div></dialog>
<script>
const data=__DATA__,labels={1:'Формы тела',2:'Посадка одежды',3:'Позы'},sexLabels={FEMALE:'Женский',MALE:'Мужской',NEUTRAL:'Нейтральный — пол не задан',MIXED:'Смешанный — см. одиночные карточки',UNKNOWN:'Не подтверждён'};
const grid=document.querySelector('#grid'),search=document.querySelector('#search'),subject=document.querySelector('#subject'),viewer=document.querySelector('#viewer');let group=0,filtered=[],current=0;
function esc(x){return String(x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function render(){const q=search.value.toLocaleLowerCase();filtered=data.filter(x=>(!group||(group===4?x.requested:x.group===group))&&(!subject.value||x.subject===subject.value)&&(!q||[x.title,x.description,...x.variants].join(' ').toLocaleLowerCase().includes(q)));document.querySelector('#count').textContent=`Листов: ${filtered.length} · Вариантов: ${filtered.reduce((n,x)=>n+x.variants.length,0)}`;grid.innerHTML=filtered.map((x,i)=>`<article class="card"><button class="preview" data-index="${i}" aria-label="Открыть: ${esc(x.title)}"><img src="${x.image}" alt="${esc(x.title)}"></button><div class="info"><span class="tag">${labels[x.group]}</span><p>${sexLabels[x.subject]}</p><h2>${esc(x.title)}</h2><p>${esc(x.variants.join(' · '))}</p><div class="meta"><span>${x.width} × ${x.height}</span><span>${x.date}</span></div></div></article>`).join('')||'<div class="empty">По этому запросу листов нет.</div>';grid.querySelectorAll('button').forEach(b=>b.onclick=()=>openImage(Number(b.dataset.index)))}
function openImage(i){current=(i+filtered.length)%filtered.length;const x=filtered[current];document.querySelector('#title').textContent=x.title;const img=document.querySelector('#full');img.src=x.image;img.alt=x.title;document.querySelector('#original').href=x.image;document.querySelector('#original').download=x.id+'.jpg';document.querySelector('#canvas').classList.remove('zoom');document.querySelector('#zoom').textContent='Масштаб 100%';document.querySelector('#caption').innerHTML=`<b>${esc(x.variants.join(' · '))}</b><br>${esc(x.description)}<br>${esc(x.limitations)} · <a href="${esc(x.post)}" target="_blank" rel="noreferrer">Публикация автора</a>`;if(!viewer.open)viewer.showModal()}
document.querySelectorAll('[data-group]').forEach(b=>b.onclick=()=>{group=Number(b.dataset.group);document.querySelectorAll('[data-group]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));render()});search.oninput=render;subject.onchange=render;document.querySelector('#close').onclick=()=>viewer.close();document.querySelector('#prev').onclick=()=>openImage(current-1);document.querySelector('#next').onclick=()=>openImage(current+1);document.querySelector('#zoom').onclick=()=>{let on=document.querySelector('#canvas').classList.toggle('zoom');document.querySelector('#zoom').textContent=on?'Вписать в окно':'Масштаб 100%'};document.addEventListener('keydown',e=>{if(viewer.open&&e.key==='ArrowRight')openImage(current+1);if(viewer.open&&e.key==='ArrowLeft')openImage(current-1)});render();
</script></html>'''
    output = root / "index.html"
    template = template.replace("__WITHHELD__ листов пока не показаны: маркировка пола источника не подтверждена.", "Все __TOTAL__ листов доступны для просмотра. Пол и нейтральность указаны на карточках. Смешанные листы размечены по одиночным рисункам. Просмотр не меняет допуск к генерации.")
    output.write_text(template.replace("__DATA__", json.dumps(images, ensure_ascii=False).replace("<", "\\u003c")).replace("__WITHHELD__", str(unconfirmed)).replace("__TOTAL__", str(len(images))), encoding="utf-8")
    print(json.dumps({"path": str(output), "images": len(images), "unconfirmed": unconfirmed}, ensure_ascii=False))


if __name__ == "__main__":
    build()
