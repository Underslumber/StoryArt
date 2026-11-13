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


if __name__=='__main__': unittest.main()
