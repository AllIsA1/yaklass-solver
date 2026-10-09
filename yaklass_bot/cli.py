from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config, default_config_path
from .notify import ConsoleNotifier
from .parser import parse_exercise


def _config_path(args) -> Path:
    return Path(args.config) if args.config else default_config_path()


def _cfg(args) -> Config:
    path = _config_path(args)
    if not path.exists() and sys.stdin.isatty() and sys.stdout.isatty():
        print("Конфиг не найден — запускаю стартовую настройку.\n")
        from .setup_wizard import run_wizard
        return run_wizard(Config.load(path), path)
    return Config.load(path)


def cmd_setup(args) -> int:
    from .setup_wizard import run_wizard
    path = _config_path(args)
    try:
        run_wizard(Config.load(path), path)
    except (KeyboardInterrupt, EOFError):
        print("\nОтменено, настройки не изменены.")
        return 1
    return 0


def cmd_init(args) -> int:
    dst = Path(args.config) if args.config else default_config_path()
    if dst.exists():
        print(f"Уже существует: {dst}")
        return 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    from importlib.resources import files
    dst.write_text((files("yaklass_bot") / "config.example.toml").read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Создан {dst}")
    return 0


def cmd_check(args) -> int:
    from .watcher import check_new_works
    n = check_new_works(_cfg(args), ConsoleNotifier())
    print(f"Новых работ: {n}")
    return 0


def cmd_watch(args) -> int:
    from .watcher import watch
    watch(_cfg(args), ConsoleNotifier())
    return 0


def cmd_solve_file(args) -> int:
    """Оффлайн-проверка: разобрать сохранённый HTML задания и получить ответ от LLM."""
    from .pipeline import solve
    from .solver import make_provider
    from .solver.base import build_prompt

    cfg = _cfg(args)
    task = parse_exercise(Path(args.file).read_text(encoding="utf-8"))
    if task.is_theory:
        print(f"«{task.title}» — страница теории, отвечать не на что (пропуск).")
        return 0
    print(f"Задание {task.position}: полей {len(task.fields)}, картинок {len(task.image_urls)}\n")
    if args.no_search:
        cfg.searxng_url = ""
    if args.prompt_only:
        print(build_prompt(task))
        return 0
    provider = make_provider(cfg)
    try:
        ans = solve(cfg, provider, task)
    finally:
        provider.close()
    print(f"Ответ (уверенность {ans.confidence:.2f}):" + (f"\nОснование: {ans.evidence}" if ans.evidence else ""))
    for f in task.fields:
        v = ans.values[f.name]
        vals = v.split(",") if f.kind == "checkbox" else [v]
        shown = "; ".join(next((o.text or o.image for o in f.options if o.value == x), x) for x in vals)
        print(f"  {f.name}: {shown}")
    return 0


def cmd_run(args) -> int:
    if args.auto == args.dry_run:
        print("Укажите режим: --dry-run (заполнить, но не отправлять) или --auto (выполнить работу самостоятельно: "
              "ответы будут ОТПРАВЛЕНЫ).", file=sys.stderr)
        return 2
    cfg = _cfg(args)
    if args.auto:
        from .autorun import run_auto
        run_auto(cfg, args.url, assume_yes=args.yes, finish_anyway=args.finish_anyway, resume=args.resume,
                 start_delay=args.start_delay)
        return 0
    from .runner import run_dry
    run_dry(cfg, args.url, auto_start=not args.no_start, start_delay=args.start_delay)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="yaklass-bot")
    ap.add_argument("-c", "--config", help="путь к config.toml")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("setup", help="стартовая настройка (браузер, API/аккаунт, vision...)").set_defaults(fn=cmd_setup)
    sub.add_parser("init", help="создать config.toml").set_defaults(fn=cmd_init)
    sub.add_parser("check", help="один раз проверить новые работы").set_defaults(fn=cmd_check)
    sub.add_parser("watch", help="проверять постоянно").set_defaults(fn=cmd_watch)
    p = sub.add_parser("solve-file", help="решить задание из сохранённого HTML (отладка)")
    p.add_argument("file")
    p.add_argument("--no-search", action="store_true", help="не использовать SearXNG, даже если он указан в config.toml")
    p.add_argument("--prompt-only", action="store_true", help="только показать промпт")
    p.set_defaults(fn=cmd_solve_file)
    r = sub.add_parser("run", help="открыть страницу и выставить ответы (отправляет человек)")
    r.add_argument("url", nargs="?", default="/TestWork", help="адрес или путь на yaklass.ru (по умолчанию список работ)")
    r.add_argument("--dry-run", "--test", dest="dry_run", action="store_true",
                   help="тестовый режим: не отправлять ответы")
    r.add_argument("--auto", action="store_true",
                   help="автономный режим: решить, ОТПРАВИТЬ все ответы (выполненные — перезаписать) и завершить работу")
    r.add_argument("--yes", action="store_true", help="--auto: не спрашивать подтверждение")
    r.add_argument("--resume", action="store_true",
                   help="--auto: пропустить задания, уже отправленные ботом в этой попытке (после сбоя)")
    r.add_argument("--finish-anyway", action="store_true",
                   help="--auto: пытаться завершить работу, даже если часть заданий осталась без ответа")
    r.add_argument("--no-start", action="store_true",
                   help="не нажимать «Начать» (по умолчанию нажимает: это тратит попытку и запускает таймер)")
    r.add_argument("--start-delay", type=int, default=5, metavar="СЕК",
                   help="пауза перед «Начать» на случай отмены (по умолчанию 5)")
    r.set_defaults(fn=cmd_run)
    args = ap.parse_args(argv)
    if args.cmd is None:   # `yaklass-bot` без аргументов: первый запуск -> настройка, иначе справка
        if not _config_path(args).exists() and sys.stdin.isatty():
            return cmd_setup(args)
        ap.print_help()
        return 0
    try:
        return args.fn(args)
    except Exception as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
