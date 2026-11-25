import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _support import (
    HARNESSES, ROLES, ROOT, SYNC, copy_package, load_role, load_script_module,
    split_frontmatter,
)


class AgentFormsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.roles = {path.stem: load_role(path) for path in sorted(ROLES.glob('*.toml'))}
        cls.harnesses = json.loads((HARNESSES / 'mapping.json').read_text())
        cls.form_fields = load_script_module(SYNC, 'sync_agents_script').FORM_FIELDS

    def parse_form(self, path):
        frontmatter, body = split_frontmatter(path)
        return frontmatter, body.strip('\n')

    def forms(self, harness):
        return {path.stem: path for path in sorted((HARNESSES / harness).glob('*.md'))}

    def test_every_role_has_a_form_in_every_registry_backed_harness(self):
        for harness in self.form_fields:
            with self.subTest(harness=harness):
                self.assertEqual(set(self.forms(harness)), set(self.roles))

    def test_frontmatter_follows_the_harness_mapping(self):
        for harness, effort_field in (('claude', 'effort'), ('pi', 'thinking')):
            config = self.harnesses[harness]
            forms = self.forms(harness)
            for name, role in self.roles.items():
                with self.subTest(harness=harness, role=name):
                    frontmatter, _ = self.parse_form(forms[name])
                    self.assertEqual(frontmatter['name'], f'"{name}"')
                    self.assertEqual(json.loads(frontmatter['description']), role['description'])
                    self.assertEqual(frontmatter['model'], f'"{config["roles"][name]["model"]}"')
                    expected_tools = ', '.join(config['tool_names'][tool] for tool in config['roles'][name]['tools'])
                    self.assertEqual(frontmatter['tools'], expected_tools)
                    self.assertEqual(json.loads(frontmatter[effort_field]), config['roles'][name]['effort'])
                    for key, value in config.get('frontmatter_defaults', {}).items():
                        self.assertEqual(frontmatter[key], 'true' if value else 'false')

    def test_committed_forms_match_regeneration(self):
        result = subprocess.run([sys.executable, str(SYNC), '--check'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def resolve_profile(self, harness, role, package=ROOT):
        return subprocess.run(
            [sys.executable, str(package / 'scripts/role-profile.py'), '--harness', harness, '--role', role],
            capture_output=True, text=True,
            env=dict(os.environ, AGENTS_METRICS='off'),
        )

    def copy_package(self, directory):
        return copy_package(directory, 'package with spaces')

    def assert_rejection_preserves_forms(self, package, harness, role, error=None, error_predicate=None):
        forms = package / 'harnesses'
        (forms / 'claude/worker.md').write_text('existing form\n')
        before = {path.relative_to(forms): path.read_bytes() for path in forms.rglob('*.md')}
        result = self.resolve_profile(harness, role, package)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stderr, '')
        self.assert_error_payload(json.loads(result.stdout), error, error_predicate)
        generated = subprocess.run([sys.executable, str(package / 'tools/sync-agents.py')],
                                   capture_output=True, text=True)
        self.assertEqual(generated.returncode, 2, generated.stderr)
        self.assertEqual(generated.stdout, '')
        self.assert_error_payload(json.loads(generated.stderr), error, error_predicate)
        after = {path.relative_to(forms): path.read_bytes() for path in forms.rglob('*.md')}
        self.assertEqual(after, before)

    def assert_error_payload(self, payload, error, error_predicate):
        self.assertEqual(set(payload), {'error'})
        if error_predicate is None:
            self.assertEqual(payload['error'], error)
        else:
            self.assertTrue(error_predicate(payload['error']), payload['error'])

    def test_missing_or_blank_canonical_fields_fail_before_forms_are_written(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.copy_package(directory)
            for field in ('name', 'description', 'model', 'model_reasoning_effort', 'developer_instructions'):
                for label, replacement in (('missing', {}), ('blank', {field: ' \t '})):
                    with self.subTest(field=field, case=label):
                        role = self.roles['reviewer'].copy()
                        del role[field]
                        role.update(replacement)
                        (package / 'roles/reviewer.toml').write_text(
                            '\n'.join(f'{key} = {json.dumps(value)}' for key, value in role.items()) + '\n'
                        )
                        self.assert_rejection_preserves_forms(
                            package, 'codex', 'reviewer', f'reviewer.toml.{field}: expected a nonempty string',
                        )

    def test_invalid_canonical_effort_fails_before_forms_are_written(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.copy_package(directory)
            role = self.roles['reviewer'] | {'model_reasoning_effort': 'unknown'}
            (package / 'roles/reviewer.toml').write_text(
                '\n'.join(f'{key} = {json.dumps(value)}' for key, value in role.items()) + '\n'
            )
            self.assert_rejection_preserves_forms(
                package, 'codex', 'reviewer', 'reviewer.toml: invalid Codex effort',
            )

    def test_invalid_frontmatter_defaults_fail_before_forms_are_written(self):
        cases = (
            ('null', None),
            ('array', []),
            ('string', 'true'),
            ('unknown key', {'unknown': True}),
            ('integer value', {'inheritSkills': 1}),
            ('string value', {'inheritSkills': 'true'}),
            ('null value', {'inheritSkills': None}),
        )
        with tempfile.TemporaryDirectory() as directory:
            package = self.copy_package(directory)
            mapping_path = package / 'harnesses/mapping.json'
            original = mapping_path.read_text()
            for harness in ('claude', 'pi'):
                for label, defaults in cases:
                    with self.subTest(harness=harness, case=label):
                        mapping = json.loads(original)
                        mapping[harness]['frontmatter_defaults'] = defaults
                        mapping_path.write_text(json.dumps(mapping))
                        self.assert_rejection_preserves_forms(
                            package, harness, 'reviewer', f'{harness}: invalid frontmatter defaults',
                        )

    def test_codex_profiles_use_canonical_model_effort_and_instructions(self):
        for name, role in self.roles.items():
            with self.subTest(role=name):
                result = self.resolve_profile('codex', name)
                self.assertEqual(result.returncode, 0, result.stderr)
                profile = json.loads(result.stdout)
                self.assertEqual(profile['name'], name)
                self.assertEqual(profile['developer_instructions'], role['developer_instructions'])
                self.assertEqual(profile['effort_policy'], 'explicit')
                self.assertEqual(profile['model'], role['model'])
                self.assertEqual(profile['effort'], role['model_reasoning_effort'])
                self.assertNotIn('tools', profile)

    def test_agentic_profiles_leave_model_and_effort_to_the_runtime(self):
        for name, role in self.roles.items():
            with self.subTest(role=name):
                result = self.resolve_profile('agentic', name)
                self.assertEqual(result.returncode, 0, result.stderr)
                profile = json.loads(result.stdout)
                self.assertEqual(profile['name'], name)
                self.assertEqual(profile['developer_instructions'], role['developer_instructions'])
                self.assertEqual(profile['model'], 'runtime-default')
                self.assertIsNone(profile['effort'])
                self.assertEqual(profile['effort_policy'], 'unsupported')
                self.assertNotIn('tools', profile)

    def test_runtime_default_profiles_resolve_with_native_tools(self):
        self.assertEqual(set(self.harnesses), {'antigravity', 'claude', 'delta', 'pi'})
        for harness in ('antigravity', 'delta'):
            config = self.harnesses[harness]
            for name, role in self.roles.items():
                with self.subTest(harness=harness, role=name):
                    result = self.resolve_profile(harness, name)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    profile = json.loads(result.stdout)
                    self.assertEqual(profile['name'], name)
                    self.assertEqual(profile['developer_instructions'], role['developer_instructions'])
                    self.assertEqual(profile['model'], 'runtime-default')
                    self.assertIsNone(profile['effort'])
                    self.assertEqual(profile['effort_policy'], 'unsupported')
                    expected = list(dict.fromkeys(
                        config['tool_names'][tool] for tool in config['roles'][name]['tools']
                    ))
                    self.assertEqual(profile['tools'], expected)

    def test_all_harnesses_declare_identical_capability_sets(self):
        for name in self.roles:
            with self.subTest(role=name):
                tool_lists = {
                    harness: config['roles'][name]['tools']
                    for harness, config in self.harnesses.items()
                }
                canonical = tool_lists['claude']
                for harness, tools in tool_lists.items():
                    self.assertEqual(tools, canonical, f'{harness}.{name}.tools')

    def test_mapping_must_declare_every_bundled_harness(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.copy_package(directory)
            mapping_path = package / 'harnesses/mapping.json'
            mapping = json.loads(mapping_path.read_text())
            del mapping['delta']
            mapping_path.write_text(json.dumps(mapping))
            self.assert_rejection_preserves_forms(
                package, 'codex', 'reviewer',
                'harness mapping must declare antigravity, claude, delta, and pi',
            )

    def test_generator_writes_forms_only_for_registry_backed_harnesses(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.copy_package(directory)
            generated = subprocess.run([sys.executable, str(package / 'tools/sync-agents.py')],
                                       capture_output=True, text=True)
            self.assertEqual(generated.returncode, 0, generated.stderr)
            self.assertFalse((package / 'harnesses' / 'delta').exists())
            for harness in self.form_fields:
                with self.subTest(harness=harness):
                    forms = {path.stem for path in (package / 'harnesses' / harness).glob('*.md')}
                    self.assertEqual(forms, set(self.roles))

    def test_effective_profiles_match_native_forms_and_canonical_instructions(self):
        for harness, effort_field in (('claude', 'effort'), ('pi', 'thinking')):
            for name, role in self.roles.items():
                with self.subTest(harness=harness, role=name):
                    result = self.resolve_profile(harness, name)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    profile = json.loads(result.stdout)
                    self.assertEqual(profile['name'], name)
                    self.assertEqual(profile['developer_instructions'], role['developer_instructions'])
                    self.assertEqual(profile['effort_policy'], 'explicit')
                    frontmatter, body = self.parse_form(self.forms(harness)[name])
                    self.assertEqual(json.loads(frontmatter['model']), profile['model'])
                    self.assertEqual(json.loads(frontmatter[effort_field]), profile['effort'])
                    self.assertEqual(frontmatter['tools'], ', '.join(profile['tools']))
                    self.assertEqual(body, profile['developer_instructions'].strip('\n'))

    def test_profile_cli_rejects_unknown_harness_or_role_with_json_error(self):
        for harness, role in (('unknown', 'reviewer'), ('codex', 'unknown'), ('claude', '../reviewer')):
            with self.subTest(harness=harness, role=role):
                result = self.resolve_profile(harness, role)
                self.assertEqual(result.returncode, 2)
                self.assertIn('error', json.loads(result.stdout))
                self.assertNotIn('Traceback', result.stderr)

    def test_profile_cli_usage_error_is_json(self):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/role-profile.py'), '--harness', 'codex'],
                                capture_output=True, text=True,
                                env=dict(os.environ, AGENTS_METRICS='off'))
        self.assertEqual(result.returncode, 2)
        self.assertIn('error', json.loads(result.stdout))

    def test_profile_cli_reports_invalid_json_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.copy_package(directory)
            (package / 'harnesses/mapping.json').write_text('{invalid json')
            result = self.resolve_profile('codex', 'worker', package)
            self.assertEqual(result.returncode, 2)
            self.assertIn('error', json.loads(result.stdout))

    def test_relocated_resolver_and_generator_share_harness_model_and_effort(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.copy_package(directory)
            mapping_path = package / 'harnesses/mapping.json'
            mapping = json.loads(mapping_path.read_text())
            mapping['pi']['roles']['reviewer'].update(model='provider/custom-review-model', effort='low')
            mapping_path.write_text(json.dumps(mapping))
            result = self.resolve_profile('pi', 'reviewer', package)
            self.assertEqual(result.returncode, 0, result.stderr)
            profile = json.loads(result.stdout)
            self.assertEqual(profile['model'], 'provider/custom-review-model')
            self.assertEqual(profile['effort'], 'low')
            generated = subprocess.run([sys.executable, str(package / 'tools/sync-agents.py')],
                                       capture_output=True, text=True)
            self.assertEqual(generated.returncode, 0, generated.stderr)
            frontmatter, _ = self.parse_form(package / 'harnesses/pi/reviewer.md')
            self.assertEqual(json.loads(frontmatter['model']), profile['model'])
            self.assertEqual(json.loads(frontmatter['thinking']), profile['effort'])

    def test_relocated_provider_mapping_forms_match_resolver_output(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.copy_package(directory)
            mapping_path = package / 'harnesses/mapping.json'
            mapping = json.loads(mapping_path.read_text())
            table = {name: f'acme/{name}-model' for name in mapping['pi']['roles']}
            table['reviewer'] = {'model': 'acme/reviewer-model', 'effort': 'low'}
            mapping['pi'].update(provider='acme', providers={'acme': table})
            mapping_path.write_text(json.dumps(mapping))
            expected = {name: (f'acme/{name}-model', role['effort'])
                        for name, role in mapping['pi']['roles'].items()}
            expected['reviewer'] = ('acme/reviewer-model', 'low')
            generated = subprocess.run([sys.executable, str(package / 'tools/sync-agents.py')],
                                       capture_output=True, text=True)
            self.assertEqual(generated.returncode, 0, generated.stderr)
            for name, (model, effort) in expected.items():
                with self.subTest(role=name):
                    result = self.resolve_profile('pi', name, package)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    profile = json.loads(result.stdout)
                    self.assertEqual((profile['model'], profile['effort']), (model, effort))
                    frontmatter, _ = self.parse_form(package / 'harnesses/pi' / f'{name}.md')
                    self.assertEqual(json.loads(frontmatter['model']), profile['model'])
                    self.assertEqual(json.loads(frontmatter['thinking']), profile['effort'])

    def test_relocated_check_stays_green_on_committed_providerless_mapping(self):
        self.assertNotIn('provider', self.harnesses['pi'])
        self.assertNotIn('providers', self.harnesses['pi'])
        with tempfile.TemporaryDirectory() as directory:
            package = self.copy_package(directory)
            check = subprocess.run([sys.executable, str(package / 'tools/sync-agents.py'), '--check'],
                                   capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)

    def test_unsupported_effort_is_explicit_and_omitted_from_forms(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.copy_package(directory)
            mapping_path = package / 'harnesses/mapping.json'
            mapping = json.loads(mapping_path.read_text())
            mapping['pi']['roles']['reviewer'].update(effort=None, effort_policy='unsupported')
            mapping_path.write_text(json.dumps(mapping))
            result = self.resolve_profile('pi', 'reviewer', package)
            self.assertEqual(result.returncode, 0, result.stderr)
            profile = json.loads(result.stdout)
            self.assertIsNone(profile['effort'])
            self.assertEqual(profile['effort_policy'], 'unsupported')
            generated = subprocess.run([sys.executable, str(package / 'tools/sync-agents.py')],
                                       capture_output=True, text=True)
            self.assertEqual(generated.returncode, 0, generated.stderr)
            frontmatter, _ = self.parse_form(package / 'harnesses/pi/reviewer.md')
            self.assertNotIn('thinking', frontmatter)
            self.assertNotIn('effort', frontmatter)

    def test_malformed_mapping_fails_before_forms_are_written(self):
        cases = (
            ('missing role', 'roles', {'reviewer': {}}),
            ('unknown tool', 'tool_names', {}),
            ('missing policy', 'roles.reviewer.effort_policy', None),
            ('invalid policy', 'roles.reviewer.effort_policy', 'inherit'),
            ('unsupported with effort', 'roles.reviewer.effort_policy', 'unsupported'),
            ('explicit without effort', 'roles.reviewer.effort', None),
            ('invalid effort', 'roles.reviewer.effort', 'unknown'),
            ('invalid model', 'roles.reviewer.model', ''),
            ('invalid tools', 'roles.reviewer.tools', 'read'),
            ('empty tools', 'roles.reviewer.tools', []),
        )
        with tempfile.TemporaryDirectory() as directory:
            package = self.copy_package(directory)
            mapping_path = package / 'harnesses/mapping.json'
            original = mapping_path.read_text()
            for label, key, value in cases:
                with self.subTest(case=label):
                    mapping = json.loads(original)
                    target = mapping['pi']
                    parts = key.split('.')
                    for part in parts[:-1]:
                        target = target[part]
                    target[parts[-1]] = value
                    mapping_path.write_text(json.dumps(mapping))
                    self.assert_rejection_preserves_forms(
                        package, 'pi', 'reviewer', error_predicate=bool,
                    )

    def test_malformed_provider_mapping_fails_before_forms_are_written(self):
        acme_table = {name: f'acme/{name}-model' for name in self.harnesses['pi']['roles']}
        beta_table = {name: f'beta/{name}-model' for name in self.harnesses['pi']['roles']}
        incomplete = {name: model for name, model in acme_table.items() if name != 'tool-runner'}
        unknown_role = acme_table | {'archivist': 'acme/archivist-model'}
        effort_outside = acme_table | {'worker': {'model': 'acme/worker-model', 'effort': 'ultra'}}
        effort_on_unsupported = acme_table | {
            'tool-runner': {'model': 'acme/tool-runner-model', 'effort': 'low'},
        }
        non_string_model = acme_table | {'worker': {'model': 3}}
        unselected = {
            'acme': acme_table,
            'beta': beta_table | {'worker': {'model': 'beta/worker-model', 'effort': 'turbo'}},
        }

        def unsupported_policy(mapping):
            mapping['pi']['roles']['tool-runner'].update(effort=None, effort_policy='unsupported')
            mapping['pi'].update(provider='acme', providers={'acme': effort_on_unsupported})

        cases = (
            ('providers without provider',
             lambda mapping: mapping['pi'].update(providers={'acme': acme_table}),
             'pi: providers require provider'),
            ('provider missing from providers',
             lambda mapping: mapping['pi'].update(provider='gone', providers={'acme': acme_table}),
             'pi: unknown provider: gone'),
            ('incomplete table',
             lambda mapping: mapping['pi'].update(provider='acme', providers={'acme': incomplete}),
             "pi.acme: table mismatch missing=['tool-runner'] extra=[]"),
            ('table with unknown role names',
             lambda mapping: mapping['pi'].update(provider='acme', providers={'acme': unknown_role}),
             "pi.acme: table mismatch missing=[] extra=['archivist']"),
            ('malformed table that no resolution selects',
             lambda mapping: mapping['pi'].update(provider='acme', providers=unselected),
             'pi.beta.worker: invalid entry effort'),
            ('entry effort outside the pi set',
             lambda mapping: mapping['pi'].update(provider='acme', providers={'acme': effort_outside}),
             'pi.acme.worker: invalid entry effort'),
            ('entry effort on an unsupported policy role', unsupported_policy,
             'pi.acme.tool-runner: unsupported effort must be null'),
            ('non-string entry model',
             lambda mapping: mapping['pi'].update(provider='acme', providers={'acme': non_string_model}),
             'pi.acme.worker.model: expected a nonempty string'),
        )
        with tempfile.TemporaryDirectory() as directory:
            package = self.copy_package(directory)
            mapping_path = package / 'harnesses/mapping.json'
            original = mapping_path.read_text()
            for label, mutate, error in cases:
                with self.subTest(case=label):
                    mapping = json.loads(original)
                    mutate(mapping)
                    mapping_path.write_text(json.dumps(mapping))
                    self.assert_rejection_preserves_forms(package, 'pi', 'reviewer', error=error)

    def test_write_mode_removes_forms_of_retired_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            relocated = copy_package(directory)
            (relocated / 'plugin.json').write_text((ROOT / 'plugin.json').read_text())
            stale = relocated / 'harnesses' / 'claude' / 'retired.md'
            stale.write_text('---\nname: "retired"\n---\nbody\n')
            result = subprocess.run([sys.executable, str(relocated / 'tools' / 'sync-agents.py')],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(stale.exists())
            check = subprocess.run([sys.executable, str(relocated / 'tools' / 'sync-agents.py'), '--check'],
                                   capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)

    def test_unknown_generator_arguments_preserve_every_form(self):
        cases = (('--chek',), ('--chec',), ('--check', '--unknown'), ('unexpected',), ('--check=true',),
                 ('--provider', 'acme'))
        for arguments in cases:
            with self.subTest(arguments=arguments), tempfile.TemporaryDirectory() as directory:
                package = self.copy_package(directory)
                forms = package / 'harnesses'
                (forms / 'claude/worker.md').write_text('existing form\n')
                (forms / 'claude/retired.md').write_text('retired form\n')
                before = {path.relative_to(forms): path.read_bytes() for path in forms.rglob('*') if path.is_file()}
                result = subprocess.run(
                    [sys.executable, str(package / 'tools/sync-agents.py'), *arguments],
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn('usage:', result.stderr)
                self.assertEqual(result.stdout, '')
                after = {path.relative_to(forms): path.read_bytes() for path in forms.rglob('*') if path.is_file()}
                self.assertEqual(after, before)

    def test_antigravity_forms_match_resolved_profiles_and_canonical_bodies(self):
        native = load_script_module(ROOT / 'tools/native_agent_forms.py', 'native_agent_forms')
        forms = {
            name: native.form_path(ROOT, 'antigravity', name)
            for name in self.roles
        }
        for name, role in self.roles.items():
            with self.subTest(role=name):
                path = forms[name]
                self.assertTrue(path.is_file(), path)
                self.assertEqual(path.parent, ROOT / 'agents')
                self.assertEqual(path.name, f'code-flow-{name}.md')
                result = self.resolve_profile('antigravity', name)
                self.assertEqual(result.returncode, 0, result.stderr)
                profile = json.loads(result.stdout)
                frontmatter, body = self.parse_form(path)
                self.assertEqual(json.loads(frontmatter['name']), f'code-flow-{name}')
                self.assertEqual(json.loads(frontmatter['description']), role['description'])
                self.assertEqual(json.loads(frontmatter['model']), 'inherit')
                self.assertEqual(frontmatter['subagent'], 'true')
                self.assertEqual(frontmatter['mainAgent'], 'false')
                self.assertEqual(json.loads(frontmatter['commandExecutionPolicy']), 'sandbox')
                self.assertNotIn('effort', frontmatter)
                self.assertNotIn('thinking', frontmatter)
                self.assertEqual(json.loads(frontmatter['tools']), profile['tools'])
                self.assertEqual(body, role['developer_instructions'].strip('\n'))

    def test_native_client_check_detects_stale_missing_and_extra_forms_and_preserves_unmanaged_files(self):
        for client, rel_dir, toml_dir in (('antigravity', 'agents', 'roles'), ('empryo', 'harnesses/empryo', None)):
            with self.subTest(client=client), tempfile.TemporaryDirectory() as directory:
                package = self.copy_package(directory)
                target_dir = package / rel_dir
                unmanaged = target_dir / 'README.md'
                unmanaged.write_text('# Unmanaged documentation\n')
                toml_before = (package / toml_dir / 'reviewer.toml').read_bytes() if toml_dir else None

                check = subprocess.run([sys.executable, str(package / 'tools/sync-agents.py'), '--check'],
                                       capture_output=True, text=True)
                self.assertEqual(check.returncode, 0, check.stderr)

                target = target_dir / 'code-flow-reviewer.md'
                target.write_text('drifted\n')
                retired = target_dir / 'code-flow-retired.md'
                retired.write_text('---\nname: "code-flow-retired"\n---\nbody\n')

                stale_check = subprocess.run([sys.executable, str(package / 'tools/sync-agents.py'), '--check'],
                                             capture_output=True, text=True)
                self.assertEqual(stale_check.returncode, 1)
                self.assertIn(f'{rel_dir}/code-flow-reviewer.md', stale_check.stderr)
                self.assertIn(f'{rel_dir}/code-flow-retired.md', stale_check.stderr)
                self.assertNotIn('README.md', stale_check.stderr)

                sync = subprocess.run([sys.executable, str(package / 'tools/sync-agents.py')],
                                      capture_output=True, text=True)
                self.assertEqual(sync.returncode, 0, sync.stderr)
                self.assertFalse(retired.exists())
                self.assertEqual(unmanaged.read_text(), '# Unmanaged documentation\n')
                if toml_dir:
                    self.assertEqual((package / toml_dir / 'reviewer.toml').read_bytes(), toml_before)
                self.assertEqual(target.read_text(), (ROOT / rel_dir / 'code-flow-reviewer.md').read_text())

                target.unlink()
                missing_check = subprocess.run([sys.executable, str(package / 'tools/sync-agents.py'), '--check'],
                                               capture_output=True, text=True)
                self.assertEqual(missing_check.returncode, 1)
                self.assertIn(f'{rel_dir}/code-flow-reviewer.md', missing_check.stderr)

    def test_diverged_harness_capability_sets_fail_before_writing(self):
        def add_unknown_capability(package, mapping):
            for config in mapping.values():
                config['tool_names']['web_search'] = 'web_search'
                config['roles']['reviewer']['tools'].append('web_search')

        def add_unknown_role(package, mapping):
            reviewer_toml = (package / 'roles/reviewer.toml').read_text()
            (package / 'roles/archivist.toml').write_text(
                reviewer_toml.replace('name = "reviewer"', 'name = "archivist"', 1)
            )
            for config in mapping.values():
                config['roles']['archivist'] = dict(config['roles']['reviewer'])

        cases = (
            (
                'diverged tools',
                lambda _pkg, mapping: mapping['antigravity']['roles']['reviewer'].__setitem__(
                    'tools', ['read', 'grep']
                ),
                'reviewer: capability mismatch in claude:',
            ),
            (
                'missing tools',
                lambda _pkg, mapping: mapping['antigravity']['roles']['reviewer'].pop('tools'),
                'antigravity.reviewer: missing tools',
            ),
            (
                'reordered tools',
                lambda _pkg, mapping: mapping['antigravity']['roles']['reviewer'].__setitem__(
                    'tools', list(reversed(mapping['antigravity']['roles']['reviewer']['tools']))
                ),
                'reviewer: capability mismatch in claude:',
            ),
            (
                'unknown empryo capability',
                add_unknown_capability,
                'reviewer: unknown empryo capability: web_search',
            ),
            (
                'unknown empryo role',
                add_unknown_role,
                'unknown empryo role: archivist',
            ),
        )
        def snapshot_forms(package):
            return {
                path.relative_to(package): path.read_bytes()
                for path in (*((package / 'agents').glob('*.md')), *((package / 'harnesses').rglob('*.md')))
            }

        for label, mutate, expected_error in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                package = self.copy_package(directory)
                mapping_path = package / 'harnesses/mapping.json'
                mapping = json.loads(mapping_path.read_text())
                mutate(package, mapping)
                mapping_path.write_text(json.dumps(mapping))
                before = snapshot_forms(package)
                sync = subprocess.run([sys.executable, str(package / 'tools/sync-agents.py')],
                                      capture_output=True, text=True)
                self.assertEqual(sync.returncode, 2, sync.stdout + sync.stderr)
                self.assertEqual(sync.stdout, '')
                payload = json.loads(sync.stderr)
                self.assertEqual(set(payload), {'error'})
                self.assertIn(expected_error, payload['error'])
                after = snapshot_forms(package)
                self.assertEqual(after, before)

    def test_antigravity_provider_table_renders_concrete_model_without_effort(self):
        with tempfile.TemporaryDirectory() as directory:
            package = self.copy_package(directory)
            mapping_path = package / 'harnesses/mapping.json'
            mapping = json.loads(mapping_path.read_text())
            table = {name: f'acme/{name}-model' for name in mapping['antigravity']['roles']}
            mapping['antigravity'].update(provider='acme', providers={'acme': table})
            mapping_path.write_text(json.dumps(mapping))
            sync = subprocess.run([sys.executable, str(package / 'tools/sync-agents.py')],
                                  capture_output=True, text=True)
            self.assertEqual(sync.returncode, 0, sync.stderr)
            frontmatter, _ = self.parse_form(package / 'agents/code-flow-reviewer.md')
            self.assertEqual(json.loads(frontmatter['model']), 'acme/reviewer-model')
            self.assertNotIn('effort', frontmatter)
            self.assertNotIn('thinking', frontmatter)

    def test_empryo_forms_match_capabilities_modes_and_canonical_bodies(self):
        native = load_script_module(ROOT / 'tools/native_agent_forms.py', 'native_agent_forms')
        expected_tools = {
            'worker': 'read, edit_file, grep, find, shell',
            'tool-runner': 'read, grep, find, shell, edit_file',
            'reviewer': 'read, grep, find, shell',
            'style-reviewer': 'read, grep, find, shell',
            'plan-reviewer': 'read, grep, find, shell',
            'spec-reviewer': 'read, grep, find, shell',
        }
        expected_modes = {
            'worker': 'code',
            'tool-runner': 'code',
            'reviewer': 'explore',
            'style-reviewer': 'explore',
            'plan-reviewer': 'explore',
            'spec-reviewer': 'explore',
        }
        self.assertEqual(set(expected_tools), set(self.roles))
        for name, role in self.roles.items():
            with self.subTest(role=name):
                path = native.form_path(ROOT, 'empryo', name)
                self.assertTrue(path.is_file(), path)
                self.assertEqual(path.parent, ROOT / 'harnesses/empryo')
                self.assertEqual(path.name, f'code-flow-{name}.md')
                frontmatter, body = self.parse_form(path)
                self.assertEqual(json.loads(frontmatter['name']), f'code-flow-{name}')
                self.assertEqual(json.loads(frontmatter['description']), role['description'])
                self.assertEqual(json.loads(frontmatter['model']), 'inherit')
                self.assertEqual(json.loads(frontmatter['mode']), expected_modes[name])
                self.assertEqual(frontmatter['tools'], expected_tools[name])
                self.assertNotIn('effort', frontmatter)
                self.assertNotIn('thinking', frontmatter)
                self.assertEqual(body, role['developer_instructions'].strip('\n'))


if __name__ == '__main__':
    unittest.main()
