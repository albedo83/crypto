"""Protect against stale factual IA context when core parameters change."""

from dataclasses import replace
import hashlib
import json
import unittest

from ai_doctrine import build_doctrine, DOCTRINE_DIGEST
from alfred.settings import DEFAULT_PARAMS
import ai_entry_arbiter
import ai_exit_arbiter


def factual(text):
    return json.loads(text.split("PARAMÈTRES FACTUELS DU NOYAU :\n", 1)[1])


class DoctrineTests(unittest.TestCase):
    def test_current_core_facts_and_disabled_exits(self):
        cfg = factual(DOCTRINE_DIGEST)
        self.assertEqual(cfg["adaptive_alpha_by_direction"]["S5_SHORT"], 0)
        self.assertEqual(cfg["runner_ext_strategies"], ["S9"])
        self.assertEqual(cfg["prop_trail_params"], {})
        self.assertLess(cfg["dead_timeout_mfe_cap_bps"], 0)
        self.assertTrue(cfg["traj_cut_long_only"])
        self.assertEqual(cfg["hold_hours"]["S1"], 72)

    def test_direction_override_and_reenabled_trail_change_reference(self):
        p = replace(DEFAULT_PARAMS, adaptive_alpha_dir={("S5", -1): -0.5},
                    prop_trail_params={"S5": {"bull": (200, .65)}},
                    hold_hours={"S1": 96})
        text = build_doctrine(p)
        cfg = factual(text)
        self.assertEqual(cfg["adaptive_alpha_by_direction"]["S5_SHORT"], -.5)
        self.assertEqual(cfg["adaptive_alpha_by_direction"]["S5_LONG"], 0)
        self.assertEqual(cfg["prop_trail_params"]["S5"]["bull"], [200, .65])
        self.assertEqual(cfg["hold_hours"]["S1"], 96)
        self.assertNotEqual(text, DOCTRINE_DIGEST)

    def test_both_prompt_hashes_cover_generated_doctrine(self):
        for module in (ai_entry_arbiter, ai_exit_arbiter):
            with self.subTest(module=module.__name__):
                expected = hashlib.sha256((module.SYSTEM_PROMPT + DOCTRINE_DIGEST).encode()).hexdigest()[:10]
                self.assertEqual(module.PROMPT_HASH, expected)
                changed = hashlib.sha256((module.SYSTEM_PROMPT + build_doctrine(
                    replace(DEFAULT_PARAMS, leverage=1))).encode()).hexdigest()[:10]
                self.assertNotEqual(module.PROMPT_HASH, changed)


if __name__ == "__main__":
    unittest.main()
