"""
PV Excess Control - Inverter and Battery Physics Simulation.
Provides mathematical models for charging/discharging batteries with efficiency losses
and computing real-time grid imports/exports under constraints.
"""

from typing import Tuple

def simulate_battery_physics(
    pv_power: float,
    current_load: float,
    battery_soc: float,
    battery_capacity_kwh: float,
    max_charge_power: float,
    max_discharge_power: float,
    allowed_discharge_limit: float,
    charging_efficiency: float,
    discharging_efficiency: float,
    step_minutes: int,
    min_battery_soc: float = 0.0,
    max_battery_soc: float = 100.0
) -> Tuple[float, float, float, float, float]:
    """
    Simulates the physical battery charging/discharging and computes grid interaction.
    
    Args:
        pv_power: Real-time solar production in Watts.
        current_load: Combined household + running appliances load in Watts.
        battery_soc: Starting battery State of Charge percentage (0.0 to 100.0).
        battery_capacity_kwh: Total battery storage capacity in kWh.
        max_charge_power: Physical inverter limits for charging in Watts.
        max_discharge_power: Physical inverter limits for discharging in Watts.
        allowed_discharge_limit: Closed-loop limit (e.g. from optimizer) restricting discharge in Watts.
        charging_efficiency: Efficiency multiplier for storing power (e.g. 0.95).
        discharging_efficiency: Efficiency multiplier for delivering power (e.g. 0.95).
        step_minutes: Simulation step duration in minutes.
        min_battery_soc: Minimum allowable state of charge in percentage.
        max_battery_soc: Maximum allowable state of charge in percentage.
        
    Returns:
        A tuple of:
        - battery_power: Actual power routed to/from battery in Watts (positive charging, negative discharging).
        - grid_export: Power exported to the utility grid in Watts.
        - grid_import: Power imported from the utility grid in Watts.
        - excess_power: Net power (grid_export - grid_import) in Watts.
        - new_battery_soc: Resulting state of charge in percentage.
    """
    raw_balance = pv_power - current_load
    
    # Inverter/battery physics simulation:
    # - Excess power (raw_balance > 0) -> Charge battery up to max_charge_power (with efficiency)
    # - Deficit (raw_balance < 0) -> Discharge battery, constrained by the inverter limit
    #   and any dynamic limits applied by the optimizer from the previous step.
    if raw_balance >= 0:
        battery_power_req = min(raw_balance, max_charge_power)
        
        # Clamp charge power to remaining battery capacity (cannot charge past max_battery_soc)
        remaining_capacity_wh = max(0.0, (max_battery_soc - battery_soc) / 100.0 * battery_capacity_kwh * 1000.0)
        max_charge_by_soc = (remaining_capacity_wh / charging_efficiency) * (60.0 / step_minutes)
        
        battery_power = min(battery_power_req, max_charge_by_soc)
        if battery_power < 0.0:
            battery_power = 0.0
    else:
        # Clamp discharge by the optimizer's protection limit from the previous step
        allowed_discharge = min(max_discharge_power, allowed_discharge_limit)
        battery_power_req = max(raw_balance, -allowed_discharge)
        
        # Clamp discharge by the energy actually available in the battery (cannot go below min_battery_soc)
        available_capacity_wh = max(0.0, (battery_soc - min_battery_soc) / 100.0 * battery_capacity_kwh * 1000.0)
        max_discharge_by_soc = (available_capacity_wh * discharging_efficiency) * (60.0 / step_minutes)
        
        battery_power = max(battery_power_req, -max_discharge_by_soc)
        if battery_power > 0.0:
            battery_power = 0.0

    # Calculate grid interaction after battery allocation
    grid_balance = raw_balance - battery_power
    grid_export = max(0.0, grid_balance)
    grid_import = max(0.0, -grid_balance)
    excess_power = grid_export - grid_import

    # Update actual battery SoC based on charge/discharge with efficiency losses
    new_battery_soc = battery_soc
    if battery_power > 0.0:  # Charging
        added_wh = battery_power * charging_efficiency * (step_minutes / 60.0)
        new_battery_soc = min(max_battery_soc, battery_soc + (added_wh / (battery_capacity_kwh * 1000.0)) * 100.0)
    elif battery_power < 0.0:  # Discharging
        removed_wh = (-battery_power / discharging_efficiency) * (step_minutes / 60.0)
        new_battery_soc = max(min_battery_soc, battery_soc - (removed_wh / (battery_capacity_kwh * 1000.0)) * 100.0)

    return battery_power, grid_export, grid_import, excess_power, new_battery_soc
