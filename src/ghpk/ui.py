"""Terminal output and progress reporting."""
import subprocess
import sys
from contextlib import contextmanager
from urllib.error import HTTPError, URLError
from tqdm import tqdm

def message(text, error=False):
    tqdm.write(text, file=sys.stderr if error else sys.stdout)


def progress(description, **kwargs):
    return tqdm(desc=description, dynamic_ncols=True, file=sys.stderr,
                disable=not sys.stderr.isatty(), **kwargs)


@contextmanager
def stage(description):
    with progress(description, total=1, unit="этап", leave=False) as bar:
        yield
        bar.update(1)
    message(description + "… Готово")


def error_text(error):
    if isinstance(error, HTTPError):
        if error.code in (403, 429):
            return f"GitHub: HTTP {error.code}. Проверьте лимит API и переменную GITHUB_TOKEN."
        return f"HTTP {error.code}: {error.reason}"
    if isinstance(error, URLError):
        return "Ошибка соединения: " + str(error.reason)
    if isinstance(error, subprocess.CalledProcessError):
        return f"Команда {error.cmd[0]} завершилась с кодом {error.returncode}."
    if isinstance(error, BlockingIOError):
        return "Не удалось получить блокировку: другой процесс ghpk уже выполняется."
    return str(error)


