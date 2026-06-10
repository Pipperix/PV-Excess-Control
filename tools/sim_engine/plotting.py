"""
PV Excess Control - Simulation Output and Plotting Module.
Provides helper functions to export simulation results to CSV and generate
an interactive Plotly HTML dashboard comparing planner schedules with real-time executions.
"""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pathlib
from typing import List, Dict, Any

from custom_components.pv_excess_control.models import ApplianceConfig

def export_results_to_csv(records: List[Dict[str, Any]], filename: str = "simulation_results.csv", output_subfolder: str = ""):
    """
    Exports simulation records to a CSV file inside the output directory.
    
    Args:
        records: List of dictionaries representing metrics recorded at each simulation timestep.
        filename: Name of the output CSV file.
        output_subfolder: Optional subdirectory name to group outputs.
    """
    df = pd.DataFrame(records)
    output_dir = pathlib.Path(__file__).parent.parent / "output"
    if output_subfolder:
        output_dir = output_dir / output_subfolder
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename
    df.to_csv(output_path, index=False)
    print(f"\nSimulation results exported to: {output_path}")

def generate_plotly_dashboard(
    records: List[Dict[str, Any]], 
    appliance_configs: List[ApplianceConfig],
    filename: str = "simulation_plotly_dashboard.html",
    is_planner_only: bool = False,
    output_subfolder: str = ""
):
    """
    Generates an interactive Plotly dashboard comparing planner predictions and real-time outcomes.
    Outputs the dashboard inside the output directory.
    
    Args:
        records: List of dictionaries representing metrics recorded at each simulation timestep.
        appliance_configs: List of ApplianceConfig objects to dynamically render comparison curves.
        filename: Name of the output HTML file.
        is_planner_only: If True, renders only the general energy flow row.
        output_subfolder: Optional subdirectory name to group outputs.
    """
    df = pd.DataFrame(records)
    
    # Unified sign conventions
    df['grid_power'] = df['grid_import'] - df['grid_export']
    df['battery_power_plot'] = -df['battery_power']  # >0 means Discharging

    # Dynamic colors for appliances
    # Washing machine (first) gets teal (#1dd1a1), Miner (second) gets blue (#2e86de)
    PALETTE = ['#1dd1a1', '#2e86de', '#ff9f43', '#9b59b6', '#ee5253', '#0abde3', '#10ac84', '#5f27cd']
    
    # Create subplots: Row 1 for general energy flow, Row 2 for plan vs reality
    rows = 1 if is_planner_only else 2
    subplot_titles = ("<b>Planned Energy Flows & Battery Strategy</b>",) if is_planner_only else (
        "<b>General Energy Flows & Battery</b>",
        "<b>Planner Schedule vs Actual Execution</b>"
    )
    
    fig = make_subplots(
        rows=rows, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.1 if not is_planner_only else 0,
        subplot_titles=subplot_titles,
        specs=[[{"secondary_y": True}]] if is_planner_only else [[{"secondary_y": True}], [{"secondary_y": False}]]
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
            x=df['time'], y=df['battery_power_plot'], name="Battery Power (W) [>0 Disch]", 
            line=dict(color='#0abde3', width=2)
        ),
        row=1, col=1, secondary_y=False,
    )

    # Grid Power (W) - Red line
    fig.add_trace(
        go.Scatter(
            x=df['time'], y=df['grid_power'], name="Grid Power (W) [>0 Import]", 
            line=dict(color='#ee5253', width=1.5)
        ),
        row=1, col=1, secondary_y=False,
    )

    # Add actual power traces (or planned power if planner-only) for all appliances as semi-transparent bars in Row 1
    for i, app in enumerate(appliance_configs):
        power_col = f"{app.id}_planned_power" if is_planner_only else f"{app.id}_power"
        color = PALETTE[i % len(PALETTE)]
        if power_col in df.columns:
            fig.add_trace(
                go.Bar(
                    x=df['time'], y=df[power_col], name=f"{app.name} Power (W)",
                    marker=dict(color=color, line=dict(width=0)),
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

    if not is_planner_only:
        # --- ROW 2: Plan vs Reality Comparison ---
        # Add comparative lines for all appliances in Row 2
        for i, app in enumerate(appliance_configs):
            actual_col = f"{app.id}_power"
            planned_col = f"{app.id}_planned_power"
            color = PALETTE[i % len(PALETTE)]
            
            # Real-time executed power (solid line with transparent fill)
            if actual_col in df.columns:
                fig.add_trace(
                    go.Scatter(
                        x=df['time'], y=df[actual_col], name=f"{app.name} Real (W)", 
                        line=dict(color=color, width=2.5),
                        fill='tozeroy', fillcolor=f'rgba({int(color[1:3], 16)}, {int(color[3:5], 16)}, {int(color[5:7], 16)}, 0.1)'
                    ),
                    row=2, col=1
                )
            
            # Planned schedule power (dotted line)
            if planned_col in df.columns:
                fig.add_trace(
                    go.Scatter(
                        x=df['time'], y=df[planned_col], name=f"{app.name} Plan (W)", 
                        line=dict(color=color, width=2, dash='dot')
                    ),
                    row=2, col=1
                )

    # Layout styling matching visualize_benchmark.py
    fig.update_layout(
        title={
            'text': "<b>PV Excess Control - Simulation & Planning Dashboard</b>",
            'x': 0.5,
            'xanchor': 'center',
            'y': 0.98,
            'yref': 'container'
        },
        template="plotly_white",
        barmode='overlay',
        legend=dict(
            orientation="h", 
            yanchor="bottom", 
            y=1.05, 
            xanchor="center", 
            x=0.5,
            bgcolor='rgba(255, 255, 255, 0.7)'
        ),
        hovermode="x unified",
        margin=dict(t=160, b=80, l=60, r=60),
        height=600 if is_planner_only else 1000
    )

    # Coordinate y-axes ranges to align their zero lines perfectly
    power_cols = ['pv', 'house_load', 'battery_power_plot', 'grid_power']
    for app in appliance_configs:
        col = f"{app.id}_planned_power" if is_planner_only else f"{app.id}_power"
        if col in df.columns:
            power_cols.append(col)

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
    if not is_planner_only:
        fig.update_yaxes(title_text="Power (Watts)", row=2, col=1)
        fig.update_xaxes(title_text="Time", showticklabels=True, row=2, col=1)
        fig.update_xaxes(showticklabels=True, row=1, col=1)
    else:
        fig.update_xaxes(title_text="Time", showticklabels=True, row=1, col=1)


    output_dir = pathlib.Path(__file__).parent.parent / "output"
    if output_subfolder:
        output_dir = output_dir / output_subfolder
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename
    fig.write_html(output_path)
    print(f"Interactive Plotly dashboard saved to: {output_path}")

def export_summary_to_txt(summary_text: str, filename: str = "summary.txt", output_subfolder: str = ""):
    """
    Exports the textual summary to a file inside the output directory.
    
    Args:
        summary_text: The entire summary string to be written.
        filename: Name of the output text file.
        output_subfolder: Optional subdirectory name to group outputs.
    """
    output_dir = pathlib.Path(__file__).parent.parent / "output"
    if output_subfolder:
        output_dir = output_dir / output_subfolder
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(summary_text)
    print(f"Simulation textual summary saved to: {output_path}")
