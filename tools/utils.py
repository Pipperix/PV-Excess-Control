import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pathlib
from datetime import datetime
import math
import sys
import os

# Add the project root to sys.path so we can import the custom component modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from custom_components.pv_excess_control.models import ApplianceConfig, Plan, Action

def export_results_to_csv(records: list[dict], filename: str = "simulation_results.csv"):
    """Exports simulation records to a CSV file using pandas."""
    df = pd.DataFrame(records)
    output_path = pathlib.Path(__file__).parent / filename
    df.to_csv(output_path, index=False)
    print(f"\nSimulation results exported to: {output_path}")

# TODO: Spiegare come si vuole rappresentare il grafico
def generate_plotly_dashboard(records: list[dict], filename: str = "simulation_plotly_dashboard.html"):
    """
    Generates an interactive Plotly dashboard from simulation records.
    Styled similarly to EMHASS visualize_benchmark.py, keeping comparison between
    planner schedule and optimizer actual execution.
    """
    df = pd.DataFrame(records)
    
    # Create subplots: Row 1 for general energy, Row 2 for plan vs reality
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.1,
        subplot_titles=(
            "<b>General Energy Flows & Battery</b>",
            "<b>Planner Schedule vs Actual Execution</b>"
        ),
        specs=[[{"secondary_y": True}], [{"secondary_y": False}]]
    )

    # --- ROW 1: General Energy Flows ---
    # PV Production (W) - Orange curve
    fig.add_trace(
        go.Scatter(
            x=df['time'], y=df['pv'], name="PV Production (W)",
            line=dict(color='#ff9f43', width=2.5),
            fill='tozeroy', fillcolor='rgba(255, 159, 67, 0.1)'
        ),
        row=1, col=1, secondary_y=False,
    )

    # House Load (W) - Blue dashed curve
    fig.add_trace(
        go.Scatter(
            x=df['time'], y=df['house_load'], name="House Load (W)",
            line=dict(color='#2e86de', width=2, dash='dash')
        ),
        row=1, col=1, secondary_y=False,
    )

    # Battery Power (W) - Cyan line
    fig.add_trace(
        go.Scatter(
            x=df['time'], y=df['battery_power'], name="Battery Power (W)", 
            line=dict(color='#0abde3', width=2)
        ),
        row=1, col=1, secondary_y=False,
    )

    # Grid Import (W) - Red line
    fig.add_trace(
        go.Scatter(
            x=df['time'], y=df['grid_import'], name="Grid Import (W)", 
            line=dict(color='#ee5253', width=1.5)
        ),
        row=1, col=1, secondary_y=False,
    )
    
    # Grid Export (W) - Purple line
    fig.add_trace(
        go.Scatter(
            x=df['time'], y=df['grid_export'], name="Grid Export (W)", 
            line=dict(color='#9b59b6', width=1.5)
        ),
        row=1, col=1, secondary_y=False,
    )

    # Washing Machine actual power (W) - Semi-transparent Teal Bar
    if 'washing_machine_power' in df.columns:
        fig.add_trace(
            go.Bar(
                x=df['time'], y=df['washing_machine_power'], name="WM Power (W)",
                marker=dict(color='#1dd1a1', line=dict(width=0)),
                opacity=0.35
            ),
            row=1, col=1, secondary_y=False,
        )

    # Miner actual power (W) - Semi-transparent Blue Bar
    if 'miner_power' in df.columns:
        fig.add_trace(
            go.Bar(
                x=df['time'], y=df['miner_power'], name="Miner Power (W)",
                marker=dict(color='#2e86de', line=dict(width=0)),
                opacity=0.35
            ),
            row=1, col=1, secondary_y=False,
        )

    # Battery SOC (%) - Dark grey line on secondary y-axis
    fig.add_trace(
        go.Scatter(
            x=df['time'], y=df['battery_soc'], name="Battery SOC (%)", 
            line=dict(color='#57606f', width=2, dash='dot')
        ),
        row=1, col=1, secondary_y=True,
    )

    # --- ROW 2: Plan vs Reality Comparison ---
    # Washing Machine (WM) actual power - Teal green (like Miner Power from visualize_benchmark.py)
    if 'washing_machine_power' in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df['time'], y=df['washing_machine_power'], name="WM Power Real (W)", 
                line=dict(color='#1dd1a1', width=2.5),
                fill='tozeroy', fillcolor='rgba(29, 209, 161, 0.1)'
            ),
            row=2, col=1
        )
    # Washing Machine (WM) planned power
    if 'washing_machine_planned_power' in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df['time'], y=df['washing_machine_planned_power'], name="WM Power Plan (W)", 
                line=dict(color='#1dd1a1', width=2, dash='dot')
            ),
            row=2, col=1
        )
    
    # Miner actual power - Blue
    if 'miner_power' in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df['time'], y=df['miner_power'], name="Miner Power Real (W)", 
                line=dict(color='#2e86de', width=2.5),
                fill='tozeroy', fillcolor='rgba(46, 134, 222, 0.1)'
            ),
            row=2, col=1
        )
    # Miner planned power
    if 'miner_planned_power' in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df['time'], y=df['miner_planned_power'], name="Miner Power Plan (W)", 
                line=dict(color='#2e86de', width=2, dash='dot')
            ),
            row=2, col=1
        )

    # Layout styling from visualize_benchmark.py
    fig.update_layout(
        title={
            'text': "<b>PV Excess Control - Simulation & Planning Dashboard</b>",
            'y':0.95,
            'x':0.5,
            'xanchor': 'center',
            'yanchor': 'top'
        },
        template="plotly_white",
        barmode='overlay',
        legend=dict(
            orientation="h", 
            yanchor="bottom", 
            y=1.02, 
            xanchor="right", 
            x=1,
            bgcolor='rgba(255, 255, 255, 0.7)'
        ),
        hovermode="x unified",
        margin=dict(t=160, b=50, l=60, r=60),
        height=850
    )

    # Coordinate y-axes ranges to align their zero lines perfectly
    power_cols = ['pv', 'house_load', 'battery_power', 'grid_import', 'grid_export']
    if 'washing_machine_power' in df.columns:
        power_cols.append('washing_machine_power')
    if 'miner_power' in df.columns:
        power_cols.append('miner_power')

    min_power = df[power_cols].min().min()
    max_power = df[power_cols].max().max()

    y1_max = max_power * 1.1 if max_power > 0 else 1000.0
    if min_power < 0:
        y1_min = min_power * 1.1
    else:
        y1_min = -y1_max * 0.05  # Small padding below zero if no negative values exist

    # Align the zero lines: y2_min / y2_max must equal y1_min / y1_max
    y2_max = 105.0
    y2_min = y2_max * (y1_min / y1_max)

    # Axes and scales
    fig.update_yaxes(title_text="Power (Watts)", range=[y1_min, y1_max], row=1, col=1, secondary_y=False)
    fig.update_yaxes(title_text="State of Charge (%)", range=[y2_min, y2_max], row=1, col=1, secondary_y=True)
    fig.update_yaxes(title_text="Power (Watts)", row=2, col=1)
    fig.update_xaxes(title_text="Time", row=2, col=1)

    output_path = pathlib.Path(__file__).parent / filename
    fig.write_html(output_path)
    print(f"Interactive Plotly dashboard saved to: {output_path}")

def get_price_for_time(current_ts: datetime, windows) -> float:
    """Helper to find the dynamic tariff price for a given timestamp."""
    for w in windows:
        if w.start <= current_ts < w.end:
            return w.price
    return windows[0].price if windows else 0.20

# TODO: Sarebbe meglio analizzare cosa il plan ritorna, per poter estrarre un grafico da esso
def get_planned_power(appliance_cfg: ApplianceConfig, current_ts: datetime, plan: Plan) -> float:
    """Returns the theoretical power (in Watts) that the Planner scheduled for
    the given appliance at the given timestamp."""
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

# TODO: Inserire funzione helper per generare dataset di 24h
# leggere la documentazione per sapere ogni quanto viene indicata la risoluzione
def generate_24h_dataset(step_minutes: int = 1) -> list[tuple[int, float, float]]:
    """Generates a 24-hour dataset with the specified minute resolution for the simulation."""
    dataset = []
    for m in range(0, 1440, step_minutes):
        # Solar production: sinusoidal bell curve peaking at 8000W between 06:00 and 18:00.
        if 360 <= m < 1080:
            pv = 8000.0 * math.sin(math.pi * (m - 360) / 720.0)
            if 750 <= m < 795:
                pv = 800.0
        else:
            pv = 0.0
            
        # Household load: 300W base + sinusoidal consumption peaks
        load = 300.0
        if 420 <= m < 510:  # Breakfast peak (07:00-08:30)
            load += 1200.0 * math.sin(math.pi * (m - 420) / 90.0)
        elif 720 <= m < 780:  # Lunch peak (12:00-13:00)
            load += 1700.0 * math.sin(math.pi * (m - 720) / 60.0)
        elif 1140 <= m < 1290:  # Dinner peak (19:00-21:30)
            load += 2200.0 * math.sin(math.pi * (m - 1140) / 150.0)
            
        dataset.append((m, pv, load))
    return dataset
