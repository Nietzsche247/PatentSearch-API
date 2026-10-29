"""manage.py extract <status|spend|quality|selfcheck|worker|enable|disable|kill|resume>: the on-demand extraction trial for
the operator (structure layer gate S1.1). The commands are the patentref clone's lapse.extract (cli.py), run with the trial's folder and key file
from the settings (LAPSE_EXTRACT_DIR, LAPSE_EXTRACT_KEY_FILE) and, for the worker, the long-text reader of
API/lapse_extract.py.

  status                         the gate and why, the key file there or not (never its value), the worker's heartbeat,
                                 jobs, results, spend, cap, what is left, the quality record
  spend [--days N]               the daily spend totals
  quality --fake [--out DIR]     the 20-patent quality check on the local stand-in provider (spends nothing)
  quality --live [--models a,b] [--max-spend USD] [--record]
                                 the quality check on the provider in config.json (spends at most --max-spend, default 1 USD)
  selfcheck                      the switch-on step's checks in a scratch folder (spends nothing)
  worker                         the worker's loop (patentref-extract.service runs this)
  enable [--model M]             config.json on with the model the quality check recorded (refused without the key file and
                                 a pass for that model and the prompt in service); the switch-on step runs it
  disable                        config.json off (enabled false): no new job; results already made are still served
  kill, resume                   make or take away the KILL file
"""
import contextlib

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "The on-demand extraction trial: status, spend, quality, selfcheck, worker"

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["status", "spend", "quality", "selfcheck", "worker", "enable", "disable", "kill",
                                               "resume"])
        parser.add_argument("--model", default="")
        parser.add_argument("--days", type=int, default=14)
        parser.add_argument("--fake", action="store_true")
        parser.add_argument("--live", action="store_true")
        parser.add_argument("--models", default="")
        parser.add_argument("--max-spend", type=float, default=1.0)
        parser.add_argument("--record", action="store_true")
        parser.add_argument("--out", default="")

    def handle(self, *args, **o):
        from API import lapse_extract

        eng = lapse_extract.engine_module()
        if eng is None:
            raise CommandError("the extraction engine (lapse.extract in the patentref clone) is not importable here")
        argv = [o["action"], "--dir", str(settings.LAPSE_EXTRACT_DIR), "--key-file", str(settings.LAPSE_EXTRACT_KEY_FILE),
                "--days", str(o["days"]), "--max-spend", str(o["max_spend"])]
        for flag in ("fake", "live", "record"):
            if o[flag]:
                argv.append(f"--{flag}")
        if o["models"]:
            argv += ["--models", o["models"]]
        if o["out"]:
            argv += ["--out", o["out"]]
        if o["model"]:
            argv += ["--model", o["model"]]
        out = self.stdout

        class _Out:   # the engine prints; the command's own stdout gets it (call_command(..., stdout=) in the suites)
            def write(self, s):
                out.write(s, ending="")

            def flush(self):
                out.flush()

        with contextlib.redirect_stdout(_Out()):
            rc = eng.cli.main(argv, read_patent=lapse_extract.read_for_worker if o["action"] == "worker" else None)
        if rc:
            raise SystemExit(rc)
