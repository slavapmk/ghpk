"""DEB download, integrity validation and APT installation."""
import hashlib
import re
from pathlib import Path
import subprocess
import tempfile
from .github import request, checksum_from_release
from .ui import message, progress, stage

def architecture():
    return subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()

def installed_version(package):
    result = subprocess.run(["dpkg-query", "-W", "-f=${Status}\n${Version}", package],
                            capture_output=True, text=True)
    lines = result.stdout.splitlines()
    return lines[1] if result.returncode == 0 and len(lines) > 1 and lines[0] == "install ok installed" else None


def update_available(asset, current, previous):
    """Compare a version-like release tag without downloading the DEB."""
    if current is None:
        return None
    if (asset.get("digest") and asset["digest"] == previous.get("digest")
            and current == previous.get("version")):
        return False
    tag = asset.get("tag", "")
    candidate = tag[1:] if tag.startswith("v") else tag
    if not re.fullmatch(r"[0-9][0-9A-Za-z.+:~\-]*", candidate):
        return None
    return subprocess.run(
        ["dpkg", "--compare-versions", candidate, "gt", current],
        check=False,
    ).returncode == 0


def install_asset(entry, asset, arch, state):
    previous = state.get(entry["repo"], {})
    expected_digest = asset.get("digest") or checksum_from_release(asset, entry["repo"])
    if previous.get("digest") == expected_digest and previous.get("package"):
        if installed_version(previous["package"]) == previous.get("version"):
            expected = entry.get("package")
            if expected and expected != previous["package"]:
                raise ValueError("Неожиданное имя пакета: " + previous["package"])
            entry["package"] = previous["package"]
            message(f"{previous['package']} уже имеет последнюю доступную версию ({previous['version']}).")
            return "unchanged"
    with tempfile.TemporaryDirectory(prefix="ghpk-") as temporary:
        folder = Path(temporary)
        deb = folder / "package.deb"
        message(f"Пол: https://github.com/{entry['repo']} {asset['tag']} {asset['name']}")
        with stage("Соединение с сервером загрузки"):
            stream = request(asset["url"])
        with stream, deb.open("wb") as output:
            length = stream.headers.get("Content-Length")
            total = int(length) if length and length.isdigit() else asset.get("size")
            with progress("Получение пакета", total=total, unit="Б", unit_scale=True,
                          unit_divisor=1024) as bar:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    bar.update(len(chunk))
        message(f"Получено: {deb.stat().st_size:,} байт.")
        checksum = hashlib.sha256()
        with deb.open("rb") as source, progress("Проверка SHA256", total=deb.stat().st_size,
                                               unit="Б", unit_scale=True, unit_divisor=1024) as bar:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                checksum.update(chunk)
                bar.update(len(chunk))
        if checksum.hexdigest() != expected_digest.split(":", 1)[1].lower():
            raise ValueError("SHA256 не совпадает; установка остановлена")
        def field(key):
            return subprocess.check_output(["dpkg-deb", "-f", str(deb), key], text=True).strip()
        with stage("Чтение информации о пакете"):
            package, version, package_arch = field("Package"), field("Version"), field("Architecture")
        expected = entry.get("package") or previous.get("package")
        if expected and package != expected:
            raise ValueError("Неожиданное имя пакета: " + package)
        if package_arch not in (arch, "all"):
            raise ValueError("Несовместимая архитектура: " + package_arch)
        current = installed_version(package)
        if current and subprocess.run(["dpkg", "--compare-versions", current, "gt", version]).returncode == 0:
            message(f"{package}: установлена версия {current}, новее доступной {version}. Понижение версии пропущено.")
            return "unchanged"
        if current != version:
            if current:
                message(f"Следующий пакет будет обновлён: {package} ({current} → {version}).")
            else:
                message(f"Следующий НОВЫЙ пакет будет установлен: {package} ({version}).")
            folder.chmod(0o755)
            deb.chmod(0o644)
            message("Передача пакета APT для установки…")
            subprocess.run(["sudo", "apt", "install", str(deb)], check=True)
            with stage("Проверка состояния установленного пакета"):
                if installed_version(package) != version:
                    raise RuntimeError("Установка не подтверждена через dpkg")
        else:
            message(f"{package} уже имеет последнюю доступную версию ({version}).")
        entry["package"] = package
        state[entry["repo"]] = {"package": package, "version": version,
                                 "tag": asset["tag"], "digest": expected_digest}
        return "updated" if current and current != version else "installed" if current != version else "unchanged"


