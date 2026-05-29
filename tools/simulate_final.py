"""
PV Excess Control - End-to-End Simulation Script
This script orchestrates the full simulation by defining the appliances once,
requesting a plan from the Planner module, and executing a real-time minute-by-minute
loop to demonstrate how the Optimizer reacts to both real-time excess and planned schedules.
"""

import sys
import os
from datetime import datetime, timedelta, timezone, time

# Add the project root to sys.path so we can import the custom component modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

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

# Import the Planner Service from the planner simulation script
from simulate_planner import generate_simulation_plan

from utils import (
    export_results_to_csv,
    generate_plotly_dashboard,
    get_price_for_time,
    get_planned_power,
    generate_24h_dataset
)

def run_simulation():
    print("--- Starting PV Excess Control End-to-End Simulation ---\n")

    # 1. SETUP CONFIGURATION
    # -------------------------------------------------------------------------
    # Appliance 1: Washing Machine (Fixed power, 2000W)
    washing_machine_cfg = ApplianceConfig(
        id="washing_machine_1",
        name="Washing Machine",
        entity_id="switch.washing_machine",
        priority=10, # Higher priority (1 is highest)
        phases=1,
        nominal_power=2000.0, # Fixed 2000W when ON
        actual_power_entity=None, # Entity that reports real-time power, if available
        dynamic_current=False, # Not using dynamic current control
        current_entity=None,
        min_current=0.0,
        max_current=0.0,
        ev_soc_entity=None,
        ev_connected_entity=None,
        is_big_consumer=False, # Not a big consumer, so no special battery protection
        battery_max_discharge_override=None,
        on_only=True, # Once turned on, it must run to completion 
        min_daily_runtime=None, # No minimum runtime
        max_daily_runtime=timedelta(hours=2), # Maximum daily runtime in hours
        max_daily_activations=1, # Can only run once per day
        schedule_deadline=None,
        switch_interval=0, # Set to 0 for immediate response in simulation
        allow_grid_supplement=True, # Allowed to use grid if needed
        max_grid_power=None
    )

    # Appliance 2: Crypto Miner (Dynamic power, 1000W to 6000W)
    # Configuration aligned with simulate_miner.py: is_big_consumer + battery discharge blocked
    miner_cfg = ApplianceConfig(
        id="miner_1",
        name="Crypto Miner",
        entity_id="number.miner_power",
        priority=20, # Lower priority
        phases=1, # We assume it is possible to modulate power to a single phase
        nominal_power=1000.0, # Minimum starting power
        actual_power_entity=None, # Entity that reports real-time power, if available
        dynamic_current=True,
        current_entity="number.miner_current",
        min_current=1000.0 / 230.0, # ~4.35A
        max_current=6000.0 / 230.0, # ~26.09A
        ev_soc_entity=None,
        ev_connected_entity=None,
        is_big_consumer=True, # Enables battery discharge protection (Phase 4 of the optimizer)
        battery_max_discharge_override=0.0, # Blocks all battery discharge when the miner is active
        on_only=False,
        min_daily_runtime=None, # Our miner can run any amount of time
        max_daily_runtime=None,
        schedule_deadline=None, # No deadline
        switch_interval=0, # Set to 0 for immediate response
        allow_grid_supplement=False, # Only run when we have excess power
        max_grid_power=None
    )

    # Create a list of appliances and a lookup dictionary by ID
    # Both appliances are included for a complete end-to-end simulation
    appliances = [washing_machine_cfg, miner_cfg]
    config_by_id = {app.id: app for app in appliances}

    # Dataset simulation start time
    # Set to 00:00 UTC to cover a full 24-hour daily cycle
    start_time = datetime(2026, 3, 22, 0, 0, 0, tzinfo=timezone.utc)

    # 2. SETUP BATTERY PHYSICS & CONFIGURATION
    # -------------------------------------------------------------------------
    battery_capacity_kwh = 10.0
    battery_soc = 50.0  # starting at 50%
    max_charge_power = 4000.0
    max_discharge_power = 4000.0
    charging_efficiency = 0.95  # Charging efficiency: only 95% of the input power is stored
    discharging_efficiency = 0.95  # Discharging efficiency: only 95% of stored energy is delivered

    battery_config = BatteryConfig(
        capacity_kwh=battery_capacity_kwh,
        max_discharge_entity=None,
        max_discharge_default=max_discharge_power,
        target_soc=60.0,
        target_time=time(6, 0),
        strategy=BatteryStrategy.APPLIANCE_FIRST,
        allow_grid_charging=True
    )

    # 3. DELEGATE PLANNING TO PLANNER MODULE
    # -------------------------------------------------------------------------
    # We pass our appliances and battery config to the planner to generate a proactive plan
    # based on mock solar forecasts, dynamic tariffs, and battery charging strategy.
    plan, tariff, forecast = generate_simulation_plan(
        appliances=appliances,
        start_time=start_time,
        battery_config=battery_config,
        current_soc=battery_soc
    )

    # Initialize the Optimizer
    optimizer = Optimizer(grid_voltage=230, min_good_samples=1)

    # Dynamic dataset generation: 24 hours at the specified resolution
    # Format: (minute_offset, pv_production_watts, house_load_watts)
    step_minutes = 30
    dataset = generate_24h_dataset(step_minutes=step_minutes)

    # 4. RUN SIMULATION LOOP
    # -------------------------------------------------------------------------
    app_states = {
        washing_machine_cfg.id: {"is_on": False, "current_power": 0.0, "current_amperage": None, "runtime": timedelta(0), "last_reason": ""},
        miner_cfg.id: {"is_on": False, "current_power": 0.0, "current_amperage": None, "runtime": timedelta(0), "last_reason": ""}
    }
    
    power_history = [] # Keep track of recent power states for the optimizer's history-based decisions
    simulation_records = []

    # Power history bootstrap: the optimizer's Phase 1 (ASSESS) computes an averaged
    # excess from the most recent power_history entries. It requires at least
    # 'min_good_samples' valid snapshots (we set min_good_samples=1 above).
    # Without any history at simulation start, the optimizer would follow a
    # safety-only fallback path that skips Phases 2/2.5/3 entirely.
    # To avoid this, we pre-populate power_history with 5 synthetic snapshots
    # representing nighttime conditions (0W PV, 300W house load, 50% battery SoC).
    # These timestamps are placed just before the simulation start so the optimizer
    # can compute a meaningful average from the very first real timestep.
    for i in range(5):
        bootstrap_ps = PowerState(
            pv_production=0.0, grid_export=0.0, grid_import=300.0, load_power=300.0,
            excess_power=-300.0, battery_soc=50.0, battery_power=0.0, ev_soc=None,
            timestamp=start_time - timedelta(minutes=(5-i)*step_minutes)
        )
        power_history.append(bootstrap_ps)
    
    # TODO: Learn more about this variable
    # Closed-loop battery discharge limit tracker.
    # The optimizer's Phase 4 (Battery Discharge Protection) can restrict how much
    # the battery is allowed to discharge. For example, when a big consumer (like
    # the miner with battery_max_discharge_override=0.0) is active, the optimizer
    # returns a BatteryDischargeAction with should_limit=True and max_discharge_watts=0.
    # In a real Home Assistant setup, the coordinator would send this limit to the
    # inverter. Here in the simulation, we store it in this variable and apply it
    # to the battery physics calculation at the NEXT timestep, creating the same
    # closed-loop feedback that would exist in a real system.
    # Initialized to max_discharge_power (no restriction) since no decision has
    # been made yet at simulation start.
    last_max_discharge_watts = max_discharge_power

    header = f"{'Time':<5} | {'Price':<6} | {'PV':<5} | {'Load':<5} | {'Batt%':<5} | {'BattW':<5} | {'GridEx':<6} | {'WM State':<8} | {'Miner State':<15} | {'Decision Reason'}"
    print(header)
    print("-" * len(header))

    # Main simulation loop: iterates over every minute of the 24-hour dataset.
    # For each timestep, this loop:
    #   1. Updates the dynamic tariff price for the current time.
    #   2. Queries the Planner's schedule to record what SHOULD be happening (planned power).
    #   3. Simulates the inverter/battery physics based on PV production vs total load.
    #   4. Builds a PowerState snapshot and ApplianceState list, then calls the Optimizer.
    #   5. Applies the Optimizer's decisions (ON/OFF/SET_CURRENT/IDLE) to update appliance states.
    #   6. Reads the Optimizer's battery discharge protection decision for the next timestep.
    #   7. Updates the battery SoC based on charge/discharge with efficiency losses.
    #   8. Increments runtime counters for active appliances.
    #   9. Records all metrics for CSV export and Plotly dashboard generation.
    for offset, pv, house_load in dataset:
        current_ts = start_time + timedelta(minutes=offset)
        
        # Update dynamic tariff price for the current time step
        tariff.current_price = get_price_for_time(current_ts, tariff.windows)
        
        # Query the Planner's schedule to determine the THEORETICAL power each appliance
        # should be drawing at this exact moment, according to the plan created before
        # the simulation started. This is purely for comparison purposes ("Plan vs Reality"
        # chart in the dashboard). It does NOT influence the optimizer's decisions in any way.
        wm_planned_pow = get_planned_power(washing_machine_cfg, current_ts, plan)
        miner_planned_pow = get_planned_power(miner_cfg, current_ts, plan)
        
        appliances_load = sum(state["current_power"] for state in app_states.values() if state["is_on"])
        current_load = house_load + appliances_load # Total load including active appliances
        
        raw_balance = pv - current_load # Balance considering actual load
        
        # Inverter/battery physics simulation:
        # - Excess power (raw_balance > 0) -> Charge battery up to max_charge_power (with 95% efficiency)
        # - Deficit (raw_balance < 0) -> Discharge battery up to max_discharge_power, constrained
        #   by the optimizer's discharge limit from the previous timestep (with 95% efficiency)
        if raw_balance >= 0:
            battery_power_req = min(raw_balance, max_charge_power)
            
            # Clamp charge power to remaining battery capacity (can't charge past 100%)
            remaining_capacity_wh = (100.0 - battery_soc) / 100.0 * battery_capacity_kwh * 1000.0
            max_charge_by_soc = (remaining_capacity_wh / charging_efficiency) * (60.0 / step_minutes)  # Max watts to fill in step_minutes
            
            battery_power = min(battery_power_req, max_charge_by_soc)
            if battery_power < 0.0:
                battery_power = 0.0
        else:
            # Clamp discharge by the optimizer's protection limit from the previous step
            allowed_discharge = min(max_discharge_power, last_max_discharge_watts)
            battery_power_req = max(raw_balance, -allowed_discharge)
            
            # Clamp discharge by the energy actually available in the battery (can't go below 0%)
            available_capacity_wh = (battery_soc / 100.0) * battery_capacity_kwh * 1000.0
            max_discharge_by_soc = (available_capacity_wh * discharging_efficiency) * (60.0 / step_minutes) # Max watts deliverable in step_minutes
            
            battery_power = max(battery_power_req, -max_discharge_by_soc)
            if battery_power > 0.0:
                battery_power = 0.0

        # Calculate grid interaction after battery allocation
        grid_balance = raw_balance - battery_power 
        grid_export = max(0.0, grid_balance)
        grid_import = max(0.0, -grid_balance)
        excess_power = grid_export - grid_import

        # Create a snapshot of the current power state for the optimizer
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
        history_cap = max(1, 20 // step_minutes)
        if len(power_history) > history_cap: power_history.pop(0) 
        # The optimizer's Phase 1 (ASSESS) computes an averaged excess power over
        # the recent power_history entries to smooth out short-lived spikes and dips.
        # We cap the history based on the step_minutes to keep the averaging window
        # relevant (approximately 20 minutes) and prevent stale data from delaying reactions.

        # Build appliance states for the optimizer input
        ha_app_states = []
        for app in appliances:
            s = app_states[app.id]
            ha_app_states.append(ApplianceState(
                appliance_id=app.id, is_on=s["is_on"], current_power=s["current_power"],
                current_amperage=s["current_amperage"], runtime_today=s["runtime"],
                energy_today=0.0, last_state_change=None, ev_connected=None
            ))

        # Invoke the optimizer engine — this is the core of the library.
        # The optimizer receives the current PowerState snapshot, the list of
        # managed appliances and their current runtime states, the Planner's
        # schedule, the power history window, and the tariff context.
        # Key configuration parameters:
        #   - plan_influence="light": the optimizer uses the Planner's schedule
        #     as a soft hint. When the plan says an appliance should be ON, the
        #     activation threshold is reduced (no hysteresis buffer), making it
        #     easier to turn on. However, if there isn't enough excess power,
        #     the optimizer will NOT force-start the appliance.
        #   - min_battery_soc=20.0: if the battery drops below 20%, Phase 4
        #     sheds all shedable appliances and blocks battery discharge entirely.
        # The optimizer returns an OptimizerResult containing:
        #   - decisions: a list of ControlDecision (one per appliance)
        #   - battery_discharge_action: whether to limit battery discharge
        result = optimizer.optimize(
            power_state=ps, appliances=appliances, appliance_states=ha_app_states,
            plan=plan, power_history=power_history, tariff=tariff,
            plan_influence="light", min_battery_soc=20.0
        )

        # Process decisions and update appliance states
        all_reasons = []
        for decision in result.decisions:
            app_id = decision.appliance_id
            action = decision.action
            reason = decision.reason
            app_states[app_id]["last_reason"] = reason
            all_reasons.append(f"{config_by_id[app_id].name}: {reason}")
            
            if action == Action.ON:
                app_states[app_id]["is_on"] = True
                app_states[app_id]["current_power"] = config_by_id[app_id].nominal_power
                if config_by_id[app_id].dynamic_current:
                    app_states[app_id]["current_amperage"] = config_by_id[app_id].nominal_power / 230.0
            elif action == Action.OFF:
                app_states[app_id]["is_on"] = False
                app_states[app_id]["current_power"] = 0.0
                app_states[app_id]["current_amperage"] = None
            elif action == Action.SET_CURRENT:
                app_states[app_id]["is_on"] = True
                app_states[app_id]["current_amperage"] = decision.target_current
                app_states[app_id]["current_power"] = decision.target_current * 230.0 * config_by_id[app_id].phases
            elif action == Action.IDLE:
                # IDLE means "keep the current state unchanged, take no action".
                # In Home Assistant this is equivalent to not sending any command to the entity.
                # The appliance state (is_on, current_power, etc.) remains as-is.
                pass
        
        # Closed-loop feedback: read the optimizer's battery discharge protection decision
        # and store it for the NEXT timestep's battery physics calculation.
        # When should_limit is True, it means the optimizer detected a condition that
        # requires restricting battery discharge (e.g. a big consumer like the miner is ON
        # and has battery_max_discharge_override=0, or battery SoC dropped below minimum).
        # When should_limit is False, the battery can discharge at full capacity.
        if result.battery_discharge_action.should_limit:
            last_max_discharge_watts = result.battery_discharge_action.max_discharge_watts
        else:
            last_max_discharge_watts = max_discharge_power

        time_str = current_ts.strftime("%H:%M")
        price_str = f"€{tariff.current_price:.2f}"
        wm_state_str = "ON" if app_states["washing_machine_1"]["is_on"] else "OFF"
        m_state = app_states["miner_1"]
        miner_state_str = f"ON({m_state['current_power']:.0f}W)" if m_state["is_on"] else "OFF"
        reasons_str = " | ".join(all_reasons) if all_reasons else "Idle"
        
        # Print log output every 15 minutes or whenever an appliance power state changes
        has_state_changed = False
        if offset > 0:
            prev_record = simulation_records[-1]
            if (prev_record["washing_machine_power"] != app_states["washing_machine_1"]["current_power"] or
                prev_record["miner_power"] != app_states["miner_1"]["current_power"]):
                has_state_changed = True

        if offset % 15 == 0 or has_state_changed:
            print(f"{time_str:<5} | {price_str:<6} | {pv:<5.0f} | {current_load:<5.0f} | {battery_soc:<5.1f} | {battery_power:<5.0f} | {grid_export:<6.0f} | {wm_state_str:<8} | {miner_state_str:<15} | {reasons_str}")

        # Update actual battery SoC based on charge/discharge with efficiency losses
        if battery_power > 0.0:  # Charging
            added_wh = battery_power * charging_efficiency * (step_minutes / 60.0)
            battery_soc = min(100.0, battery_soc + (added_wh / (battery_capacity_kwh * 1000.0)) * 100.0)
        elif battery_power < 0.0:  # Discharging
            removed_wh = (-battery_power / discharging_efficiency) * (step_minutes / 60.0)
            battery_soc = max(0.0, battery_soc - (removed_wh / (battery_capacity_kwh * 1000.0)) * 100.0)

        # Update runtime for active appliances
        for state in app_states.values():
            if state["is_on"]:
                state["runtime"] += timedelta(minutes=step_minutes)

        # Record metrics for dashboard
        simulation_records.append({
            "time": time_str,
            "pv": round(pv, 1),
            "house_load": round(house_load, 1),
            "washing_machine_power": round(app_states["washing_machine_1"]["current_power"], 1),
            "washing_machine_planned_power": round(wm_planned_pow, 1),
            "miner_power": round(app_states["miner_1"]["current_power"], 1),
            "miner_planned_power": round(miner_planned_pow, 1),
            "battery_power": round(battery_power, 1),
            "battery_soc": round(battery_soc, 2),
            "grid_import": round(grid_import, 1),
            "grid_export": round(grid_export, 1),
            "decision": reasons_str
        })

    # Export results and generate dashboard
    export_results_to_csv(simulation_records)
    generate_plotly_dashboard(simulation_records)

if __name__ == "__main__":
    run_simulation()
