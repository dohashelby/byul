"""Preserve fixed IDs and unique F evidence while changing current guidance."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
DATA=json.loads((ROOT/'data/checklist.json').read_text())
ROWS={x['id']:x for k,v in DATA.items() if k.endswith('_assessment_draft') and isinstance(v,list) for x in v}
ORIGINAL=json.loads((Path(__file__).parent/'fixtures/checklist_phase1_preservation.json').read_text())
spec=importlib.util.spec_from_file_location('framework_verify',ROOT/'verify_framework.py')
verify=importlib.util.module_from_spec(spec);spec.loader.exec_module(verify)

class EditorialPreservationTests(unittest.TestCase):
    def test_ids_relations_and_f_evidence_preserved(self):
        self.assertEqual(list(ROWS),ORIGINAL['ids'])
        for cid,suffix in ORIGINAL['f_suffixes'].items():
            with self.subTest(check=cid):
                self.assertTrue(ROWS[cid]['base_method_and_evidence'].endswith(suffix))
                self.assertIn('**vLLM v0.31.0 구현 메모:**',ROWS[cid]['base_method_and_evidence'])
        self.assertEqual(sum(len(x['test_branch_conditions']) for x in ROWS.values()),112)

    def test_all_existing_display_text_uses_structured_source(self):
        for row in ROWS.values():
            self.assertTrue(verify.method_matches(row),row['id'])
        changed=copy.deepcopy(ROWS['CHK-LIF-012']);changed['normal_control']='different control'
        self.assertFalse(verify.method_matches(changed))

    def test_debug_selection_uses_activation_not_failure(self):
        condition=ROWS['CHK-LIF-004']['test_branch_conditions'][1]['apply_when']
        self.assertIn('실제 활성화',condition)
        self.assertNotIn('메모리 고갈이',condition)

    def test_manual_recall_is_not_automatic_retry_evidence(self):
        row=ROWS['CHK-LIF-012']
        self.assertIn('수동 재호출',row['challenge_steps'][0])
        self.assertIn('실제 자동 retry 주체',row['challenge_steps'][1])
        self.assertIn('미확인',row['unknown_criteria'])

if __name__=='__main__':unittest.main()
