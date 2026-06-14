"""
PV Excess Control - Simulation Runner and Orchestrator.
Executes both Planner-Only and full End-to-End simulation runs, 
providing detailed logging and closed-loop coordination with the optimizer.
"""

import sys
import os
from datetime import datetime, timedelta, timezone, time
from typing import List, Dict, Any, Tuple

# Add the project root to sys.path so we can import the custom component modules
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from custom_components.pv_excess_control.planner import Planner
from custom_components.pv_excess_control.optimizer import Optimizer
from custom_components.pv_excess_control.models import (
    PowerState, 
    ApplianceConfig, 
    ApplianceState, 
    Action,
    Plan,
    BatteryConfig,
    BatteryStrategy
)

from sim_engine.loader import load_simulation_config, load_simulation_dataset
from sim_engine.physics import simulate_battery_physics
from sim_engine.plotting import export_results_to_csv, generate_plotly_dashboard, export_summary_to_txt

def get_price_for_time(current_ts: datetime, windows: List[Any]) -> float:
    """Helper to find the dynamic tariff price for a given timestamp."""
    for w in windows:
        if w.start <= current_ts < w.end:
            return w.price
    return windows[0].price if windows else 0.20

def get_planned_power(appliance_cfg: ApplianceConfig, current_ts: datetime, plan: Plan) -> float:
    """
    Returns the theoretical power (in Watts) that the Planner scheduled for
    the given appliance at the given timestamp.
    """
    if plan is None or not plan.entries:
        return 0.0
    for entry in plan.entries:
        if entry.appliance_id == appliance_cfg.id:
            if entry.window.start <= current_ts < entry.window.end:
                if entry.action == Action.ON:
                    return appliance_cfg.nominal_power
                elif entry.action == Action.SET_CURRENT:
                    current = entry.target_current if entry.target_current is not None else (appliance_cfg.nominal_power / 230.0)
                    return current * 230.0 * appliance_cfg.phases
    return 0.0

def get_expected_pv(current_ts: datetime, timeline: List[Any]) -> float:
    for slot in timeline:
        if slot.start <= current_ts < slot.end:
            return slot.expected_solar_watts
    return 0.0

def convert_plan_to_records(
    plan: Plan,
    timeline: List[Any],
    dataset: List[Tuple[datetime, float, float, float]],
    tariff_windows: List[Any],
    appliance_configs: List[ApplianceConfig],
    base_load_watts: float,
    starting_soc: float,
    battery_config: BatteryConfig,
    battery_min_soc: float,
    battery_max_soc: float,
    max_charge_power: float,
    max_discharge_power: float,
    charging_efficiency: float = 0.95,
    discharging_efficiency: float = 0.95
) -> List[Dict[str, Any]]:
    """
    Converts a Planner Timeline and Plan into a list of records for CSV/HTML export.
    Uses the dataset timestamps to provide high-resolution output matching the optimizer.
    """
    records = []
    current_soc = starting_soc
    battery_capacity_kwh = battery_config.capacity_kwh if battery_config else 0.0
    
    if len(dataset) > 1:
        step_hours = (dataset[1][0] - dataset[0][0]).total_seconds() / 3600.0
    else:
        step_hours = 0.25

    for current_ts, _, _, _ in dataset:
        if (current_ts - dataset[0][0]).total_seconds() >= 86400:
            break
            
        duration_hours = step_hours
        expected_pv = get_expected_pv(current_ts, timeline)
        price = get_price_for_time(current_ts, tariff_windows)
        
        record = {
            "time": current_ts.strftime("%Y-%m-%d %H:%M"),
            "pv": round(expected_pv, 1),
            "house_load": round(base_load_watts, 1),
            "price": round(price, 3),
            "decision": "Planned Strategy"
        }
        
        # Determine planned power and check for big consumers with battery protection
        total_appliance_power = 0.0
        big_consumer_active = False
        for app in appliance_configs:
            p_power = get_planned_power(app, current_ts, plan)
            record[f"{app.id}_planned_power"] = round(p_power, 1)
            record[f"{app.id}_power"] = 0.0  # No real execution data
            total_appliance_power += p_power
            # If a big consumer is active and battery override is 0, it means it CANNOT use the battery
            if p_power > 0 and app.is_big_consumer and app.battery_max_discharge_override == 0.0:
                big_consumer_active = True
            
        # Estimate Battery evolution
        raw_balance = expected_pv - (base_load_watts + total_appliance_power)
        
        battery_power = 0.0
        if battery_capacity_kwh > 0:
            if raw_balance > 0:
                # Charge battery
                remaining_capacity_wh = max(0.0, (battery_max_soc - current_soc) / 100.0 * battery_capacity_kwh * 1000.0)
                max_charge_by_soc = (remaining_capacity_wh / charging_efficiency) / duration_hours if duration_hours > 0 else 0.0
                
                battery_power = min(raw_balance, max_charge_power, max_charge_by_soc)
                added_wh = battery_power * charging_efficiency * duration_hours
                current_soc = min(battery_max_soc, current_soc + (added_wh / (battery_capacity_kwh * 1000.0)) * 100.0)
            elif raw_balance < 0:
                # Discharge battery
                allowed_discharge = 0.0 if big_consumer_active else max_discharge_power
                
                needed_wh = abs(raw_balance) * duration_hours
                available_wh = max(0.0, (current_soc - battery_min_soc) / 100.0 * battery_capacity_kwh * 1000.0)
                max_discharge_by_soc = (available_wh * discharging_efficiency) / duration_hours if duration_hours > 0 else 0.0
                
                battery_power_req = max(raw_balance, -allowed_discharge, -max_discharge_by_soc)
                battery_power = battery_power_req
                removed_wh = abs(battery_power) / discharging_efficiency * duration_hours
                current_soc = max(battery_min_soc, current_soc - (removed_wh / (battery_capacity_kwh * 1000.0)) * 100.0)
        
        record["battery_power"] = round(battery_power, 1)
        record["battery_soc"] = round(current_soc, 2)
        
        # Grid estimate
        grid_balance = raw_balance - battery_power
        record["grid_export"] = round(max(0.0, grid_balance), 1)
        record["grid_import"] = round(max(0.0, -grid_balance), 1)
        
        records.append(record)
        
    return records

def execute_scenario(config_name: str, dataset_name: str, planner_only: bool = False):
    """
    Orchestrates the entire simulation run: loads configs and datasets,
    executes the simulation loop (planner or optimizer), prints formatted timelines,
    and writes results to output.
    
    Args:
        config_name: Name of the JSON config inside configs/ (without extension).
        dataset_name: Name of the CSV dataset inside datasets/ (without extension).
        planner_only: If True, halts execution after the planning schedule phase.
    """
    subfolder = f"{config_name}_{dataset_name}"
    
    # 1. LOAD CONFIGURATION AND DATASET
    print(f"--- Loading Scenario Configuration: {config_name} ---")
    config = load_simulation_config(config_name)
    
    bootstrap_minutes = config["simulation"]["bootstrap_minutes"]
    
    appliance_configs = config["appliance_configs"]
    config_by_id = {app.id: app for app in appliance_configs}
    
    battery_config = config["battery_config"]
    battery_capacity_kwh = battery_config.capacity_kwh if battery_config else 0.0
    battery_soc = config["battery_starting_soc"]
    battery_min_soc = config["battery_min_soc"]
    battery_max_soc = config["battery_max_soc"]
    max_charge_power = config["max_charge_power"]
    max_discharge_power = config["max_discharge_power"]
    charging_efficiency = config["charging_efficiency"]
    discharging_efficiency = config["discharging_efficiency"]
    
    print(f"--- Loading Time-series Dataset: {dataset_name} ---")
    dataset, tariff, forecast = load_simulation_dataset(dataset_name)
    
    if not dataset:
        print("Error: Dataset is empty.")
        return
        
    start_time = dataset[0][0]
    
    # Calculate step minutes dynamically from dataset
    if len(dataset) > 1:
        step_minutes = (dataset[1][0] - dataset[0][0]).total_seconds() / 60.0
    else:
        step_minutes = 30.0 # safe fallback
    
    # 2. RUN PLANNER SCHEDULE
    print("--- Starting PV Excess Control Planner Module ---")
    print("--- Inputs Summary ---")
    print(f"Simulation Start Time: {start_time.strftime('%Y-%m-%d %H:%M')}")
    print(f"Solar Forecast Total: {forecast.remaining_today_kwh:.2f} kWh")
    print(f"Tariff Start Price: €{tariff.current_price:.2f}/kWh")
    print("-" * 80)
    
    planner = Planner(grid_voltage=230, timezone_str="UTC")
    
    plan = planner.create_plan(
        forecast=forecast,
        tariff=tariff,
        appliances=appliance_configs,
        battery_config=battery_config,
        current_soc=battery_soc,
        export_limit=None,
        base_load_watts=500.0  # Base load matching the original implementation
    )
    
    # Output planner schedule details
    print(f"\nPLAN CONFIDENCE: {plan.confidence*100:.0f}%")
    print("\n--- Scheduled Timeline ---")
    print(f"{'Appliance':<15} | {'Start':<6} | {'End':<6} | {'Price':<6} | {'Action':<15} | {'Reason'}")
    print("-" * 80)
    for entry in plan.entries:
        app_id = entry.appliance_id
        start_str = entry.window.start.strftime("%H:%M")
        end_str = entry.window.end.strftime("%H:%M")
        price_str = f"€{entry.window.price:.2f}"
        action_str = str(entry.action.name)
        print(f"{app_id:<15} | {start_str:<6} | {end_str:<6} | {price_str:<6} | {action_str:<15} | {entry.reason.name}")
    print("--- End of Planner Module ---\n\n")

    # --- AUTOMATIC PLANNER DATA EXPORT ---
    # Re-retrieve timeline to build records
    timeline = planner.build_timeline(forecast, tariff.windows, base_load_watts=500.0)
    planner_records = convert_plan_to_records(
        plan=plan,
        timeline=timeline,
        dataset=dataset,
        tariff_windows=tariff.windows,
        appliance_configs=appliance_configs,
        base_load_watts=500.0,
        starting_soc=battery_soc,
        battery_config=battery_config,
        battery_min_soc=battery_min_soc,
        battery_max_soc=battery_max_soc,
        max_charge_power=max_charge_power,
        max_discharge_power=max_discharge_power,
        charging_efficiency=charging_efficiency,
        discharging_efficiency=discharging_efficiency
    )
    export_results_to_csv(planner_records, "planner_result.csv", output_subfolder=subfolder)
    generate_plotly_dashboard(planner_records, appliance_configs, "planner_result.html", is_planner_only=True, output_subfolder=subfolder)
    # -------------------------------------
    
    if planner_only:
        print("Planner-Only execution completed successfully.")
        return
        
    # 3. RUN MINUTE-BY-MINUTE SIMULATION LOOP WITH OPTIMIZER
    print("--- Starting PV Excess Control End-to-End Simulation ---\n")
    optimizer = Optimizer(grid_voltage=230, min_good_samples=1)
    
    # Initialize appliance runtime tracking
    app_states = {}
    for app in appliance_configs:
        app_states[app.id] = {
            "is_on": False,
            "current_power": 0.0,
            "current_amperage": None,
            "runtime": timedelta(0),
            "last_reason": ""
        }
        
    power_history = []
    simulation_records = []
    
    # Power history bootstrap: the optimizer's Phase 1 (ASSESS) computes an averaged
    # excess from the most recent power_history entries. It requires at least
    # 'min_good_samples' valid snapshots (we set min_good_samples=1 above).
    # Without any history at simulation start, the optimizer would follow a
    # safety-only fallback path that skips Phases 2/2.5/3 entirely.
    # To avoid this, we pre-populate power_history with synthetic snapshots
    # representing nighttime conditions (0W PV, 300W house load, starting battery SoC).
    # These timestamps are placed just before the simulation start so the optimizer
    # can compute a meaningful average from the very first real timestep.
    bootstrap_steps = int(bootstrap_minutes / step_minutes)
    for i in range(bootstrap_steps):
        bootstrap_ts = start_time - timedelta(seconds=int((bootstrap_steps - i) * step_minutes * 60))
        bootstrap_ps = PowerState(
            pv_production=0.0,
            grid_export=0.0,
            grid_import=300.0,
            load_power=300.0,
            excess_power=-300.0,
            battery_soc=battery_soc,
            battery_power=0.0,
            ev_soc=None,
            timestamp=bootstrap_ts
        )
        power_history.append(bootstrap_ps)
        
    # Closed-loop battery discharge limit tracker.
    # The optimizer's Phase 4 (Battery Discharge Protection) can restrict how much
    # the battery is allowed to discharge. For example, when a big consumer is active,
    # the optimizer returns a BatteryDischargeAction with should_limit=True.
    # In a real Home Assistant setup, the coordinator sends this limit to the inverter.
    # Here in the simulation, we store it in this variable and apply it to the battery
    # physics calculation at the NEXT timestep, creating the closed-loop feedback loop.
    last_max_discharge_watts = max_discharge_power
    

    # Print table header for CLI logs (Dynamic to support configured appliances)
    header_parts = ["Time", "Price", "PV", "Load", "Batt%", "BattW", "GridEx"]
    for app in appliance_configs:
        header_parts.append(f"{app.name:<12}")
    header_parts.append("Decision Reason")
    header_str = " | ".join(header_parts)
    print(header_str)
    print("-" * len(header_str))
    
    # Main simulation loop over CSV dataset profile
    for current_ts, pv, house_load, feed_in in dataset:
        if (current_ts - dataset[0][0]).total_seconds() >= 86400: # 1 day max
            break
        
        # Update dynamic tariff price and feed-in for the current time step
        tariff.current_price = get_price_for_time(current_ts, tariff.windows)
        tariff.feed_in_tariff = feed_in
        
        # Query Planner schedule for target appliance power outputs (for visualization comparisons only)
        planned_powers = {}
        for app in appliance_configs:
            planned_powers[app.id] = get_planned_power(app, current_ts, plan)
            
        # Calculate combined current load
        appliances_load = sum(state["current_power"] for state in app_states.values() if state["is_on"])
        current_load = house_load + appliances_load
        
        # Inverter and Battery physical model run
        battery_power, grid_export, grid_import, excess_power, battery_soc = simulate_battery_physics(
            pv_power=pv,
            current_load=current_load,
            battery_soc=battery_soc,
            battery_capacity_kwh=battery_capacity_kwh,
            max_charge_power=max_charge_power,
            max_discharge_power=max_discharge_power,
            allowed_discharge_limit=last_max_discharge_watts,
            charging_efficiency=charging_efficiency,
            discharging_efficiency=discharging_efficiency,
            step_minutes=step_minutes,
            min_battery_soc=battery_min_soc,
            max_battery_soc=battery_max_soc
        )
        
        # Record power state snapshot
        ps = PowerState(
            pv_production=float(pv),
            grid_export=float(grid_export),
            grid_import=float(grid_import),
            load_power=float(current_load),
            excess_power=float(excess_power),
            battery_soc=float(battery_soc),
            battery_power=float(battery_power),
            ev_soc=None,
            timestamp=current_ts
        )
        power_history.append(ps)
        
        # Cap the history based on the step_minutes to keep the averaging window
        # relevant (approximately 20 minutes) and prevent stale data from delaying reactions.
        history_cap = max(1, int(20 / step_minutes))
        if len(power_history) > history_cap:
            power_history.pop(0)
            
        # Map current runtime states to Optimizer inputs
        ha_app_states = []
        for app in appliance_configs:
            s = app_states[app.id]
            ha_app_states.append(ApplianceState(
                appliance_id=app.id,
                is_on=s["is_on"],
                current_power=s["current_power"],
                current_amperage=s["current_amperage"],
                runtime_today=s["runtime"],
                energy_today=0.0,
                last_state_change=None,
                ev_connected=None
            ))
            
        # Run Optimizer
        result = optimizer.optimize(
            power_state=ps,
            appliances=appliance_configs,
            appliance_states=ha_app_states,
            plan=plan,
            power_history=power_history,
            tariff=tariff,
            plan_influence="light",
            min_battery_soc=battery_min_soc
        )
        
        # Apply decisions to update appliance state machines
        all_reasons = []
        for decision in result.decisions:
            app_id = decision.appliance_id
            action = decision.action
            reason = decision.reason
            app_states[app_id]["last_reason"] = reason
            all_reasons.append(f"{config_by_id[app_id].name}: {reason}")
            
            if action == Action.ON:
                if not app_states[app_id]["is_on"]:
                    app_states[app_id]["is_on"] = True
                    if config_by_id[app_id].dynamic_current and decision.target_current is not None:
                        app_states[app_id]["current_amperage"] = decision.target_current
                        app_states[app_id]["current_power"] = decision.target_current * 230.0 * config_by_id[app_id].phases
                    else:
                        app_states[app_id]["current_power"] = config_by_id[app_id].nominal_power
                        if config_by_id[app_id].dynamic_current:
                            app_states[app_id]["current_amperage"] = config_by_id[app_id].nominal_power / 230.0
                # If already ON, Action.ON (staying on) maintains the current_power/amperage
            elif action == Action.OFF:
                app_states[app_id]["is_on"] = False
                app_states[app_id]["current_power"] = 0.0
                app_states[app_id]["current_amperage"] = None
            elif action == Action.SET_CURRENT:
                app_states[app_id]["is_on"] = True
                app_states[app_id]["current_amperage"] = decision.target_current
                app_states[app_id]["current_power"] = decision.target_current * 230.0 * config_by_id[app_id].phases
            elif action == Action.IDLE:
                # IDLE does not change the state of the device
                pass
                
        # Store feedback battery limits for next iteration
        if result.battery_discharge_action.should_limit:
            last_max_discharge_watts = result.battery_discharge_action.max_discharge_watts
        else:
            last_max_discharge_watts = max_discharge_power
            
        # Format print row
        time_str = current_ts.strftime("%H:%M")
        price_str = f"€{tariff.current_price:.2f}"
        
        app_log_states = []
        for app in appliance_configs:
            s = app_states[app.id]
            if s["is_on"]:
                app_log_states.append(f"ON({s['current_power']:.0f}W)")
            else:
                app_log_states.append("OFF")
                
        reasons_str = " | ".join(all_reasons) if all_reasons else "Idle"
        
        # Check if any appliance state changed to force log output
        has_state_changed = False
        if len(simulation_records) > 0:
            prev_record = simulation_records[-1]
            for app in appliance_configs:
                power_key = f"{app.id}_power"
                if prev_record.get(power_key) != app_states[app.id]["current_power"]:
                    has_state_changed = True
                    break
                    
        # Log to stdout at 15-minute intervals or on state changes
        if (current_ts.minute % 15 == 0 and current_ts.second == 0) or has_state_changed:
            row_parts = [
                time_str,
                price_str,
                f"{pv:<5.0f}",
                f"{current_load:<5.0f}",
                f"{battery_soc:<5.1f}",
                f"{battery_power:<5.0f}",
                f"{grid_export:<6.0f}"
            ]
            for state_str in app_log_states:
                row_parts.append(f"{state_str:<12}")
            row_parts.append(reasons_str)
            print(" | ".join(row_parts))
            
        # Update runtimes
        for state in app_states.values():
            if state["is_on"]:
                state["runtime"] += timedelta(minutes=step_minutes)
                
        # Save record for CSV and Plotly export
        record = {
            "time": current_ts.strftime("%Y-%m-%d %H:%M"),
            "pv": round(pv, 1),
            "house_load": round(house_load, 1),
            "battery_power": round(battery_power, 1),
            "battery_soc": round(battery_soc, 2),
            "grid_import": round(grid_import, 1),
            "grid_export": round(grid_export, 1),
            "decision": reasons_str
        }
        for app in appliance_configs:
            record[f"{app.id}_power"] = round(app_states[app.id]["current_power"], 1)
            record[f"{app.id}_planned_power"] = round(planned_powers[app.id], 1)
            
        simulation_records.append(record)
        
    # 4. EXPORT OUTPUT REPORTING FILES
    export_results_to_csv(simulation_records, "optimization_result.csv", output_subfolder=subfolder)
    generate_plotly_dashboard(simulation_records, appliance_configs, "optimization_result.html", output_subfolder=subfolder)

    # 5. GENERATE TEXTUAL SUMMARY
    summary_lines = []
    summary_lines.append("\n" + "="*60)
    summary_lines.append("                SIMULATION DAILY SUMMARY")
    summary_lines.append("="*60)
    
    total_pv_wh = 0.0
    total_import_wh = 0.0
    total_export_wh = 0.0
    total_house_load_wh = 0.0
    appliance_totals_wh = {app.id: 0.0 for app in appliance_configs}
    
    for rec in simulation_records:
        total_pv_wh += rec.get("pv", 0.0) * (step_minutes / 60.0)
        total_import_wh += rec.get("grid_import", 0.0) * (step_minutes / 60.0)
        total_export_wh += rec.get("grid_export", 0.0) * (step_minutes / 60.0)
        total_house_load_wh += rec.get("house_load", 0.0) * (step_minutes / 60.0)
        for app in appliance_configs:
            appliance_totals_wh[app.id] += rec.get(f"{app.id}_power", 0.0) * (step_minutes / 60.0)
            
    total_consumption_wh = total_house_load_wh + sum(appliance_totals_wh.values())
    
    if total_consumption_wh > 0:
        self_sufficiency = max(0.0, (total_consumption_wh - total_import_wh) / total_consumption_wh * 100.0)
    else:
        self_sufficiency = 100.0
        
    summary_lines.append(f" Total PV Production:        {total_pv_wh / 1000.0:>8.2f} kWh")
    summary_lines.append(f" Total Grid Import:          {total_import_wh / 1000.0:>8.2f} kWh")
    summary_lines.append(f" Total Grid Export:          {total_export_wh / 1000.0:>8.2f} kWh")
    summary_lines.append(f" Total Consumption:          {total_consumption_wh / 1000.0:>8.2f} kWh")
    summary_lines.append("-" * 60)
    summary_lines.append(" Appliance Breakdown:")
    for app in appliance_configs:
        summary_lines.append(f"   - {app.name:<21} {appliance_totals_wh[app.id] / 1000.0:>8.2f} kWh")
    summary_lines.append("-" * 60)
    final_soc = simulation_records[-1]["battery_soc"] if simulation_records else battery_soc
    summary_lines.append(f" Final Battery SoC:          {final_soc:>8.1f} %")
    summary_lines.append(f" Self-Sufficiency:           {self_sufficiency:>8.1f} %")
    summary_lines.append("="*60 + "\n")
    
    summary_text = "\n".join(summary_lines)
    print(summary_text)
    
    export_summary_to_txt(summary_text, "summary.txt", output_subfolder=subfolder)
