"""CLI: python -m clipbot <comando>"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from .config import Config
from .models import parse_ts, utcnow
from .pipeline import Pipeline

log = logging.getLogger("clipbot")


def run_cycle(p: Pipeline, dry_run: bool = False) -> bool:
    """Um ciclo discover + process. Nunca levanta exceção: o bot 24/7 não pode morrer por
    um erro pontual (API fora do ar, disco, banco ocupado). As duas etapas são
    independentes: se a busca falhar, os aprovados ainda são publicados."""
    ok = True
    try:
        p.discover()
    except Exception:
        log.exception("ciclo: a busca falhou; tentando de novo no próximo ciclo")
        ok = False
    try:
        for res in p.process(dry_run=dry_run):
            log.info("process: %s", res)
    except Exception:
        log.exception("ciclo: a publicação falhou; tentando de novo no próximo ciclo")
        ok = False
    return ok


def run_loop(p: Pipeline, interval_min: int, once: bool = False, dry_run: bool = False,
             sleep=time.sleep) -> int:
    failures = 0
    while True:
        failures = 0 if run_cycle(p, dry_run) else failures + 1
        if once:
            return 1 if failures else 0
        if failures >= 3:
            log.error("ATENÇÃO: %d ciclos seguidos com erro. Verifique credenciais e conexão.", failures)
        sleep(interval_min * 60)


def cmd_queue(p: Pipeline, args) -> None:
    rows = p.db.by_status(args.status, limit=args.limit)
    if not rows:
        print(f"Nenhum clipe com status '{args.status}'.")
        return
    now = utcnow()
    print(f"{'score':>9}  {'views':>7}  {'dur':>4}  {'idade':>6}  {'uid':<45} streamer / título")
    for r in rows:
        age_h = (now - parse_ts(r["created_at"])).total_seconds() / 3600
        print(f"{r['score']:>9.1f}  {r['views']:>7}  {r['duration']:>4.0f}  {age_h:>5.1f}h  {r['uid']:<45} "
              f"{r['broadcaster']} / {(r['title'] or '')[:50]}")
        print(f"{'':>34}{r['url']}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="clipbot", description="Bot de clipes GTA VI -> Shorts/TikTok")
    ap.add_argument("--config", default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("discover", help="busca e ranqueia clipes novos")
    q = sub.add_parser("queue", help="lista a fila")
    q.add_argument("--status", default="discovered")
    q.add_argument("--limit", type=int, default=20)
    a = sub.add_parser("approve", help="aprova clipes para publicação")
    a.add_argument("uids", nargs="*")
    a.add_argument("--top", type=int, default=0, help="aprova os N melhores da fila")
    a.add_argument("--force", action="store_true", help="ignora limite diário por streamer")
    r = sub.add_parser("reject", help="rejeita clipes")
    r.add_argument("uids", nargs="+")
    pr = sub.add_parser("process", help="baixa, renderiza e publica aprovados")
    pr.add_argument("--dry-run", action="store_true", help="só renderiza, não publica")
    pr.add_argument("--max", type=int, default=1)
    run = sub.add_parser("run", help="loop contínuo: discover + process")
    run.add_argument("--interval", type=int, default=15, help="minutos entre ciclos")
    run.add_argument("--once", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    sub.add_parser("stats", help="contagem por status e uploads em 24h")
    ex = sub.add_parser("export", help="exporta uploads para CSV (Power BI/Excel)")
    ex.add_argument("--out", default="data/uploads.csv")

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = Pipeline(Config.load(args.config))

    if args.cmd == "discover":
        print(json.dumps(p.discover(), ensure_ascii=False, indent=2))
    elif args.cmd == "queue":
        cmd_queue(p, args)
    elif args.cmd == "approve":
        n = p.approve_top(args.top) if args.top else sum(p.approve(u, args.force) for u in args.uids)
        print(f"{n} clipe(s) aprovado(s).")
    elif args.cmd == "reject":
        for u in args.uids:
            p.reject(u)
        print(f"{len(args.uids)} clipe(s) rejeitado(s).")
    elif args.cmd == "process":
        print(json.dumps(p.process(dry_run=args.dry_run, max_items=args.max), ensure_ascii=False, indent=2))
    elif args.cmd == "run":
        try:
            sys.exit(run_loop(p, args.interval, args.once, args.dry_run))
        except KeyboardInterrupt:
            print("\nEncerrado pelo usuário.")
    elif args.cmd == "stats":
        caps = {t: p.db.capacity(t, p.cfg["publishing"]["daily_limit"][t]) for t in p.cfg.enabled_targets}
        print(json.dumps({"por_status": p.db.stats(), "slots_restantes_24h": caps}, indent=2, ensure_ascii=False))
    elif args.cmd == "export":
        print(f"{p.db.export_csv(args.out)} linha(s) exportada(s) para {args.out}")


if __name__ == "__main__":
    main()
