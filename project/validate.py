"""Check how well the tool's ranking predicts real-world gripper performance.

usage: python validate.py <metrics.json> <measured.csv>

metrics.json  the ranking of one job: what GET /jobs/<job> returns, or the
              jobs/job_<name>/metrics.json that run_local.py writes. Rows are
              [design, pseudo_cgs, load_point_displacement, stress].
measured.csv  one row per physically tested design, header "design,score", where
              design matches the tool's design names (e.g. honey-20-n45deg) and
              score is any real-world measure where HIGHER IS BETTER (grasp success
              rate, pull-out force, objects held, ...). Flip the sign of measures
              where lower is better.

Prints the Spearman rank correlation between each tool metric and the measured
score: +1 means the metric orders the designs exactly like the real tests. With
fewer than ~5 tested designs the correlation is not meaningful.
"""
import csv
import json
import sys

from scipy.stats import spearmanr


def main(metrics_path, measured_path):
    data = json.load(open(metrics_path))
    rows = data["metrics"] if isinstance(data, dict) else data
    tool = {r[0]: {"pseudo CGS": r[1], "displacement": r[2], "-stress": -r[3]} for r in rows}
    measured = {r["design"].strip(): float(r["score"]) for r in csv.DictReader(open(measured_path))}

    designs = [d for d in measured if d in tool]
    missing = sorted(set(measured) - set(tool))
    if missing:
        print(f"not in the tool's results (check the names): {', '.join(missing)}")
    if len(designs) < 3:
        sys.exit(f"need at least 3 designs in both files, found {len(designs)}")

    real = [measured[d] for d in designs]
    print(f"{len(designs)} designs compared")
    for metric in ("pseudo CGS", "displacement", "-stress"):
        rho, p = spearmanr([tool[d][metric] for d in designs], real)
        print(f"  {metric:12s} vs measured: Spearman rho = {rho:+.2f} (p = {p:.2f})")

    order = sorted(designs, key=lambda d: -tool[d]["pseudo CGS"])
    real_rank = {d: i + 1 for i, d in enumerate(sorted(designs, key=lambda d: -measured[d]))}
    print("\n  tool rank  real rank  design")
    for i, d in enumerate(order, 1):
        print(f"  {i:9d}  {real_rank[d]:9d}  {d}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:3])
