#!/usr/bin/env python3
"""
PV Excess Control - Simulation CLI.
Combines JSON configurations from tools/configs/ and CSV datasets from tools/datasets/
to run planning schedules and real-time optimization simulations.

Usage Examples:
    # Run the default mock simulation
    python tools/run_simulation.py --config default_mock --dataset default_mock

    # Run the miner scenario for 2026-05-09
    python tools/run_simulation.py --config miner_config_2026_05_09 --dataset scenario_2026_05_09

    # Run planner-only schedule for 2026-05-10
    python tools/run_simulation.py --config miner_config_2026_05_10 --dataset scenario_2026_05_10 --planner-only
"""

import argparse
import sys
import os

# Add the project root to sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from tools.sim_engine.runner import execute_scenario

def main():
    parser = argparse.ArgumentParser(
        description="PV Excess Control - Simulation CLI"
    )
    parser.add_argument(
        "--config",
        type=str,
        default="default_mock",
        help="Name of the JSON configuration inside tools/configs/ (without the .json extension)"
    )
    parser.add_argument(
        "--dataset",
        type=str,
        default="default_mock",
        help="Name of the CSV profile dataset inside tools/datasets/ (without the .csv extension)"
    )
    parser.add_argument(
        "--planner-only",
        action="store_true",
        help="Only execute the planner schedule phase (skips the minute-by-minute optimizer simulation loop)"
    )
    parser.add_argument(
        "--run-all",
        action="store_true",
        help="Run all combinations of configs and datasets in batch mode"
    )
    
    args = parser.parse_args()
    
    if args.run_all:
        import glob
        # Ensure we look in the correct absolute paths relative to this script
        base_dir = os.path.dirname(os.path.abspath(__file__))
        configs_dir = os.path.join(base_dir, "configs")
        datasets_dir = os.path.join(base_dir, "datasets")
        
        # Extract base names without extensions for both configs and datasets
        configs = [os.path.splitext(os.path.basename(f))[0] for f in glob.glob(os.path.join(configs_dir, "*.json"))]
        datasets = [os.path.splitext(os.path.basename(f))[0] for f in glob.glob(os.path.join(datasets_dir, "*.csv"))]
        
        print(f"--- BATCH MODE ---")
        print(f"Found {len(configs)} configs and {len(datasets)} datasets.")
        
        # Nested loop to iterate over the Cartesian product of (Configs x Datasets)
        for cfg in configs:
            for ds in datasets:
                print(f"\n{'='*80}")
                print(f"Executing Batch Scenario: Config='{cfg}', Dataset='{ds}'")
                print(f"{'='*80}")
                try:
                    execute_scenario(
                        config_name=cfg,
                        dataset_name=ds,
                        planner_only=args.planner_only
                    )
                except Exception as e:
                    # Catch and log exceptions to prevent one failing combination from stopping the entire batch
                    print(f"[ERROR] Batch scenario {cfg} + {ds} failed: {e}", file=sys.stderr)
                    # Continue to the next combination
    else:
        try:
            execute_scenario(
                config_name=args.config,
                dataset_name=args.dataset,
                planner_only=args.planner_only
            )
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"An unexpected error occurred during the simulation: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            sys.exit(1)

if __name__ == "__main__":
    main()
