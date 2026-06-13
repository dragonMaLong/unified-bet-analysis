from __future__ import annotations

import argparse
import os
from pathlib import Path

from tristar_bet import export_results_csv, load_file, load_many


DEFAULT_OUT_DIR = Path(r"D:\software\analysis\Data\BET\TriStar_II_3020_format_analysis")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TriStar II 3020 SMP minimal parser")
    parser.add_argument("input", nargs="?", help="SMP file path or directory containing SMP files")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="CSV output directory")
    parser.add_argument("--prefix", default="tristar3020_minimal_parser", help="Output file prefix")
    parser.add_argument("--no-export", action="store_true", help="Only print a CLI summary")
    parser.add_argument("--ui", action="store_true", help="Start the Chinese Qt analysis interface")
    args = parser.parse_args(argv)

    if args.ui or not args.input:
        return _run_ui_or_explain()

    input_path = Path(args.input)
    if input_path.is_dir():
        unique: dict[Path, Path] = {}
        for pattern in ("*.SMP", "*.smp", "*.DAT", "*.dat"):
            for path in input_path.glob(pattern):
                unique.setdefault(path.resolve(), path)
        data_paths = sorted(unique.values())
        results = load_many(data_paths)
    else:
        results = [load_file(input_path)]

    for result in results:
        _print_summary(result)

    if not args.no_export:
        output_paths = export_results_csv(results, args.out_dir, prefix=args.prefix)
        print(f"Exported {len(output_paths)} files to: {Path(args.out_dir).resolve()}")
        for path in output_paths:
            print(f"  {path}")

    return 0


def _run_ui_or_explain() -> int:
    os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt5")
    try:
        from tristar_bet.ui.main_window import run
    except ModuleNotFoundError as exc:
        if exc.name in {"PyQt5", "pyqtgraph", "openpyxl", "numpy"}:
            print("尚未安装图形界面依赖。")
            print("请运行: python -m pip install PyQt5 pyqtgraph numpy openpyxl")
            print("命令行解析仍可使用，例如: python app.py sample.SMP")
            return 2
        raise

    return run()


def _print_summary(result) -> None:
    sample = result.sample
    run = result.run_conditions
    free_space = result.free_space
    print(f"File: {result.header.file_name}")
    print(f"Sample: {result.sample_name}")
    print(f"Operator: {sample.operator}")
    print(f"Mass: {sample.sample_mass_g:.8g} g" if sample.sample_mass_g is not None else "Mass: n/a")
    print(f"Adsorptive: {run.adsorptive_short or run.adsorptive_name}")
    print(f"Bath temperature: {run.bath_temperature_K:.6g} K" if run.bath_temperature_K is not None else "Bath temperature: n/a")
    print(f"Warm free space: {free_space.warm_free_space_cm3:.8g} cm3" if free_space.warm_free_space_cm3 is not None else "Warm free space: n/a")
    print(f"Cold free space: {free_space.cold_free_space_cm3:.8g} cm3" if free_space.cold_free_space_cm3 is not None else "Cold free space: n/a")
    print(f"Isotherm points: {result.point_count}")
    print("")


if __name__ == "__main__":
    raise SystemExit(main())
