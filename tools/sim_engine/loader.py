"""
PV Excess Control - Simulation Loader Module.
Provides functions to load appliance configurations, battery properties, 
and time-series data from config JSONs and profile CSVs.
"""

import json
import os
import pathlib
import pandas as pd
from datetime import datetime, timedelta, time, timezone
from typing import Tuple, List, Dict, Any

from custom_components.pv_excess_control.models import (
    ApplianceConfig,
    BatteryConfig,
    TariffInfo,
    TariffWindow,
    ForecastData,
    HourlyForecast
)
from custom_components.pv_excess_control.const import BatteryStrategy

def parse_duration(val: str | None) -> timedelta | None:
    """
    Helper to convert a duration string 'HH:MM:SS' to a timedelta object.
    Returns None if the input is None.
    """
    if val is None:
        return None
    try:
        parts = val.split(":")
        if len(parts) == 3:
            return timedelta(hours=int(parts[0]), minutes=int(parts[1]), seconds=int(parts[2]))
        elif len(parts) == 2:
            return timedelta(minutes=int(parts[0]), seconds=int(parts[1]))
    except (ValueError, TypeError):
        pass
    return None

def parse_time(val: str | None) -> time | None:
    """
    Helper to convert a time string 'HH:MM:SS' or 'HH:MM' to a time object.
    Returns None if the input is None.
    """
    if val is None:
        return None
    try:
        return time.fromisoformat(val)
    except (ValueError, TypeError):
        pass
    return None

def load_simulation_config(config_name: str) -> Dict[str, Any]:
    """
    Loads a JSON configuration file from the configs directory.
    
    Args:
        config_name: Name of the config file without extension.
        
    Returns:
        A dictionary containing parsed configuration fields.
    """
    config_dir = pathlib.Path(__file__).parent.parent / "configs"
    config_file = config_dir / f"{config_name}.json"
    
    if not config_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_file}")
        
    with open(config_file, "r") as f:
        config_data = json.load(f)
        
    # Standardize simulation section
    sim_sec = config_data.setdefault("simulation", {})
    if "start_time" in sim_sec:
        # Convert start time string to timezone-aware UTC datetime
        dt_str = sim_sec["start_time"].replace("Z", "+00:00")
        sim_sec["start_time"] = datetime.fromisoformat(dt_str)
    else:
        sim_sec["start_time"] = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        
    sim_sec.setdefault("step_minutes", 30)
    sim_sec.setdefault("bootstrap_minutes", 150)
    
    # Standardize battery section if exists
    bat_sec = config_data.get("battery")
    if bat_sec:
        strategy_str = bat_sec.get("strategy", "APPLIANCE_FIRST")
        # Match enum key case or raw string
        try:
            strategy_enum = BatteryStrategy(strategy_str.lower())
        except ValueError:
            strategy_enum = BatteryStrategy.APPLIANCE_FIRST
            
        config_data["battery_config"] = BatteryConfig(
            capacity_kwh=float(bat_sec["capacity_kwh"]),
            max_discharge_entity=bat_sec.get("max_discharge_entity"),
            max_discharge_default=float(bat_sec["max_discharge_default"]) if bat_sec.get("max_discharge_default") is not None else None,
            target_soc=float(bat_sec["target_soc"]),
            target_time=parse_time(bat_sec["target_time"]) or time(6, 0),
            strategy=strategy_enum,
            allow_grid_charging=bool(bat_sec.get("allow_grid_charging", True))
        )
        config_data["battery_starting_soc"] = float(bat_sec.get("starting_soc", 50.0))
        config_data["battery_min_soc"] = float(bat_sec.get("min_soc", 20.0))
        config_data["battery_max_soc"] = float(bat_sec.get("max_soc", 100.0))
        config_data["max_charge_power"] = float(bat_sec.get("max_charge_power", 4000.0))
        config_data["max_discharge_power"] = float(bat_sec.get("max_discharge_power", 4000.0))
        config_data["charging_efficiency"] = float(bat_sec.get("charging_efficiency", 0.95))
        config_data["discharging_efficiency"] = float(bat_sec.get("discharging_efficiency", 0.95))
    else:
        config_data["battery_config"] = None
        config_data["battery_starting_soc"] = 50.0
        config_data["battery_min_soc"] = 20.0
        config_data["battery_max_soc"] = 100.0
        config_data["max_charge_power"] = 4000.0
        config_data["max_discharge_power"] = 4000.0
        config_data["charging_efficiency"] = 0.95
        config_data["discharging_efficiency"] = 0.95
        
    # Parse appliances list
    app_list = []
    for app_data in config_data.get("appliances", []):
        app_list.append(ApplianceConfig(
            id=app_data["id"],
            name=app_data["name"],
            entity_id=app_data["entity_id"],
            priority=int(app_data["priority"]),
            phases=int(app_data["phases"]),
            nominal_power=float(app_data["nominal_power"]),
            actual_power_entity=app_data.get("actual_power_entity"),
            dynamic_current=bool(app_data.get("dynamic_current", False)),
            current_entity=app_data.get("current_entity"),
            min_current=float(app_data.get("min_current", 0.0)),
            max_current=float(app_data.get("max_current", 0.0)),
            ev_soc_entity=app_data.get("ev_soc_entity"),
            ev_connected_entity=app_data.get("ev_connected_entity"),
            is_big_consumer=bool(app_data.get("is_big_consumer", False)),
            battery_max_discharge_override=float(app_data["battery_max_discharge_override"]) if app_data.get("battery_max_discharge_override") is not None else None,
            on_only=bool(app_data.get("on_only", True)),
            min_daily_runtime=parse_duration(app_data.get("min_daily_runtime")),
            max_daily_runtime=parse_duration(app_data.get("max_daily_runtime")),
            max_daily_activations=int(app_data["max_daily_activations"]) if app_data.get("max_daily_activations") is not None else None,
            schedule_deadline=parse_time(app_data.get("schedule_deadline")),
            switch_interval=int(app_data.get("switch_interval", 0)),
            allow_grid_supplement=bool(app_data.get("allow_grid_supplement", True)),
            max_grid_power=float(app_data["max_grid_power"]) if app_data.get("max_grid_power") is not None else None
        ))
        
    config_data["appliance_configs"] = app_list
    return config_data

def load_simulation_dataset(
    dataset_name: str, 
    start_time: datetime, 
    step_minutes: int
) -> Tuple[List[Tuple[int, float, float, float]], TariffInfo, ForecastData]:
    """
    Loads and parses a time-series CSV file from the datasets directory,
    re-constructing physical simulation timelines, dynamic tariffs, and solar forecasts.
    
    Args:
        dataset_name: Name of the CSV file without extension.
        start_time: Datetime representing the start of the simulation.
        step_minutes: Interval in minutes between dataset steps.
        
    Returns:
        A tuple containing:
        - List of (minute_offset, pv_power, house_load, feed_in_tariff) for the core loop steps.
        - TariffInfo representing dynamic tariff windows.
        - ForecastData representing solar expectations for the planner.
    """
    dataset_dir = pathlib.Path(__file__).parent.parent / "datasets"
    dataset_file = dataset_dir / f"{dataset_name}.csv"
    
    if not dataset_file.exists():
        raise FileNotFoundError(f"Dataset file not found: {dataset_file}")
        
    df = pd.read_csv(dataset_file)
    
    # Verify required columns
    required_cols = {"minute_offset", "pv_power", "house_load"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Dataset CSV is missing columns: {missing}")
        
    # Parse run steps: (minute_offset, pv, load, feed_in)
    run_steps = []
    for _, row in df.iterrows():
        run_steps.append((
            int(row["minute_offset"]),
            float(row["pv_power"]),
            float(row["house_load"]),
            float(row.get("prod_price", row.get("feed_in_tariff", 0.08)))
        ))
        
    # --- Generate dynamic TariffInfo ---
    cheap_threshold = 0.10
    feed_in = 0.08
    battery_charge_threshold = 0.05
    
    # Parse hourly tariff windows
    tariff_windows = []
    # If the CSV has a tariff_price column, we group hourly to create 24 tariff windows.
    # Otherwise, fallback to a standard pricing structure.
    for hour_offset in range(24):
        slot_start = start_time + timedelta(hours=hour_offset)
        slot_end = slot_start + timedelta(hours=1)
        
        # Filter CSV records that fall in this hour
        hour_records = df[
            (df["minute_offset"] >= hour_offset * 60) & 
            (df["minute_offset"] < (hour_offset + 1) * 60)
        ]
        
        if not hour_records.empty and "tariff_price" in df.columns:
            price = float(hour_records["tariff_price"].mean())
        else:
            # Fallback to default mock tariff rules if not present in CSV
            hour_of_day = slot_start.hour
            if 1 <= hour_of_day <= 4:
                price = 0.05  # Super cheap night
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
        
    tariff_info = TariffInfo(
        current_price=tariff_windows[0].price,
        feed_in_tariff=feed_in,
        cheap_price_threshold=cheap_threshold,
        battery_charge_price_threshold=battery_charge_threshold,
        windows=tariff_windows
    )
    
    # --- Generate solar ForecastData ---
    hourly_forecasts = []
    total_kwh = 0.0
    
    for hour_offset in range(24):
        slot_start = start_time + timedelta(hours=hour_offset)
        slot_end = slot_start + timedelta(hours=1)
        
        # Filter CSV records that fall in this hour
        hour_records = df[
            (df["minute_offset"] >= hour_offset * 60) & 
            (df["minute_offset"] < (hour_offset + 1) * 60)
        ]
        
        if not hour_records.empty and "forecast_pv_power" in df.columns:
            expected_watts = float(hour_records["forecast_pv_power"].mean())
        else:
            # Fallback mock forecast solar tomorrow
            hour_of_day = slot_start.hour
            if 8 <= hour_of_day <= 16:
                expected_watts = 4000.0
            else:
                expected_watts = 0.0
                
        expected_kwh = expected_watts / 1000.0
        hourly_forecasts.append(HourlyForecast(
            start=slot_start,
            end=slot_end,
            expected_kwh=expected_kwh,
            expected_watts=expected_watts
        ))
        total_kwh += expected_kwh
        
    forecast_data = ForecastData(
        remaining_today_kwh=total_kwh,
        hourly_breakdown=hourly_forecasts,
        tomorrow_total_kwh=total_kwh
    )
    
    return run_steps, tariff_info, forecast_data
