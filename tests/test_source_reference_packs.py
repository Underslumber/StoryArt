import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from tools.source_reference_packs import digest, pack_for_slot, expand_slots, prepare, write, validate_panel_risk


class OriginalSheetBindingTests(unittest.TestCase):
    def test_prepare_rejects_paths_outside_request_before_writing(self):
        with TemporaryDirectory() as temporary:
            folder = Path(temporary) / 'STYLE_GENERATIONS' / '00_PENDING' / 'req'
            folder.mkdir(parents=True)
            write(folder / 'EXECUTION_GUARD.json', {'owner_chat_id': 'chat', 'task_revision': 1})
            write(folder / 'REFERENCE_PLAN.json', {
                'request_id': 'req', 'style_name': 'STYLE', 'generation_workflow': {'slots': []},
            })
            spec = folder / 'spec.json'
            destination = folder.parent / 'outside'
            authorization = {
                'request_id': 'req', 'chat_id': 'chat',
                'user_quote': 'Разрешить листы и продолжить генерацию',
            }
            for name in ('../../outside', str(destination), 'nested\\outside'):
                with self.subTest(name=name):
                    write(spec, {**authorization, 'groups': [{'name': name}]})
                    with patch('tools.style_pack_manager.assert_active_request_path'):
                        with self.assertRaisesRegex(ValueError, 'safe group names'):
                            prepare(folder, spec)
                    self.assertFalse((folder / 'TECHNICAL_REFERENCES').exists())
                    self.assertFalse((folder / 'REFERENCE_PLAN_BEFORE_SOURCE_PACKS_REV1.json').exists())
                    self.assertFalse(destination.with_suffix('.png').exists())

    def test_sheet_keeps_originals_roles_scope_and_review(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary)
            folder=root/'STYLE_GENERATIONS/00_PENDING/req/TECHNICAL_REFERENCES'
            folder.mkdir(parents=True)
            original=root/'original.png'; original.write_bytes(b'unchanged original')
            image=folder/'sheet.png'; image.write_bytes(b'packed image')
            source=dict(path=str(original),sha256=digest(original),active_roles=['BODY'])
            slot=dict(path=str(image),sha256=digest(image),active_roles=['BODY'],manifest_path=str(folder/'sheet.json'))
            authority=dict(user_quote='Explicit sheet approval')
            manifest=dict(request_id='req',sources=[source],authorization=authority,
                          output=dict(path=str(image),sha256=digest(image)),generator_safe=True,
                          layout_review=dict(status='PASS'))
            write(folder/'sheet.json',manifest)
            binding={**slot,'manifest_sha256':digest(folder/'sheet.json')}
            plan=dict(request_id='req',style_name='STYLE',pack_path=str(root/'STYLE_PROJECT_PACK'),
                      source_reference_packing=dict(authorization=authority,original_slots=[source],packs=[binding]))
            self.assertEqual(expand_slots(plan,[slot]),[source])
            from tools import generation_risk_assessor as risk
            with self.assertRaisesRegex(ValueError, 'risk assessment is missing'):
                validate_panel_risk(plan,'A plain adult figure.',[slot])
            risk_path=folder/'panels-risk.json'
            args=risk.build_parser().parse_args(['--text','A plain adult figure.','--reference',
                f'{original}::D2::0D::Reviewed original panel::BODY','--output',str(risk_path)])
            args.handler(args)
            plan['source_reference_packing']['panel_risk_assessment']=dict(path=str(risk_path),sha256=digest(risk_path))
            validate_panel_risk(plan,'A plain adult figure.',[slot])
            from tools.task_execution_guard import identity_anchor_sources, GuardError
            self.assertEqual(identity_anchor_sources(plan,[slot],'A plain adult figure.'),[source])
            report=json.loads(risk_path.read_text(encoding='utf-8'))
            report['references']=[]
            write(risk_path,report)
            plan['source_reference_packing']['panel_risk_assessment']['sha256']=digest(risk_path)
            with self.assertRaisesRegex(risk.RiskAssessmentError,'each unique physical reference'):
                validate_panel_risk(plan,'A plain adult figure.',[slot])
            with self.assertRaisesRegex(GuardError,'source-sheet identity lineage'):
                identity_anchor_sources(plan,[slot],'A plain adult figure.')
            with self.assertRaisesRegex(ValueError,'every selected original'):
                expand_slots(plan,[])
            with self.assertRaisesRegex(ValueError,'role union'):
                pack_for_slot(plan,{**slot,'active_roles':['CLOTHES']})
            clothes = {**source, 'active_roles': ['CLOTHES']}
            manifest['sources'] = [clothes]
            plan['source_reference_packing']['original_slots'] = [clothes]
            write(folder/'sheet.json', manifest)
            binding['manifest_sha256'] = digest(folder/'sheet.json')
            with self.assertRaisesRegex(ValueError, 'role union'):
                pack_for_slot(plan, {**slot, 'active_roles': ['CLOTHES']})
            manifest['sources'] = [source]
            plan['source_reference_packing']['original_slots'] = [source]
            manifest['generator_safe']=False; write(folder/'sheet.json',manifest)
            binding['manifest_sha256']=digest(folder/'sheet.json')
            with self.assertRaisesRegex(ValueError,'layout review'):
                pack_for_slot(plan,slot)
            manifest['generator_safe']=True; manifest['request_id']='other'; write(folder/'sheet.json',manifest)
            binding['manifest_sha256']=digest(folder/'sheet.json')
            with self.assertRaisesRegex(ValueError,'request, bytes'):
                pack_for_slot(plan,slot)
            manifest['request_id']='req'; write(folder/'sheet.json',manifest)
            binding['manifest_sha256']=digest(folder/'sheet.json')
            original.write_bytes(b'changed original')
            with self.assertRaisesRegex(ValueError,'changed original'):
                pack_for_slot(plan,slot)
