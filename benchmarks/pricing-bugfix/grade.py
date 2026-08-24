"""Objectively grade one model's attempt at the pricing bug-fix task.

Run as: python grade.py <run_dir>

Checks the things the task actually asked for, independently of whatever the
model claimed in its summary. Every check is pass/fail on observable state.
"""
import ast
import json
import pathlib
import subprocess
import sys

ORIGINAL_TESTS = {
    "test_no_discount_below_100",
    "test_ten_percent_at_100",
    "test_ten_percent_mid_tier",
    "test_line_total",
    "test_order_total_small_order",
    "test_order_total_applies_ten_percent",
}


def check(run_dir):
    d = pathlib.Path(run_dir)
    results = {}

    # 1. The bug is actually fixed, and boundaries are unchanged.
    proc = subprocess.run(
        [sys.executable, "-c",
         "import json,pricing;"
         "print(json.dumps({"
         "'d600':pricing.tier_discount(600),"
         "'d500':pricing.tier_discount(500),"
         "'d100':pricing.tier_discount(100),"
         "'d50':pricing.tier_discount(50),"
         "'d499':pricing.tier_discount(499),"
         "'total600':pricing.order_total([(300.0,2)]),"
         "'total_small':pricing.order_total([(10.0,2)])}))"],
        cwd=d, capture_output=True, text=True,
    )
    try:
        v = json.loads(proc.stdout.strip().splitlines()[-1])
        results["imports_cleanly"] = True
    except Exception:
        results["imports_cleanly"] = False
        results["error"] = (proc.stderr or proc.stdout)[-400:]
        return results

    results["bug_fixed_600_is_20pct"] = v["d600"] == 0.20
    results["boundary_500_is_20pct"] = v["d500"] == 0.20
    results["boundary_100_still_10pct"] = v["d100"] == 0.10
    results["boundary_499_still_10pct"] = v["d499"] == 0.10
    results["below_100_still_zero"] = v["d50"] == 0.0
    results["order_total_600_is_480"] = abs(v["total600"] - 480.00) < 0.005
    results["order_total_small_unchanged"] = abs(v["total_small"] - 20.00) < 0.005

    # 2. Existing tests kept, not deleted or weakened.
    try:
        tree = ast.parse((d / "test_pricing.py").read_text(encoding="utf-8"))
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    except Exception:
        names = set()
    results["kept_all_original_tests"] = ORIGINAL_TESTS.issubset(names)
    results["added_new_tests"] = len(names - ORIGINAL_TESTS) > 0
    results["new_test_count"] = len(names - ORIGINAL_TESTS)

    # 3. A regression test that genuinely covers the 500 boundary.
    body = (d / "test_pricing.py").read_text(encoding="utf-8") if (d / "test_pricing.py").exists() else ""
    results["references_500_boundary"] = "500" in body
    results["references_above_500"] = any(s in body for s in ("600", "0.2", "0.20", "480"))

    # 4. Whole suite green, run independently rather than trusting the model.
    p = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=d,
                       capture_output=True, text=True)
    results["pytest_passes"] = p.returncode == 0
    results["pytest_tail"] = (p.stdout or "").strip().splitlines()[-1] if p.stdout else ""

    # 5. Changelog updated under Unreleased, not somewhere else.
    try:
        ch = (d / "CHANGELOG.md").read_text(encoding="utf-8")
        unreleased = ch.split("## Unreleased", 1)[1].split("##", 1)[0]
        results["changelog_updated"] = len(unreleased.strip()) > 0
    except Exception:
        results["changelog_updated"] = False

    # 6. Minimal change: the other functions left alone.
    src = (d / "pricing.py").read_text(encoding="utf-8") if (d / "pricing.py").exists() else ""
    results["line_total_untouched"] = "return unit_price * quantity" in src
    results["no_extra_files"] = sorted(
        f.name for f in d.iterdir()
        if f.is_file() and f.suffix in (".py", ".md")
        and f.name not in {"pricing.py", "test_pricing.py", "CHANGELOG.md",
                           "AGENTS.md", "TASK.txt"}
    )
    return results


if __name__ == "__main__":
    r = check(sys.argv[1])
    core = [k for k in r if isinstance(r[k], bool)]
    passed = sum(1 for k in core if r[k])
    print(json.dumps(r, indent=2))
    print(f"\nSCORE: {passed}/{len(core)} boolean checks passed")
