'''The requirements catalog (remote_control/requirements.py) from the real registries of the simulated car: every
spoken phrase, dashboard switch, setting and web command has a requirement, ids are unique. With PICAR_REQUIREMENTS_CATALOG
set (tools/run_tests.py), the catalog is written there for tools/requirements_report.py.'''
import os
import sys
import tempfile
import unittest

os.environ.update(PICAR_SIM='1', PICAR_DATA=tempfile.mkdtemp(prefix='picar_test_'), DJANGO_SETTINGS_MODULE='remote_control.settings')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'remote_control'))
import django  # noqa: E402
django.setup()

from remote_control import commands, events, features, requirements, vocabulary  # noqa: E402

CATALOG_ENV = 'PICAR_REQUIREMENTS_CATALOG'


def car_catalog():
    return requirements.catalog(list(features.FEATURES), list(events.EDITABLE_SETTINGS), commands.api_names())


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.by_id = {requirement.id: requirement for requirement in car_catalog()}

    def test_every_phrase_is_a_variant(self):
        for language in vocabulary.LANGUAGES:
            for phrase in vocabulary.phrases(language):
                requirement_id, variant = requirements.voice_requirement(language, phrase)
                self.assertIn(variant, self.by_id[requirement_id].variants)

    def test_every_switch_setting_and_web_command(self):
        expected = (['K.switch.' + name for name in features.FEATURES] + ['K.setting.' + name for name in events.EDITABLE_SETTINGS]
                    + ['K.command.' + name for name in commands.api_names()])
        self.assertEqual([name for name in expected if name not in self.by_id], [])

    def test_hand_written_ones_are_well_formed(self):
        for requirement in requirements.SYSTEM + requirements.CLAUDE:
            self.assertIn(requirement.checked_by, requirements.EVIDENCE, requirement.id)
            self.assertTrue(requirement.variants, requirement.id)
            self.assertRegex(requirement.id, r'^[SC]\.[a-z_]+\.[a-z_]+$')

    def test_catalog_written_for_the_report(self):
        path = os.environ.get(CATALOG_ENV)
        if not path:
            self.skipTest('no %s: not a tools/run_tests.py run' % CATALOG_ENV)
        requirements.write_catalog(path, car_catalog())
        self.assertTrue(os.path.getsize(path) > 0)


if __name__ == '__main__':
    unittest.main()
