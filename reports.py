import pandas as pd
import plotly.express as px
import streamlit as st
import datetime as dt
import sqlite3
import os

# --- Database Connection and Data Loading ---
@st.cache_data
def load_trends_data():
    """
    Connects to the SQLite database and loads mutual fund trend data from
    the 'mf_nav_trends' table into a DataFrame.
    """
    db_path = "db/eqt.db" # The same path used in main.py
    if not os.path.exists(db_path):
        st.error(f"Database file not found at {db_path}. Please run main.py first.")
        return pd.DataFrame()

    try:
        conn = sqlite3.connect(db_path)
        # Execute a query to get all data from the mf_nav_trends table
        query = "SELECT * FROM mf_nav_trends"
        df = pd.read_sql(query, conn)
        conn.close()
        
        # Pre-process the data as before
        df['Date'] = pd.to_datetime(df['Date']).dt.date
        return df
    except sqlite3.Error as e:
        st.error(f"Database error: {e}")
        return pd.DataFrame()
    except Exception as e:
        st.error(f"An unexpected error occurred: {e}")
        return pd.DataFrame()


@st.cache_data
def load_returns_data():
    """
    Connects to the SQLite database and loads calculated returns data from
    the 'mf_nav_returns' table into a DataFrame.
    """
    db_path = "db/eqt.db"
    if not os.path.exists(db_path):
        st.error(f"Database file not found at {db_path}. Please run main.py first.")
        return pd.DataFrame()
    
    try:
        conn = sqlite3.connect(db_path)
        query = "SELECT * FROM mf_nav_returns"
        df = pd.read_sql(query, conn)
        conn.close()
        return df
    except sqlite3.Error as e:
        st.error(f"Database error: {e}")
        return pd.DataFrame()
    except Exception as e:
        st.error(f"An unexpected error occurred: {e}")
        return pd.DataFrame()

# App layout to full width
st.set_page_config(layout="wide")

# Load data from the new database function
df_trends = load_trends_data()
df_returns = load_returns_data()

# Exit early if data loading failed
if df_trends.empty or df_returns.empty:
    st.stop()

# Define start dates - Using dt alias to avoid conflicts
current_time = dt.datetime.now() # Store current time once
start_date_10y = (current_time - dt.timedelta(days=365*10)).date()
start_date_5y = (current_time - dt.timedelta(days=365*5)).date()
start_date_3y = (current_time - dt.timedelta(days=365*3)).date()
start_date_1y = (current_time - dt.timedelta(days=365)).date()
start_date_6m = (current_time - dt.timedelta(days=180)).date()
start_date_3m = (current_time - dt.timedelta(days=90)).date()
start_date_ytd = dt.datetime(current_time.year, 1, 1).date()

# UI Title
st.markdown("📈 SBI Thematic Funds: Rolling 3-Month Avg Trends")
with st.sidebar:
    st.header("Filters")
    # Fund Category selector
    available_categories = sorted(df_trends['Category'].unique())
    # Default to only "Indexes" if present, else fallback to all
    default_categories = ["Indexes"] if "Indexes" in available_categories else available_categories
    selected_categories = st.multiselect(
        "Select fund category(s):",
        options=available_categories,
        default=default_categories,
        placeholder="Choose fund categories...",
    )

    # Filter funds based on selected categories first
    df_trends_by_category = df_trends[df_trends['Category'].isin(selected_categories)]
    df_returns_by_category = df_returns[df_returns['Category'].isin(selected_categories)]


    # Fund selector, now filtered by category
    available_funds = sorted(df_trends_by_category['Fund_Name'].unique())
    selected_funds = st.multiselect(
        "Select fund(s) to compare:",
        options=available_funds,
        default=available_funds,
        placeholder="Choose fund names...",
    )

# Filter for selected funds for both dataframes
df_trends_filtered = df_trends_by_category[df_trends_by_category['Fund_Name'].isin(selected_funds)]
df_returns_filtered = df_returns_by_category[df_returns_by_category['Fund_Name'].isin(selected_funds)]


# Filter per trend
df_10y = df_trends_filtered[df_trends_filtered['Date'] >= start_date_10y]
df_5y = df_trends_filtered[df_trends_filtered['Date'] >= start_date_5y]
df_3y = df_trends_filtered[df_trends_filtered['Date'] >= start_date_3y]
df_1y = df_trends_filtered[df_trends_filtered['Date'] >= start_date_1y]
df_6m = df_trends_filtered[df_trends_filtered['Date'] >= start_date_6m]
df_3m = df_trends_filtered[df_trends_filtered['Date'] >= start_date_3m]
df_ytd = df_trends_filtered[df_trends_filtered['Date'] >= start_date_ytd]

# Improved plot function
def create_plot(data, y_col, title):
    fig = px.line(
        data,
        x='Date', # The x-axis remains 'Date' to show trends over time
        y=y_col,
        color='Fund_Name', # Fund names are used for coloring and legend
        markers=False, # Remove dots/markers
        labels={'Date': 'Date', y_col: 'Rolling Avg %', 'Fund_Name': 'Fund Name'},
        title=title
    )
    fig.add_hline(y=0, line_dash="dash", line_color="gray")

    # Set NIFTY 50 line color to grey
    for trace in fig.data:
        if 'NIFTY 50' in trace.name:
            trace.line.color = 'grey'
    
    # Add end value labels for each line
    for fund in data['Fund_Name'].unique():
        fund_data = data[data['Fund_Name'] == fund].sort_values('Date')
        if not fund_data.empty:
            last_point = fund_data.iloc[-1]
            fig.add_annotation(
                x=last_point['Date'],
                y=last_point[y_col],
                text=f"{last_point[y_col]:.1f}%",
                showarrow=False,
                xanchor="left",
                yanchor="middle",
                xshift=5,
                font=dict(size=10, color="black")
            )
    
    fig.update_layout(
        template="plotly_white",
        title_font_size=16,
        height=450, # Increased height to accommodate labels
        margin=dict(l=40, r=60, t=60, b=120), # Increased right margin for end labels
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.35, # Adjusted legend position
            xanchor="center",
            x=0.5,
            font=dict(size=10) # Smaller legend font
        ),
        xaxis=dict(
            tickangle=0, # Horizontal labels
            tickmode='auto',
            nticks=6, # Reduced number of ticks for horizontal labels
            tickfont=dict(size=10)
        ),
        yaxis=dict(
            tickfont=dict(size=10)
        )
    )
    # Make lines thinner and remove markers
    fig.update_traces(
        line=dict(width=2), # Thinner lines
        mode='lines' # Only lines, no markers
    )
    return fig

# Create figures
fig_10y = create_plot(df_10y, 'Rolling_3M_Avg_10Y_Pct', "10Y (Rolling 3M Avg)")
fig_5y = create_plot(df_5y, 'Rolling_3M_Avg_5Y_Pct', "5Y (Rolling 3M Avg)")
fig_3y = create_plot(df_3y, 'Rolling_3M_Avg_3Y_Pct', "3Y (Rolling 3M Avg)")
fig_1y = create_plot(df_1y, 'Rolling_3M_Avg_1Y_Pct', "1Y (Rolling 3M Avg)")
fig_6m = create_plot(df_6m, 'Rolling_3M_Avg_6M_Pct', "6M (Rolling 3M Avg)")
fig_3m = create_plot(df_3m, 'Rolling_3M_Avg_3M_Pct', "3M (Rolling 3M Avg)")
fig_ytd = create_plot(df_ytd, 'Rolling_3M_Avg_YTD_Pct', "YTD (Rolling 3M Avg)")


# --- Reports Logic ---
def create_returns_tables(df_returns):
    """
    Generates a single DataFrame for yearly and quarterly returns,
    showing only the last 4 years and 3 quarters with specific column names.
    """
    df_returns = df_returns.copy()

    # Get the current year and quarter
    today = dt.date.today()
    current_year = today.year
    current_quarter = (today.month - 1) // 3 + 1
    
    # Identify and rename the quarterly columns
    quarterly_cols_map = {}
    quarterly_cols_map['QTD_Return'] = f'{current_year}-Q{current_quarter} (QTD)'
    
    # Get the last 3 quarters
    quarterly_cols = sorted([col for col in df_returns.columns if col.startswith('Abs_Return_Q')], reverse=True)
    for i, col in enumerate(quarterly_cols[:3]):
        # Extract quarter and year from column name like 'Abs_Return_Q4_2024'
        parts = col.split('_')
        quarter = parts[2]
        year = parts[3]
        quarterly_cols_map[col] = f'{year}-{quarter}'
        
    df_returns.rename(columns=quarterly_cols_map, inplace=True)
    
    # Identify and rename the yearly columns
    yearly_cols_map = {}
    yearly_cols_map['YTD_Return'] = f'{current_year}(YTD)'

    # Get the last 9 years
    yearly_cols = sorted([col for col in df_returns.columns if col.startswith('Abs_Return_2')], reverse=True)
    for i, col in enumerate(yearly_cols[:9]):
        # Extract year from column name like 'Abs_Return_2024'
        year = col.split('_')[-1]
        yearly_cols_map[col] = year
        
    df_returns.rename(columns=yearly_cols_map, inplace=True)
    
    # Combine the column names in the desired order
    final_cols = ['Fund_Name', 'Category'] + list(quarterly_cols_map.values()) + list(yearly_cols_map.values())
    
    # Select the final DataFrame
    return df_returns[final_cols]

# Generate the single combined returns table from the filtered data
combined_returns_df = create_returns_tables(df_returns_filtered)
# --- UI Tabs ---

# Define a function for styling negative values
def highlight_negative(val):
    """Highlights negative values with a light red background."""
    if isinstance(val, (int, float)) and val < 0:
        return 'background-color: #ffcccc'
    return ''

tab1, tab2, tab3 = st.tabs(["Short-Term Trends", "Long-Term Trends", "Returns Report"])

with tab1:
    st.markdown("### Short-Term Trends")
    # 3x3 grid layout with 4 charts
    col1, col2, col3 = st.columns(3)
    with col1:
        st.plotly_chart(fig_3m, use_container_width=True)
    with col2:
        st.plotly_chart(fig_6m, use_container_width=True)
    with col3:
        st.plotly_chart(fig_ytd, use_container_width=True)
    
    col4, col5, col6 = st.columns(3)
    with col4:
        st.plotly_chart(fig_1y, use_container_width=True)
    with col5:
        # Empty column
        pass
    with col6:
        # Empty column
        pass

with tab2:
    st.markdown("### Long-Term Trends")
    # 3x3 grid layout with 3 charts
    col1, col2, col3 = st.columns(3)
    with col1:
        st.plotly_chart(fig_3y, use_container_width=True)
    with col2:
        st.plotly_chart(fig_5y, use_container_width=True)
    with col3:
        st.plotly_chart(fig_10y, use_container_width=True)

with tab3:
    st.markdown("### Returns Report")
    st.markdown("#### Combined Returns")
    # Apply the styling function before displaying the dataframe
    st.dataframe(combined_returns_df.style.applymap(highlight_negative), use_container_width=True)