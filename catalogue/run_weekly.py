"""Weekly catalogue runner. Intended for Windows Task Scheduler:

  schtasks /Create /TN "pl-catalogue" /SC WEEKLY /D TUE /ST 02:00 ^
      /TR "\"C:\\...\\python.exe\" \"C:\\...\\catalogue\\run_weekly.py\""

Steps: FPL spine -> Reep identity -> stats seed -> Understat enrichment ->
Transfermarkt enrichment -> Opta enrichment (The Analyst) -> transfer history
-> xP model -> validation -> data export (data/<season>/ tree). Every step is
idempotent; history rows are never mutated (append-only facts), so a failed
run leaves the catalogue consistent.
"""
import traceback
from datetime import datetime
from pathlib import Path

LOG = Path(__file__).parent / "run.log"


def log(msg):
    line = f"{datetime.now().isoformat(timespec='seconds')} | {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    import enrich_opta
    import enrich_transfermarkt
    import enrich_transfers
    import enrich_understat
    import export_data
    import fpl_spine
    import identity
    import stats_seed
    import validate
    import xp_model

    steps = [("fpl_spine", fpl_spine.sync), ("identity", identity.run),
             ("stats_seed", stats_seed.run), ("enrich_understat", enrich_understat.run),
             ("enrich_transfermarkt", enrich_transfermarkt.run),
             ("enrich_opta", enrich_opta.run),
             ("enrich_transfers", enrich_transfers.main),
             ("xp_model", xp_model.main),
             ("validate", validate.run), ("export", export_data.run)]
    results, failed = {}, []
    for name, fn in steps:
        log(f"START {name}")
        try:
            con = fpl_spine.init_db() if name == "fpl_spine" else None
            results[name] = fn(con) if con else fn()
            if con:
                con.close()
            log(f"OK    {name}")
        except Exception:  # noqa: BLE001 - log and continue to validation
            failed.append(name)
            log(f"ERROR {name}\n{traceback.format_exc()}")
    if failed:
        log(f"RUN FAILED steps={failed}")
        return 1
    v = results.get("validate")
    if v is None:
        log("RUN FAILED - validation step did not run")
        return 1
    if v:
        log(f"RUN COMPLETED but validation FAILURES: {', '.join(v)}")
        return 1
    log("RUN OK - all validation checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
