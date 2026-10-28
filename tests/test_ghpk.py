import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from ghpk import cli, github, packages
from ghpk.storage import Store, read_json


def asset(name, **extra):
    return dict(name=name, browser_download_url='https://github.com/file/' + name,
                digest='sha256:' + 'a' * 64, size=10, **extra)


class RepositoryTests(unittest.TestCase):
    def test_normalization(self):
        for value in ['Owner/Project', 'https://github.com/Owner/Project/', 'https://github.com/Owner/Project.git']:
            self.assertEqual(github.normalize_repo(value), 'owner/project')

    def test_reject_other_hosts_and_subpaths(self):
        for value in ['http://github.com/a/b', 'https://evil.test/a/b', 'a/b/releases', 'https://github.com/a/b?x=y', 'https://github.com/a/b#x']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                github.normalize_repo(value)

    def test_architecture_aliases(self):
        for arch, token in [('amd64', 'amd64'), ('amd64', 'x86_64'), ('amd64', 'x64'), ('arm64', 'aarch64'), ('armhf', 'armv7'), ('i386', 'i686'), ('riscv64', 'riscv64')]:
            candidate = asset('app-1-' + token + '.deb')
            self.assertEqual(github.select_asset([candidate], arch), candidate)

    def test_no_partial_architecture_matches(self):
        with self.assertRaises(ValueError):
            github.select_asset([asset('app-x86_64.deb')], 'i386')

    def test_all_and_unlabelled(self):
        for name in ['app_1_all.deb', 'app-noarch.deb', 'app.deb']:
            candidate = asset(name)
            self.assertEqual(github.select_asset([candidate], 'amd64'), candidate)

    def test_prefer_native_over_all(self):
        self.assertEqual(github.select_asset([asset('a_all.deb'), asset('a_amd64.deb')], 'amd64')['name'], 'a_amd64.deb')

    def test_ambiguous_requires_config_filter(self):
        candidates = [asset('app-amd64.deb'), asset('app-sciter-amd64.deb')]
        with self.assertRaises(ValueError):
            github.select_asset(candidates, 'amd64')
        self.assertEqual(github.select_asset(candidates, 'amd64', '^app-amd64'), candidates[0])

    def test_non_deb_never_selected(self):
        with self.assertRaises(ValueError):
            github.select_asset([asset('app-amd64.rpm')], 'amd64')

    def test_latest_missing_integrity_fails(self):
        release = dict(tag_name='v1', assets=[dict(name='a_amd64.deb', browser_download_url='https://example.org/a')])
        with patch.object(github, 'request', return_value=io.BytesIO(json.dumps(release).encode())), self.assertRaises(ValueError):
            github.latest({'repo': 'a/b'}, 'amd64')

    def test_checksum_formats(self):
        target = dict(name='a.deb', manifests=['https://example.org/SHA256SUMS'])
        for text in ['a' * 64 + '  a.deb', 'a' * 64 + ' *./a.deb', 'SHA256 (a.deb) = ' + 'a' * 64]:
            with patch.object(github, 'request', return_value=io.BytesIO(text.encode())):
                self.assertEqual(github.checksum_from_release(target, 'a/b'), 'sha256:' + 'a' * 64)

    def test_conflicting_checksums_fail(self):
        text = 'a' * 64 + '  a.deb\n' + 'b' * 64 + '  a.deb'
        with patch.object(github, 'request', return_value=io.BytesIO(text.encode())), self.assertRaises(ValueError):
            github.checksum_from_release(dict(name='a.deb', manifests=['https://example.org/sums']), 'a/b')


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.environment = patch.dict(os.environ, XDG_CONFIG_HOME=self.temporary.name, XDG_CACHE_HOME=self.temporary.name)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.uid = patch('os.geteuid', return_value=1000)
        self.uid.start()
        self.addCleanup(self.uid.stop)
        self.arch = patch.object(packages, 'architecture', return_value='amd64')
        self.arch.start()
        self.addCleanup(self.arch.stop)
        self.store = Store()
        self.release = dict(tag='v1', name='app-amd64.deb', digest='sha256:'+'a'*64, architecture='amd64', pattern=None)

    def test_only_three_commands(self):
        self.assertEqual(cli.run([]), 0)
        for args in [['install'], ['sync'], ['--config', 'file'], ['update', 'a/b'], ['upgrade', '--asset', 'a']]:
            self.assertEqual(cli.run(args), 2)

    def test_multiple_install_deduplicates_and_persists(self):
        def install(entry, release, arch, state):
            entry['package'] = entry['repo'].split('/')[1]
            return 'installed'
        with patch.object(github, 'latest', return_value=self.release), patch.object(packages, 'install_asset', side_effect=install) as operation:
            self.assertEqual(cli.run(['install', 'a/b', 'https://github.com/A/B', 'c/d']), 0)
        self.assertEqual(operation.call_count, 2)
        self.assertEqual([e['repo'] for e in read_json(self.store.config, {})['repos']], ['a/b', 'c/d'])

    def test_failure_continues_and_only_success_is_saved(self):
        with patch.object(github, 'latest', return_value=self.release), patch.object(packages, 'install_asset', side_effect=[ValueError('bad package'), 'installed']) as operation:
            self.assertEqual(cli.run(['install', 'a/b', 'c/d']), 1)
        self.assertEqual(operation.call_count, 2)
        self.assertEqual([e['repo'] for e in read_json(self.store.config, {})['repos']], ['c/d'])

    def test_update_never_installs_and_upgrade_uses_cache(self):
        self.store.config.write_text(json.dumps({'repos': [{'repo': 'a/b'}]}))
        with patch.object(github, 'latest', return_value=self.release) as latest, patch.object(packages, 'install_asset', return_value='unchanged') as install:
            self.assertEqual(cli.run(['update']), 0)
            install.assert_not_called()
            self.assertEqual(cli.run(['upgrade']), 0)
            self.assertEqual(latest.call_count, 1)
            install.assert_called_once()

    def test_update_summary_counts(self):
        self.store.config.write_text(json.dumps({'repos': [
            {'repo': repo, 'package': 'app'} for repo in ['a/b', 'c/d', 'e/f']]}))
        release = dict(self.release, tag='v2.0')
        with patch.object(github, 'latest', return_value=release), patch.object(
                packages, 'installed_version', side_effect=['1.0-1', '2.0-1', None]), patch.object(cli, 'message') as output:
            self.assertEqual(cli.run(['update']), 0)
        output.assert_any_call('Доступно обновлений: 1.')
        output.assert_any_call('Не удалось определить наличие обновления: 1.')

    def test_upgrade_requires_update(self):
        self.store.config.write_text(json.dumps({'repos': [{'repo': 'a/b'}]}))
        with patch.object(packages, 'install_asset') as install:
            self.assertEqual(cli.run(['upgrade']), 1)
        install.assert_not_called()

    def test_disabled_entries_not_updated(self):
        self.store.config.write_text(json.dumps({'repos': [{'repo': 'a/b', 'enabled': False}]}))
        with patch.object(github, 'latest') as latest:
            self.assertEqual(cli.run(['update']), 0)
        latest.assert_not_called()

    def test_invalid_batch_rejected_before_install(self):
        with patch.object(packages, 'install_asset') as install, self.assertRaises(ValueError):
            cli.run(['install', 'a/b', 'https://evil.test/c/d'])
        install.assert_not_called()


class PackageTests(unittest.TestCase):
    def test_update_availability(self):
        for tag, current, expected in [
            ('v0.12.0', '0.12.0-1', False),
            ('v0.13.0', '0.12.0-1', True),
            ('v0.9.0', '0.12.0-1', False),
            ('v0.12.0', None, None),
            ('stable', '0.12.0-1', None),
        ]:
            with self.subTest(tag=tag, current=current):
                self.assertIs(packages.update_available(dict(tag=tag), current, {}), expected)


    def test_real_deb_metadata_and_install_handoff(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            control = folder / 'pkg' / 'DEBIAN'
            control.mkdir(parents=True)
            control.joinpath('control').write_text('Package: ghpk-test\nVersion: 1.0\nArchitecture: all\nMaintainer: Test <test@example.org>\nDescription: Test fixture\n')
            deb = folder / 'fixture.deb'
            subprocess.run(['dpkg-deb', '--build', '--root-owner-group', str(control.parent), str(deb)], check=True, capture_output=True)
            payload = deb.read_bytes()
            data = dict(tag='v1', name='app.deb', url='https://github.com/file', digest='sha256:'+hashlib.sha256(payload).hexdigest(), size=len(payload))
            response = io.BytesIO(payload)
            response.headers = {'Content-Length': str(len(payload))}
            versions = iter([None, '1.0'])
            state = {}
            actual_run = subprocess.run
            installs = []
            def dispatch(args, **kwargs):
                if args[:3] == ['sudo', 'apt', 'install']:
                    installs.append(args)
                    return subprocess.CompletedProcess(args, 0)
                return actual_run(args, **kwargs)
            with patch.object(packages, 'request', return_value=response), patch.object(packages, 'installed_version', side_effect=lambda _: next(versions)), patch.object(packages.subprocess, 'run', side_effect=dispatch):
                self.assertEqual(packages.install_asset({'repo': 'a/b'}, data, 'amd64', state), 'installed')
            self.assertEqual(len(installs), 1)
            self.assertEqual(state['a/b']['package'], 'ghpk-test')

    def test_checksum_failure_never_calls_apt(self):
        response = io.BytesIO(b'invalid package')
        response.headers = {}
        data = dict(tag='v1', name='app.deb', url='https://github.com/file', digest='sha256:'+'0'*64)
        with patch.object(packages, 'request', return_value=response), patch.object(packages.subprocess, 'run') as apt, self.assertRaisesRegex(ValueError, 'SHA256'):
            packages.install_asset({'repo': 'a/b'}, data, 'amd64', {})
        apt.assert_not_called()

    def test_unchanged_no_download(self):
        data = dict(digest='sha256:'+'a'*64)
        state = {'a/b': dict(package='app', version='1.0', digest=data['digest'])}
        with patch.object(packages, 'request') as request, patch.object(packages, 'installed_version', return_value='1.0'):
            self.assertEqual(packages.install_asset({'repo': 'a/b'}, data, 'amd64', state), 'unchanged')
        request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
