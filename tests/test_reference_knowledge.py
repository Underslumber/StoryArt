"""Discovery must never silently promote a source or discard its restrictions."""
import unittest
from pathlib import Path
from tools.reference_knowledge import normalize, candidate_set, search


class ReferenceKnowledgeTests(unittest.TestCase):
    def record(self):
        return {'id':'example','title':'Кулак','tags':['HAND:FIST'],
                'categories':['HANDS_FINGERS'],'subject_label':'UNKNOWN',
                'subject_label_status':'UNKNOWN','relative_path':'must-not-be-opened.png',
                'sha256':'a'*64,'role':'DRAWING_TECHNIQUE','raw':{
                    'summary_ru':'Поворот кисти и форма кулака.',
                    'allowed_uses':['RESEARCH_ONLY'], 'forbidden_uses':['BODY_TRANSFER']}}

    def test_unknown_has_description_but_no_image_route(self):
        r=normalize('DRAWING_GUIDES',Path('.'),self.record())
        self.assertNotIn('path',r)
        self.assertFalse(r['preview_allowed'])
        self.assertFalse(r['generation_eligible'])
        self.assertIn('Поворот кисти',r['description'])
        self.assertTrue(any('RESEARCH_ONLY' in x for x in r['limitations']))
        self.assertTrue(any('BODY_TRANSFER' in x for x in r['limitations']))

    def test_selection_never_claims_visual_approval(self):
        r=normalize('DRAWING_GUIDES',Path('.'),self.record())
        data={'records':[r],'inputs':{}}
        result=candidate_set(data,[r['key'],r['key']],'Нужен кулак')
        self.assertEqual(1,len(result['items']))
        self.assertFalse(result['items'][0]['attachment_allowed'])
        self.assertEqual('NOT_CHECKED',result['items'][0]['visual_review'])
        self.assertEqual(r['limitations'],result['items'][0]['limitations'])
        with self.assertRaises(ValueError): candidate_set(data,['missing'],'brief')

    def test_search_respects_subject_and_all_words(self):
        data={'records':[{'pool':'A','subject':'MALE','search_text':'кулак рука'},
                         {'pool':'A','subject':'FEMALE','search_text':'кулак рука'},
                         {'pool':'A','subject':'MALE','search_text':'ладонь'}]}
        self.assertEqual(1,len(search(data,'кулак рука',subject='MALE')))
        self.assertEqual([],search(data,'кулак нога'))

    def test_search_ranks_relevant_title_and_covers_pools(self):
        rows=[{'key':'A:weak','pool':'A','subject':'MALE','title':'Поза','group_id':'1','search_text':'сидя лежа'},
              {'key':'A:strong','pool':'A','subject':'MALE','title':'Сидя на стуле','group_id':'2','search_text':'сидя на стуле'},
              {'key':'B:strong','pool':'B','subject':'MALE','title':'Сидя на полу','group_id':'3','search_text':'сидя на полу'}]
        got=search({'records':rows},'сидя',limit=2)
        self.assertEqual(['A:strong','B:strong'],[r['key'] for r in got])

    def test_search_keeps_exact_pool_filter(self):
        rows=[{'key':'A:1','pool':'A','subject':'MALE','title':'Кулак','search_text':'кулак'},
              {'key':'B:1','pool':'B','subject':'MALE','title':'Кулак','search_text':'кулак'}]
        self.assertEqual(['A:1'],[r['key'] for r in search({'records':rows},'кулак',pool='A')])

    def test_pose_queries_use_reviewed_body_facing_independently_of_camera(self):
        rows = [
            {'key': 'rear', 'angle': 'front', 'body_facing': 'BACK'},
            {'key': 'profile', 'angle': 'back', 'body_facing': 'SIDE'},
            {'key': 'oblique', 'angle': 'side', 'body_facing': 'THREE_QUARTER'},
            {'key': 'unreviewed', 'angle': 'back'},
            {'key': 'excluded', 'angle': 'back', 'body_facing': 'BACK', 'status': 'POSE_REVIEW_EXCLUDED'},
        ]
        data = {'records': [dict(r, pool='POSE_ATLAS', subject='FEMALE', search_text='стойка') for r in rows]}
        for phrase, key in [('со спины', 'rear'), ('сбоку', 'profile'), ('в ¾', 'oblique')]:
            self.assertEqual([key], [r['key'] for r in search(data, phrase)])
        self.assertEqual(['rear'], [r['key'] for r in search(data, 'стойка', body_facing='BACK')])
        with self.assertRaises(ValueError):
            search(data, 'сбоку', body_facing='BACK')

    def test_search_normalizes_common_russian_query_phrases_only(self):
        rows=[{'key':'WS_0389_3d_front','pool':'WAVESPEED_POSES','subject':'NEUTRAL','title':'High five','group_id':'WS_0389','search_text':'high five'},
              {'key':'CHAIR_1','pool':'A','subject':'MALE','title':'Сидя на стуле','search_text':'сидя на стуле'}]
        original=[r['search_text'] for r in rows]
        self.assertEqual(['WS_0389_3d_front'],[r['key'] for r in search({'records':rows},'дай пять')])
        self.assertEqual(['WS_0389_3d_front'],[r['key'] for r in search({'records':rows},'дай 5')])
        self.assertEqual(['CHAIR_1'],[r['key'] for r in search({'records':rows},'сидит на стуле')])
        self.assertEqual([],search({'records':rows},'zzq-no-match-xyz'))
        self.assertEqual(original,[r['search_text'] for r in rows])

    def test_wavespeed_exposes_missing_anatomy_review_and_its_actual_evidence(self):
        source=self.record()
        source.update(title='Sitting on heels',tags=['Sitting on the floor'],angle='front')
        source['raw']={'visual_qa_status':'NOT_CHECKED','anatomy_qa_status':'NOT_CHECKED'}
        pending=normalize('WAVESPEED_POSES',Path('.'),source)
        self.assertTrue(any('анатомии' in x for x in pending['missing_checks']))
        self.assertNotIn('обувь',pending['aliases_ru'])
        self.assertIn('не подтверждение',pending['description'])
        source['raw'].update(visual_qa_status='PASS',anatomy_qa_status='PASS',
                             visual_review_evidence='Connected body observed.',review_limitations='Far hand occluded.')
        reviewed=normalize('WAVESPEED_POSES',Path('.'),source)
        self.assertIn('Connected body observed.',reviewed['description'])
        self.assertIn('Far hand occluded.',reviewed['limitations'])
        self.assertFalse(any('анатомии' in x for x in reviewed['missing_checks']))


if __name__=='__main__': unittest.main()
