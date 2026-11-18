"""Explicitly authorized, request-local sheets of reviewed original references."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def pack_for_slot(plan, slot, *, require_review=True):
    binding = next((p for p in plan.get('source_reference_packing', {}).get('packs', [])
                    if Path(p['path']).resolve() == Path(slot['path']).resolve()), None)
    if binding is None:
        return None
    manifest_path = Path(binding['manifest_path']).resolve()
    expected_root = Path(plan['pack_path']).parent / (plan['style_name']+'_GENERATIONS') / '00_PENDING' / plan['request_id'] / 'TECHNICAL_REFERENCES'
    if manifest_path.parent != expected_root.resolve() or Path(slot['path']).resolve().parent != expected_root.resolve():
        raise ValueError('Reference sheets must remain in this active request technical folder.')
    if (str(manifest_path) != str(Path(slot.get('manifest_path', '')).resolve())
            or digest(manifest_path) != binding['manifest_sha256']):
        raise ValueError('Reference sheet manifest differs from the prepared binding.')
    manifest = read(manifest_path)
    original = plan['source_reference_packing']['original_slots']
    expected = {(str(Path(s['path']).resolve()), s['sha256'], tuple(s['active_roles'])) for s in original}
    roles = set()
    for source in manifest.get('sources', []):
        key = (str(Path(source['path']).resolve()), source['sha256'], tuple(source['active_roles']))
        if key not in expected or digest(source['path']) != source['sha256']:
            raise ValueError('Reference sheet contains an unselected or changed original.')
        roles.update(source['active_roles'])
    if (manifest.get('request_id') != plan.get('request_id') or not manifest.get('sources')
            or Path(manifest['output']['path']).resolve() != Path(slot['path']).resolve()
            or digest(slot['path']) != slot['sha256'] or slot['sha256'] != manifest['output']['sha256']
            or set(slot['active_roles']) != roles or roles.intersection({'STYLE', 'CLOTHES'})):
        raise ValueError('Reference sheet request, bytes or exact role union is invalid.')
    authority = plan['source_reference_packing']['authorization']
    if manifest.get('authorization') != authority or not authority.get('user_quote'):
        raise ValueError('Reference sheet authorization is missing or changed.')
    if require_review and (manifest.get('generator_safe') is not True
                           or manifest.get('layout_review', {}).get('status') != 'PASS'):
        raise ValueError('Reference sheet needs its actual layout review before execution.')
    return manifest


def expand_slots(plan, slots):
    expanded = []
    for slot in slots:
        manifest = pack_for_slot(plan, slot)
        expanded.extend(manifest['sources'] if manifest else [slot])
    packing = plan.get('source_reference_packing')
    if packing:
        def key(s): return (str(Path(s['path']).resolve()), s['sha256'], tuple(s['active_roles']))
        if sorted(map(key, expanded)) != sorted(map(key, packing['original_slots'])):
            raise ValueError('Reference sheets must carry every selected original exactly once with unchanged roles.')
    return expanded


def validate_panel_risk(plan, prompt, slots):
    """Keep the exact original-panel risk contract alongside physical sheet risk."""
    packing = plan.get('source_reference_packing')
    if not packing:
        return
    from tools.generation_risk_assessor import validate_assessment
    binding = packing.get('panel_risk_assessment', {})
    path = Path(binding.get('path', ''))
    if not path.is_file() or digest(path) != binding.get('sha256'):
        raise ValueError('Exact original-panel risk assessment is missing or changed.')
    validate_assessment(read(path), prompt, expand_slots(plan, slots))


def prepare(folder: Path, specification: Path):
    from tools import style_pack_manager as manager
    folder = folder.resolve()
    manager.assert_active_request_path(folder/'EXECUTION_GUARD.json')
    guard = read(folder/'EXECUTION_GUARD.json')
    spec = read(specification)
    if guard.get('active_attempt') or guard.get('task_revision', 0) < 1:
        raise ValueError('Reference packing requires a settled attempt and an explicit current correction.')
    plan_path = folder/'REFERENCE_PLAN.json'
    plan = read(plan_path)
    if plan.get('source_reference_packing'):
        raise ValueError('Reference sheets already prepared; review the existing sheets.')
    if (spec.get('request_id') != plan['request_id'] or spec.get('chat_id') != guard['owner_chat_id']
            or spec.get('user_quote') != 'Разрешить листы и продолжить генерацию'):
        raise ValueError('Packing must match this explicitly authorized request and current chat.')
    original = plan['generation_workflow']['slots']
    paths = manager.make_paths(folder.parents[2], plan['style_name'])
    for source in original:
        manager.validate_resolved_slot_compatibility(paths, plan, source, Path(source['path']).resolve())
        if digest(source['path']) != source['sha256']:
            raise ValueError('A selected original changed before sheet preparation.')
    archive = folder/f'REFERENCE_PLAN_BEFORE_SOURCE_PACKS_REV{guard["task_revision"]}.json'
    output = folder/'TECHNICAL_REFERENCES'
    if output.resolve() != output:
        raise ValueError('Reference sheets must remain in this active request technical folder.')
    destinations = []
    names = set()
    for group in spec['groups']:
        name = group['name']
        if (not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', name)
                or name.lower() in names or (output/f'{name}.png').is_reserved()):
            raise ValueError('Reference sheets require unique, safe group names.')
        names.add(name.lower())
        image_path, manifest_path = output/f'{name}.png', output/f'{name}.json'
        if any(path.resolve().parent != output or path.exists() or path.is_symlink()
               for path in (image_path, manifest_path)):
            raise ValueError('Refusing to overwrite or escape the request reference sheet folder.')
        destinations.append((image_path, manifest_path))
    with archive.open('xb') as stream:
        stream.write(plan_path.read_bytes())
    output.mkdir(exist_ok=True)
    authority = {k: spec[k] for k in ('request_id', 'chat_id', 'user_quote')}
    packs, consumed, new_slots = [], set(), []
    font_path = Path('C:/Windows/Fonts/arial.ttf')
    font = ImageFont.truetype(str(font_path), 24) if font_path.is_file() else ImageFont.load_default()
    for group, (image_path, manifest_path) in zip(spec['groups'], destinations):
        sources, panels = [], []
        for item in group['panels']:
            number = item['source_slot']
            if number in consumed or not 1 <= number <= len(original):
                raise ValueError('Reference sheet contains a duplicate or unknown source slot.')
            source = original[number-1]
            if 'STYLE' in source['active_roles'] or 'CLOTHES' in source['active_roles']:
                raise ValueError('STYLE and dress originals must remain separate physical files.')
            consumed.add(number)
            with Image.open(source['path']) as opened:
                panel = opened.convert('RGB')
                panel.thumbnail((720, 1200), Image.Resampling.LANCZOS)
                panels.append(panel.copy())
            sources.append({**source, 'participant': item['participant'], 'label': item['label']})
        canvas = Image.new('RGB', (len(panels)*752+32, max(p.height for p in panels)+104), '#f2f2f2')
        draw = ImageDraw.Draw(canvas)
        for i, (panel, source) in enumerate(zip(panels, sources)):
            x = 32+i*752+(720-panel.width)//2
            y = 80+(canvas.height-104-panel.height)//2
            canvas.paste(panel, (x, y))
            draw.text((32+i*752, 20), source['label'], fill='#111111', font=font)
            source['panel_box'] = [x, y, x+panel.width, y+panel.height]
        with image_path.open('xb') as stream:
            canvas.save(stream, format='PNG')
        manifest = dict(schema_version=1, kind='REVIEWED_ORIGINAL_REFERENCE_SHEET',
                        request_id=plan['request_id'], authorization=authority, sources=sources,
                        output=dict(path=str(image_path), sha256=digest(image_path), dimensions=list(canvas.size)),
                        generator_safe=False, layout_review=dict(status='NOT_CHECKED'),
                        construction='Aspect-preserving resize and placement only; no crop, retouch, source overwrite or generated helper image.')
        with manifest_path.open('x', encoding='utf-8') as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        slot = dict(path=str(image_path), sha256=digest(image_path),
                    active_roles=sorted({r for s in sources for r in s['active_roles']}),
                    manifest_path=str(manifest_path), physically_attach=True)
        packs.append({**slot, 'manifest_sha256': digest(manifest_path)})
        new_slots.append(slot)
    remaining = [s for i,s in enumerate(original, 1) if i not in consumed]
    if len(new_slots)+len(remaining) > 5:
        raise ValueError('Authorized sheets still exceed the backend five-reference limit.')
    # STYLE and the exact two dress originals stay first, followed by the two sheets.
    slots = remaining+new_slots
    for i, slot in enumerate(slots, 1): slot['slot'] = i
    plan['source_reference_packing'] = dict(authorization=authority, original_slots=original, packs=packs,
                                           separate_original_roles=['STYLE','CLOTHES'])
    plan['generation_workflow'] = dict(mode='SINGLE_PASS', attachment_limit=5, attachments_used=len(slots),
                                       slots=slots, all_selected_references_physically_attached=True,
                                       authorized_source_sheet_transport=True)
    plan['execution_call'] = None
    plan.pop('user_selections', None)
    plan['gate_status'] = 'PREPARED_AWAITING_EXECUTABLE_CALL'
    write(plan_path, plan)
    print(json.dumps({'sheets':[p['path'] for p in packs], 'physical_slots':len(slots)}, ensure_ascii=False))


def review(folder, manifest_path, reviewer, findings):
    from tools import style_pack_manager as manager
    folder = folder.resolve()
    manager.assert_active_request_path(folder/'EXECUTION_GUARD.json')
    plan_path = folder/'REFERENCE_PLAN.json'
    plan = read(plan_path)
    binding = next(p for p in plan['source_reference_packing']['packs']
                   if Path(p['manifest_path']).resolve() == manifest_path.resolve())
    manifest = pack_for_slot(plan, binding, require_review=False)
    if not reviewer.strip() or len(findings.strip()) < 30:
        raise ValueError('Actual identified layout review and substantive findings required.')
    manifest['layout_review'] = dict(status='PASS', reviewer=reviewer, findings=findings,
                                     output_sha256=manifest['output']['sha256'])
    manifest['generator_safe'] = True
    write(manifest_path, manifest)
    binding['manifest_sha256'] = digest(manifest_path)
    write(plan_path, plan)
    print('REFERENCE_SHEET_LAYOUT_REVIEW=PASS')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command', choices=['prepare','review'])
    parser.add_argument('--folder',type=Path,required=True)
    parser.add_argument('--specification',type=Path)
    parser.add_argument('--manifest',type=Path)
    parser.add_argument('--reviewer',default='')
    parser.add_argument('--findings',default='')
    args=parser.parse_args()
    if args.command=='prepare': prepare(args.folder,args.specification)
    else: review(args.folder,args.manifest,args.reviewer,args.findings)


if __name__=='__main__': main()
