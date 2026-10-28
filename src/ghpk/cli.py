"""Three commands: install, update and upgrade."""
import fcntl
import os
import sys
from . import github, packages
from .storage import Store, read_json, save_json
from .ui import error_text, message, stage

USAGE = 'Использование:\n  ghpk install REPO [REPO ...]\n  ghpk update\n  ghpk upgrade'


def summary(counts):
    message(f"Обновлено: {counts['updated']}, установлено: {counts['installed']}, без изменений: {counts['unchanged']}, ошибок: {counts['errors']}.")


def run(arguments):
    if not arguments:
        message(USAGE)
        return 0
    command, *targets = arguments
    if command not in ('install', 'update', 'upgrade') or (command == 'install' and not targets) or (command != 'install' and targets):
        message('E: Неверная команда или число аргументов.\n' + USAGE, error=True)
        return 2
    if os.geteuid() == 0:
        raise ValueError('Запускайте ghpk без sudo. Права будут запрошены при установке пакета.')
    # Normalize all inputs before any package installation; preserve order and remove duplicates.
    targets = list(dict.fromkeys(github.normalize_repo(value) for value in targets))
    store = Store()
    with (store.cache / 'lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with stage('Чтение списка репозиториев'):
            config = store.load()
            state = read_json(store.state, {})
        entries = config['repos']
        arch = packages.architecture()
        if command == 'install':
            selected = [next((dict(entry) for entry in entries if entry['repo'] == repo), {'repo': repo}) for repo in targets]
        else:
            selected = [entry for entry in entries if entry.get('enabled', True)]
        if not selected:
            message('Нет настроенных активных репозиториев. Используйте: ghpk install автор/репозиторий')
            return 0
        cache = {} if command == 'update' else read_json(store.releases, {})
        counts = dict(updated=0, installed=0, unchanged=0, errors=0, available=0, unknown=0)
        for index, entry in enumerate(selected, 1):
            repo = entry['repo']
            message(f'[{index}/{len(selected)}] https://github.com/{repo}')
            try:
                if command == 'update':
                    asset = github.latest(entry, arch)
                    cache[repo] = asset
                    package = entry.get('package') or state.get(repo, {}).get('package')
                    version = packages.installed_version(package) if package else None
                    available = packages.update_available(asset, version, state.get(repo, {}))
                    if available is None:
                        counts['unknown'] += 1
                    elif available:
                        counts['available'] += 1
                    message(f"Пол: {repo} — релиз {asset['tag']}; установлено: {version or 'не определено'}.")
                    continue
                asset = github.latest(entry, arch) if command == 'install' else cache.get(repo)
                if not asset or asset.get('architecture') != arch or asset.get('pattern') != entry.get('asset'):
                    raise ValueError('Сведения о релизе отсутствуют или конфигурация изменилась. Выполните ghpk update.')
                if command == 'upgrade':
                    previous = state.get(repo, {})
                    package = entry.get('package') or previous.get('package')
                    version = packages.installed_version(package) if package else None
                    if packages.update_available(asset, version, previous) is False:
                        message(f"{package}: обновление не требуется (установлено: {version}, релиз: {asset['tag']}).")
                        counts['unchanged'] += 1
                        continue
                result = packages.install_asset(entry, asset, arch, state)
                if command == 'install':
                    entry['enabled'] = True
                    entries[:] = [entry if old['repo'] == repo else old for old in entries]
                    if not any(old['repo'] == repo for old in entries):
                        entries.append(entry)
                with stage('Сохранение состояния'):
                    save_json(store.state, state)
                    save_json(store.config, config)
                counts[result] += 1
            except Exception as error:
                counts['errors'] += 1
                message(f'E: {repo}: {error_text(error)}', error=True)
        if command == 'update':
            with stage('Запись сведений о релизах'):
                save_json(store.releases, cache)
            message(f"Обработано репозиториев: {len(selected)}. Успешно: {len(cache)}. Ошибок: {counts['errors']}.")
            message(f"Доступно обновлений: {counts['available']}.")
            if counts['unknown']:
                message(f"Не удалось определить наличие обновления: {counts['unknown']}.")
        else:
            summary(counts)
        return int(counts['errors'] != 0)


def main():
    try:
        return run(sys.argv[1:])
    except KeyboardInterrupt:
        message('E: Операция прервана пользователем.', error=True)
        return 130
    except Exception as error:
        message('E: ' + error_text(error), error=True)
        return 1
