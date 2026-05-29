"""
PV Excess Control - Planner Simulation Script
This script provides the planner logic for the simulation, generating
mock forecasts, dynamic tariffs, and producing a complete schedule plan.
"""

import sys
import os
from datetime import datetime, timedelta, time, timezone

# Add the project root to sys.path so we can import the custom component modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from custom_components.pv_excess_control.planner import Planner
from custom_components.pv_excess_control.models import (
    ApplianceConfig, 
    ForecastData, 
    HourlyForecast,
    TariffInfo, 
    TariffWindow,
    BatteryConfig
)

def _generate_mock_forecast(start_time: datetime) -> ForecastData:
    """Generates a 24-hour mock solar forecast starting from start_time."""
    hourly_forecasts = []
    total_kwh = 0.0
    for hour_offset in range(24):
        slot_start = start_time + timedelta(hours=hour_offset)
        slot_end = slot_start + timedelta(hours=1)
        
        hour_of_day = slot_start.hour
        
        # Sun only between 08:00 and 16:00
        if 8 <= hour_of_day <= 16:
            expected_watts = 4000.0  # Good solar day tomorrow
        else:
            expected_watts = 0.0

        hourly_forecasts.append(HourlyForecast(
            start=slot_start,
            end=slot_end,
            expected_kwh=(expected_watts / 1000.0),
            expected_watts=expected_watts
        ))
        total_kwh += (expected_watts / 1000.0)

    return ForecastData(
        remaining_today_kwh=total_kwh,
        hourly_breakdown=hourly_forecasts,
        tomorrow_total_kwh=total_kwh
    )

def _generate_mock_tariffs(start_time: datetime) -> TariffInfo:
    """Generates 24-hour dynamic tariffs (e.g. Tibber) starting from start_time."""
    tariff_windows = []
    cheap_threshold = 0.10
    
    for hour_offset in range(24):
        slot_start = start_time + timedelta(hours=hour_offset)
        slot_end = slot_start + timedelta(hours=1)
        hour_of_day = slot_start.hour
        
        # Night 01:00 - 04:00: Super Cheap (0.05)
        # Evening 18:00 - 22:00: Expensive (0.30)
        # Other times: Normal (0.20)
        if 1 <= hour_of_day <= 4:
            price = 0.05  # Super cheap night!
        elif 18 <= hour_of_day <= 22:
            price = 0.30  # Expensive evening peak
        else:
            price = 0.20  # Normal

        tariff_windows.append(TariffWindow(
            start=slot_start,
            end=slot_end,
            price=price,
            is_cheap=(price <= cheap_threshold)
        ))

    return TariffInfo(
        current_price=tariff_windows[0].price,
        feed_in_tariff=0.08,
        cheap_price_threshold=cheap_threshold,
        battery_charge_price_threshold=0.05,
        windows=tariff_windows
    )

def generate_simulation_plan(
    appliances: list[ApplianceConfig], 
    start_time: datetime,
    battery_config: BatteryConfig | None = None,
    current_soc: float | None = None
):
    """
    Generates a proactive plan for the given appliances using mock forecasts
    and tariffs. Logs the decisions and returns the Plan, TariffInfo, and ForecastData.
    """
    print("--- Starting PV Excess Control Planner Module ---")
    
    # 1. SETUP MOCK FORECAST AND TARIFFS
    forecast = _generate_mock_forecast(start_time)
    tariff = _generate_mock_tariffs(start_time)
    
    # 2. RUN PLANNER
    print("--- Inputs Summary ---")
    print(f"Simulation Start Time: {start_time.strftime('%H:%M')}")
    print("Solar: Tomorrow from 08:00 to 16:00")
    print("Prices: Super cheap between 01:00 and 05:00")
    print("-" * 80)

    planner = Planner(grid_voltage=230, timezone_str="UTC")
    
    # Generate the plan using base_load_watts=500, no export limit
    plan = planner.create_plan(
        forecast=forecast,
        tariff=tariff,
        appliances=appliances,
        battery_config=battery_config,
        current_soc=current_soc,
        export_limit=None,
        base_load_watts=500.0
    )

    # 3. OUTPUT PLAN RESULTS
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
    
    # 4. RETURN DATA TO ORCHESTRATOR
    return plan, tariff, forecast

# Optional execution if the file is run independently
if __name__ == "__main__":
    tz = timezone.utc
    now = datetime.now(tz).replace(minute=0, second=0, microsecond=0)
    
    # EV Charger: Needs 3 hours of charging (min_daily_runtime) by 07:00 tomorrow.
    ev_charger = ApplianceConfig(
        id="ev_charger",
        name="EV Charger",
        entity_id="switch.ev_charger",
        priority=10,
        phases=1,
        nominal_power=3000.0,
        actual_power_entity=None,
        dynamic_current=False,
        current_entity=None,
        min_current=0.0,
        max_current=0.0,
        ev_soc_entity=None,
        ev_connected_entity=None,
        is_big_consumer=False,
        battery_max_discharge_override=None,
        on_only=True,
        min_daily_runtime=timedelta(hours=3),
        max_daily_runtime=None,
        schedule_deadline=time(7, 0), # Deadline 07:00 AM
        switch_interval=300,
        allow_grid_supplement=True, # Allowed to use grid to meet deadline
        max_grid_power=None
    )
    
    generate_simulation_plan([ev_charger], now)
