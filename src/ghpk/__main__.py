import sys
try:
    from .cli import main
except ModuleNotFoundError as error:
    if error.name != 'tqdm':
        raise
    print('E: Не найден tqdm. Установите: sudo apt install python3-tqdm', file=sys.stderr)
    sys.exit(1)
sys.exit(main())
