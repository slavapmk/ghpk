#!/bin/sh
set -eu
if [ "$(id -u)" -ne 0 ]; then
    echo 'Выполните: sudo sh install.sh' >&2
    exit 1
fi
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
/usr/bin/python3 -c 'import tqdm' || {
    echo 'Установите зависимости: sudo apt install python3 python3-tqdm' >&2
    exit 1
}
install -d /usr/local/lib/ghpk/ghpk /usr/local/bin
for module in "$project_dir"/src/ghpk/*.py; do
    install -m 644 "$module" /usr/local/lib/ghpk/ghpk/
done
install -m 755 "$project_dir/ghpk" /usr/local/bin/ghpk
echo 'ghpk установлен в /usr/local/bin/ghpk'
