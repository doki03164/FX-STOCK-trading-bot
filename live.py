"""One command to refresh the live tab: fetch quotes -> scan -> rebuild the dashboard.

    python live.py            # refresh bars, scan all three ladders, rebuild
    python live.py --nofetch  # skip the download, just rescan the cached bars

The backtest itself is not re-run — sweep.csv and candidates.parquet are reused, so
this takes about a minute rather than ten. Re-run `python sweep.py` only when you
change the strategy or extend the universe.
"""
import os, sys, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))


def step(name, args):
    print(f"\n{'='*70}\n{name}\n{'='*70}")
    r = subprocess.run([sys.executable, os.path.join(HERE, args[0])] + args[1:])
    if r.returncode != 0:
        print(f"! {args[0]} exited {r.returncode}")
    return r.returncode


def main():
    bot = ["bot.py"] + (["--nofetch"] if "--nofetch" in sys.argv else [])
    bot += [a for a in sys.argv[1:] if a in ("1h", "4h", "1d")]
    if step("1/2  掃描即時盤面", bot):
        return 1
    if step("2/2  重建儀表板", ["report.py"]):
        return 1
    print(f"\n完成 → {os.path.join(HERE, 'dashboard.html')}")
    print("開啟後預設就在「即時訊號」分頁。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
