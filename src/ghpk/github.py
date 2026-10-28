"""GitHub release lookup, asset selection and checksum manifests."""
import json
import os
import re
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from .ui import stage

ALIASES = {
    'amd64': ('amd64', 'x86_64', 'x64'),
    'arm64': ('arm64', 'aarch64'),
    'armhf': ('armhf', 'armv7', 'armv7l'),
    'i386': ('i386', 'i686', 'x86'),
    'riscv64': ('riscv64',),
}


def normalize_repo(value):
    if not isinstance(value, str):
        raise ValueError('Репозиторий должен быть строкой.')
    value = value.strip()
    if '://' in value:
        parsed = urlparse(value)
        if parsed.scheme != 'https' or parsed.netloc.lower() != 'github.com' or parsed.query or parsed.fragment:
            raise ValueError('Ожидается HTTPS-ссылка на репозиторий github.com.')
        value = parsed.path.strip('/')
    value = value.rstrip('/').removesuffix('.git')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value):
        raise ValueError('Ожидается автор/репозиторий или https://github.com/автор/репозиторий.')
    return value.lower()


def request(url):
    parsed = urlparse(url)
    if parsed.scheme != 'https':
        raise ValueError('Разрешены только HTTPS URL.')
    headers = {'User-Agent': 'ghpk/0.1.0'}
    if parsed.hostname == 'api.github.com':
        headers['Accept'] = 'application/vnd.github+json'
        if os.environ.get('GITHUB_TOKEN'):
            headers['Authorization'] = 'Bearer ' + os.environ['GITHUB_TOKEN']
    return urlopen(Request(url, headers=headers), timeout=60)


def has_token(name, tokens):
    alternatives = [re.escape(token) + (r'(?!_64)' if token == 'x86' else '') for token in tokens]
    return re.search(r'(?<![a-z0-9])(?:' + '|'.join(alternatives) + r')(?![a-z0-9])', name.lower()) is not None


def select_asset(assets, arch, pattern=None):
    debs = [asset for asset in assets if asset['name'].lower().endswith('.deb')]
    if pattern:
        candidates = [asset for asset in debs if re.search(pattern, asset['name'])]
    else:
        candidates = [asset for asset in debs if has_token(asset['name'], ALIASES.get(arch, (arch,)))]
        if not candidates:
            candidates = [asset for asset in debs if has_token(asset['name'], ('all', 'noarch'))]
        if not candidates:
            # Some projects omit the architecture; dpkg-deb still validates it before installation.
            known = tuple(token for values in ALIASES.values() for token in values)
            candidates = [asset for asset in debs if not has_token(asset['name'], known)]
    if len(candidates) != 1:
        names = ', '.join(asset['name'] for asset in debs) or 'нет'
        raise ValueError('Не удалось однозначно выбрать DEB. Укажите asset в repos.json. Доступные пакеты: ' + names)
    return candidates[0]


def latest(entry, arch):
    with stage('Чтение сведений о релизе ' + entry['repo']):
        with request('https://api.github.com/repos/' + entry['repo'] + '/releases/latest') as stream:
            release = json.load(stream)
    assets = release.get('assets', [])
    asset = select_asset(assets, arch, entry.get('asset'))
    digest = asset.get('digest') or ''
    if not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', digest):
        digest = None
    manifests = [a for a in assets if re.fullmatch(r'(?:sha256sums?(?:\.txt)?|checksums?(?:\.txt)?)', a['name'], re.I)
                 or a['name'] in (asset['name'] + '.sha256', asset['name'] + '.sha256sum')]
    if not digest and not manifests:
        raise ValueError('Нет SHA256 от GitHub и поддерживаемого файла контрольных сумм.')
    return dict(tag=release['tag_name'], name=asset['name'], url=asset['browser_download_url'],
                digest=digest, manifests=[a['browser_download_url'] for a in manifests],
                architecture=arch, pattern=entry.get('asset'), size=asset.get('size'))


def checksum_from_release(asset, repo):
    matches = set()
    with stage('Чтение контрольных сумм ' + repo):
        for url in asset.get('manifests', []):
            with request(url) as stream:
                data = stream.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024:
                raise ValueError('Файл контрольных сумм превышает 4 МиБ.')
            for line in data.decode('utf-8').splitlines():
                match = re.fullmatch(r'([0-9a-fA-F]{64})\s+\*?(.+)', line.strip())
                if match and match[2].removeprefix('./') == asset['name']:
                    matches.add(match[1].lower())
                bsd = re.fullmatch(r'SHA256 \((.+)\) = ([0-9a-fA-F]{64})', line.strip())
                if bsd and bsd[1] == asset['name']:
                    matches.add(bsd[2].lower())
                if re.fullmatch(r'[0-9a-fA-F]{64}', line.strip()) and urlparse(url).path.endswith((asset['name'] + '.sha256', asset['name'] + '.sha256sum')):
                    matches.add(line.strip().lower())
    if len(matches) != 1:
        raise ValueError('Нет однозначной SHA256 для ' + asset['name'])
    return 'sha256:' + matches.pop()
