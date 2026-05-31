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
    
    args = parser.parse_args()
    
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
