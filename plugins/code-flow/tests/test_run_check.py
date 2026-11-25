import contextlib
import io
import json
import os
from pathlib import Path, PosixPath
import runpy
import select
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import ANY, Mock, call, patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/run-check.py'


class RunCheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.repo = self.directory / 'repository with spaces'
        self.repo.mkdir()

    def cli(self, *arguments):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, arguments)],
            capture_output=True, text=True, timeout=20,
            env=dict(os.environ, AGENTS_HOME=str(self.directory / 'metrics home')),
        )
        data = json.loads(result.stdout)
        if data.get('log_path'):
            self.addCleanup(Path(data['log_path']).unlink, missing_ok=True)
        return result.returncode, data

    def run_child(self, code, *arguments):
        return self.cli('--cwd', self.repo, '--', sys.executable, '-c', code, *arguments)

    def test_preserves_argv_cwd_output_environment_and_closed_stdin(self):
        arguments = ['a b', '$(touch injected)', 'semi;colon', 'quote"', '--flag']
        command = (
            'import json, os, sys; '
            'print(json.dumps({"arguments": sys.argv[1:], "cwd": os.getcwd(), '
            '"env": {key: os.environ[key] for key in '
            '("CI", "NO_COLOR", "TERM", "PAGER")}, "stdin": sys.stdin.read()})); '
            'print("stderr entry", file=sys.stderr)'
        )
        code, result = self.run_child(command, *arguments)
        self.assertEqual(code, 0)
        self.assertEqual(result['command'], [sys.executable, '-c', command, *arguments])
        self.assertEqual(result['cwd'], str(self.repo.resolve()))
        self.assertEqual(result['exit_status'], 0)
        self.assertFalse(result['timed_out'])
        lines = Path(result['log_path']).read_text().splitlines()
        self.assertIn('stderr entry', lines)
        payload = json.loads(next(line for line in lines if line.startswith('{')))
        self.assertEqual(payload, {
            'arguments': arguments, 'cwd': str(self.repo.resolve()),
            'env': {'CI': '1', 'NO_COLOR': '1', 'TERM': 'dumb', 'PAGER': 'cat'},
            'stdin': '',
        })
        self.assertEqual(list(self.repo.iterdir()), [])

    def test_failed_check_runs_once_and_preserves_actual_exit_status(self):
        counter = self.directory / 'execution count'
        command = (
            'from pathlib import Path; import sys; '
            'path = Path(sys.argv[1]); '
            'path.write_text(path.read_text() + "run\\n" if path.exists() else "run\\n"); '
            'print("failure: fixture.py:4"); sys.exit(2)'
        )
        code, result = self.run_child(command, str(counter))
        self.assertEqual(code, 1)
        self.assertEqual(result['exit_status'], 2)
        self.assertFalse(result['timed_out'])
        self.assertEqual(counter.read_text(), 'run\n')
        self.assertEqual(Path(result['log_path']).read_text(), 'failure: fixture.py:4\n')

    @unittest.skipUnless(os.name == 'posix', 'requires Unix signal exit statuses')
    def test_signal_exit_is_a_check_failure(self):
        code, result = self.run_child('import os, signal; os.kill(os.getpid(), signal.SIGTERM)')
        self.assertEqual(code, 1)
        self.assertEqual(result['exit_status'], -signal.SIGTERM)
        self.assertFalse(result['timed_out'])

    @unittest.skipUnless(os.name == 'posix', 'requires Unix process groups')
    def test_successful_leader_exit_terminates_its_surviving_descendant(self):
        fifo = self.directory / 'descendant.fifo'
        os.mkfifo(fifo)
        reader = os.fdopen(os.open(fifo, os.O_RDONLY | os.O_NONBLOCK), 'rb', buffering=0)
        self.addCleanup(reader.close)
        command = '''import os, signal, sys
read_fd, write_fd = os.pipe()
if os.fork() == 0:
    os.close(read_fd)
    output = os.open(sys.argv[1], os.O_WRONLY)
    os.write(output, (str(os.getpgrp()) + "\\n").encode())
    os.write(write_fd, b"ready")
    os.close(write_fd)
    signal.pause()
os.close(write_fd)
assert os.read(read_fd, 5) == b"ready"
os.close(read_fd)
'''

        code, result = self.run_child(command, str(fifo))

        group = int(reader.read(4096))
        self.addCleanup(self.kill_group_if_present, group)
        self.assertEqual(code, 0)
        self.assertEqual(result['exit_status'], 0)
        self.assertFalse(result['timed_out'])
        self.assertEqual(select.select([reader], [], [], 5)[0], [reader])
        self.assertEqual(reader.read(1), b'')

    def kill_group_if_present(self, group):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(group, signal.SIGKILL)

    def test_each_execution_gets_a_unique_log_outside_the_repository(self):
        first_code, first = self.run_child('print("first")')
        second_code, second = self.run_child('print("second")')
        self.assertEqual((first_code, second_code), (0, 0))
        self.assertNotEqual(first['log_path'], second['log_path'])
        self.assertFalse(Path(first['log_path']).is_relative_to(self.repo))
        self.assertEqual(Path(first['log_path']).read_text(), 'first\n')
        self.assertEqual(Path(second['log_path']).read_text(), 'second\n')

    def test_binary_output_is_preserved(self):
        code, result = self.run_child('import os; os.write(1, b"\\xff\\x00")')
        self.assertEqual(code, 0)
        self.assertEqual(Path(result['log_path']).read_bytes(), b'\xff\x00')

    def test_shell_syntax_requires_an_explicit_shell(self):
        code, result = self.cli('--cwd', self.repo, '--', 'bash', '-c', 'printf "%s" "two words"')
        self.assertEqual(code, 0)
        self.assertEqual(Path(result['log_path']).read_text(), 'two words')

    def test_launch_error_is_distinct_from_a_failed_check(self):
        code, result = self.cli('--cwd', self.repo, '--', self.directory / 'missing executable')
        self.assertEqual(code, 2)
        self.assertIsNone(result['exit_status'])
        self.assertFalse(result['timed_out'])
        self.assertIn('error', result)
        self.assertTrue(Path(result['log_path']).is_file())

    def test_usage_errors_emit_json_without_running_a_command(self):
        cases = [
            (), ('--cwd', self.repo), ('--cwd', self.repo, '--'),
            ('--cwd', self.repo, sys.executable),
            ('--cwd', self.directory / 'missing', '--', sys.executable),
            ('--cwd', SCRIPT, '--', sys.executable),
            ('--cwd', self.repo, '--timeout', '0', '--', sys.executable),
            ('--cwd', self.repo, '--timeout', '-1', '--', sys.executable),
            ('--cwd', self.repo, '--timeout', 'nan', '--', sys.executable),
            ('--cwd', self.repo, '--timeout', 'inf', '--', sys.executable),
            ('--cwd', self.repo, '--timeout', 'invalid', '--', sys.executable),
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                code, result = self.cli(*arguments)
                self.assertEqual(code, 2)
                self.assertIn('error', result)
        self.assertEqual(list(self.repo.iterdir()), [])

    @unittest.skipUnless(os.name == 'posix', 'requires Unix process groups')
    def test_timeout_kills_the_process_group_and_reaps_the_child_once(self):
        module = runpy.run_path(str(SCRIPT))
        process = Mock(pid=12345, returncode=-signal.SIGKILL)
        process.wait.side_effect = [subprocess.TimeoutExpired(['fixture'], 7), -signal.SIGKILL]
        output = io.StringIO()
        with patch.object(sys, 'argv', [str(SCRIPT), '--cwd', str(self.repo), '--timeout', '7', '--', 'fixture']), \
                patch('subprocess.Popen', return_value=process) as launch, \
                patch('os.killpg') as kill_group, contextlib.redirect_stdout(output):
            code = module['main']()
        result = json.loads(output.getvalue())
        self.addCleanup(Path(result['log_path']).unlink, missing_ok=True)
        self.assertEqual(code, 1)
        self.assertTrue(result['timed_out'])
        self.assertEqual(result['exit_status'], -signal.SIGKILL)
        launch.assert_called_once_with(
            ['fixture'], cwd=self.repo.resolve(), env=os.environ | {
                'CI': '1', 'NO_COLOR': '1', 'TERM': 'dumb', 'PAGER': 'cat',
            }, stdin=subprocess.DEVNULL, stdout=ANY,
            stderr=subprocess.STDOUT, start_new_session=True,
        )
        log = launch.call_args.kwargs['stdout']
        self.assertEqual(log.name, result['log_path'])
        self.assertEqual(log.mode, 'rb+')
        kill_group.assert_called_once_with(12345, signal.SIGKILL)
        self.assertEqual(process.wait.call_args_list, [call(timeout=7), call()])

    def test_timeout_kills_the_immediate_child_on_non_posix_systems(self):
        module = runpy.run_path(str(SCRIPT))
        process = Mock(pid=12345, returncode=-9)
        process.wait.side_effect = [subprocess.TimeoutExpired(['fixture'], 7), -9]
        output = io.StringIO()
        with patch.object(sys, 'argv', [str(SCRIPT), '--cwd', str(self.repo), '--timeout', '7', '--', 'fixture']), \
                patch('subprocess.Popen', return_value=process) as launch, \
                patch.dict(module['main'].__globals__, {'Path': PosixPath}), patch('os.name', 'nt'), \
                patch('os.killpg') as kill_group, contextlib.redirect_stdout(output):
            code = module['main']()
        result = json.loads(output.getvalue())
        self.addCleanup(Path(result['log_path']).unlink, missing_ok=True)
        self.assertEqual(code, 1)
        self.assertTrue(result['timed_out'])
        self.assertEqual(result['exit_status'], -9)
        launch.assert_called_once_with(
            ['fixture'], cwd=self.repo.resolve(), env=os.environ | {
                'CI': '1', 'NO_COLOR': '1', 'TERM': 'dumb', 'PAGER': 'cat',
            }, stdin=subprocess.DEVNULL, stdout=ANY,
            stderr=subprocess.STDOUT, start_new_session=False,
        )
        process.kill.assert_called_once_with()
        kill_group.assert_not_called()
        self.assertEqual(process.wait.call_args_list, [call(timeout=7), call()])

    def test_default_timeout_is_300_seconds(self):
        module = runpy.run_path(str(SCRIPT))
        process = Mock(pid=12345, returncode=0)
        output = io.StringIO()
        with patch.object(sys, 'argv', [str(SCRIPT), '--cwd', str(self.repo), '--', 'fixture']), \
                patch('subprocess.Popen', return_value=process) as launch, \
                patch('os.killpg', create=True), \
                contextlib.redirect_stdout(output):
            code = module['main']()
        result = json.loads(output.getvalue())
        self.addCleanup(Path(result['log_path']).unlink, missing_ok=True)
        self.assertEqual(code, 0)
        launch.assert_called_once()
        process.wait.assert_called_once_with(timeout=300)

    @unittest.skipUnless(os.name == 'posix', 'requires Unix process groups')
    def test_process_group_that_already_exited_is_still_reported_as_timed_out(self):
        module = runpy.run_path(str(SCRIPT))
        process = Mock(pid=12345, returncode=0)
        process.wait.side_effect = [subprocess.TimeoutExpired(['fixture'], 300), 0]
        output = io.StringIO()
        with patch.object(sys, 'argv', [str(SCRIPT), '--cwd', str(self.repo), '--', 'fixture']), \
                patch('subprocess.Popen', return_value=process) as launch, \
                patch('os.killpg', side_effect=ProcessLookupError) as kill_group, \
                contextlib.redirect_stdout(output):
            code = module['main']()
        result = json.loads(output.getvalue())
        self.addCleanup(Path(result['log_path']).unlink, missing_ok=True)
        self.assertEqual(code, 1)
        self.assertTrue(result['timed_out'])
        self.assertEqual(result['exit_status'], 0)
        launch.assert_called_once()
        kill_group.assert_called_once_with(12345, signal.SIGKILL)
        self.assertEqual(process.wait.call_args_list, [call(timeout=300), call()])


if __name__ == '__main__':
    unittest.main()
