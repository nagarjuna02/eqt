# modules.py
import os
import re
import sqlite3
import pandas as pd
import yfinance as yf
from typing import Dict, Any, Optional
import logging
from datetime import datetime, timedelta

# Configure logging
logger = logging.getLogger(__name__)

_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# --- Database Operations Section ---------------------------------------------

def get_db_connection(db_path: str) -> sqlite3.Connection:
    """Establishes a connection to the SQLite database."""
    return sqlite3.connect(db_path)

def quote_identifier(identifier: str) -> str:
    """Safely quotes a SQLite identifier that comes from configuration."""
    if not _SQL_IDENTIFIER_RE.fullmatch(identifier):
        raise ValueError(f"Invalid SQLite identifier: {identifier!r}")
    return f'"{identifier}"'

def get_last_date_for_fund(ticker: str, db_path: str, table_name: str) -> Optional[str]:
    """
    Retrieves the most recent date for a given ticker from the database.
    Returns None if the table or ticker data doesn't exist.
    """
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT MAX(Date) FROM {quote_identifier(table_name)} WHERE Ticker = ?", (ticker,))
            result = cursor.fetchone()
            return result[0] if result and result[0] else None
    except sqlite3.Error as e:
        logger.error(f"Database error getting last date for {ticker} from {table_name}: {e}")
        return None

def delete_records_from_date(ticker: str, start_date: str, db_path: str, table_name: str) -> None:
    """
    Deletes records for a specific ticker from a given start date.
    """
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"DELETE FROM {quote_identifier(table_name)} WHERE Ticker = ? AND Date >= ?",
                (ticker, start_date),
            )
            conn.commit()
            logger.info(f"Deleted existing data for {ticker} from {start_date} onwards from {table_name}.")
    except sqlite3.Error as e:
        logger.error(f"Database error deleting records for {ticker}: {e}")
    except Exception as e:
        logger.error(f"Error deleting data for {ticker}: {e}")

def save_master_data_to_db(df: pd.DataFrame, db_path: str, table_name: str) -> None:
    """Saves a DataFrame of master fund data to the specified SQLite table."""
    try:
        quote_identifier(table_name)
        with get_db_connection(db_path) as conn:
            # Using 'replace' to ensure only the latest master data is kept for a ticker
            df.to_sql(table_name, conn, if_exists='replace', index=False)
            logger.info(f"Successfully saved {len(df)} new records to '{table_name}'.")
    except sqlite3.Error as e:
        logger.error(f"SQLite error saving master data: {e}")
    except Exception as e:
        logger.error(f"Error saving to database: {e}")

def save_nav_data_to_db(df: pd.DataFrame, db_path: str, table_name: str) -> None:
    """Saves a DataFrame of NAV data to the specified SQLite table."""
    try:
        quote_identifier(table_name)
        with get_db_connection(db_path) as conn:
            # Using 'append' for time-series data
            df.to_sql(table_name, conn, if_exists='append', index=False)
            logger.info(f"Successfully saved {len(df)} new NAV records to '{table_name}'.")
    except sqlite3.Error as e:
        logger.error(f"SQLite error saving NAV data: {e}")
    except Exception as e:
        logger.error(f"Error saving to database: {e}")

# --- Data Fetching & Processing Section --------------------------------------

def get_fund_info(ticker: str) -> Dict[str, Any]:
    """
    Fetches detailed fund information from Yahoo Finance.
    """
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        
        result = {
            'Fund_Family': info.get('fundFamily', 'N/A'),
            'Long_Name': info.get('longName', 'N/A'),
            'Fund_Type': info.get('quoteType', 'N/A'),
            'Currency': info.get('currency', 'N/A'),
            'Expense_Ratio': info.get('annualReportExpenseRatio', 'N/A'),
            'Net_Assets': info.get('totalAssets', 'N/A'),
            'Market_Cap': info.get('marketCap', 'N/A'),
            'NAV': info.get('navPrice', 'N/A'),
            'Fund_Manager': info.get('manager', 'N/A'),
            'Management_Company': info.get('managementCompany', 'N/A'),
            'Inception_Date': info.get('fundInceptionDate', 'N/A'),
            'Yield': info.get('yield', 'N/A'),
            'Beta': info.get('beta', 'N/A'),
            'PE_Ratio': info.get('trailingPE', 'N/A'),
            'Holdings_Count': info.get('holdingsCount', 'N/A'),
            'Holdings_Turnover': info.get('annualHoldingsTurnover', 'N/A'),
            'Investment_Strategy': info.get('longBusinessSummary', 'N/A'),
            'Category_Name': info.get('categoryName', 'N/A'),
            'Fund_Description': info.get('description', 'N/A')
        }
        # Filter out fields that will not be in the master table as per user request
        return {key: value for key, value in result.items() if key not in ['Expense_Ratio', 'Net_Assets', 'PE_Ratio', 'Holdings']}
    except Exception as e:
        logger.error(f"Error getting info for {ticker}: {e}")
        return {key: 'N/A' for key in [
            'Fund_Family', 'Long_Name', 'Fund_Type', 'Currency',
            'Market_Cap', 'NAV', 'Fund_Manager', 'Management_Company',
            'Inception_Date', 'Yield', 'Beta', 'Investment_Strategy',
            'Category_Name', 'Fund_Description'
        ]}


def get_price_data(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    Fetches historical price data for a given ticker and date range.
    """
    try:
        df = yf.download(
            tickers=ticker, 
            start=start_date, 
            end=end_date, 
            progress=False, 
            auto_adjust=True
        )
        if df.empty:
            logger.warning(f"No data available for {ticker} in the range {start_date} to {end_date}.")
            return pd.DataFrame()
        
        # Handle multi-index columns from yfinance for single tickers
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
            
        df = df.reset_index()
        # Removed the problematic rename, as 'Close' is already adjusted when auto_adjust=True
        return df
    except Exception as e:
        logger.error(f"Error fetching price data for {ticker}: {e}")
        return pd.DataFrame()

def calculate_and_save_trends(db_path: str, nav_table: str, master_table: str, trends_table: str) -> None:
    """
    Reads data from the new mf_nav table, merges with master data,
    calculates rolling averages and percentage changes, and saves
    the results to a new destination table.
    """
    logger.info("Starting trend calculation process...")
    try:
        with get_db_connection(db_path) as conn:
            # Read all data from the source table, using 'Close'
            df_nav = pd.read_sql_query(
                f"SELECT Ticker, Date, Close FROM {quote_identifier(nav_table)}",
                conn,
            )
            df_master = pd.read_sql_query(
                f"SELECT Ticker, Fund_Name, Category FROM {quote_identifier(master_table)}",
                conn,
            )
            
            if df_nav.empty:
                logger.warning(f"No NAV data found in {nav_table} to calculate trends.")
                return

            # Convert 'Close' column to numeric, coercing errors to NaN
            df_nav['Close'] = pd.to_numeric(df_nav['Close'], errors='coerce')
            df_nav.dropna(subset=['Close'], inplace=True)

            # Merge the NAV and master data to get fund name and category
            df_trends = pd.merge(df_nav, df_master, on='Ticker', how='left')

            # Convert Date column to datetime
            df_trends['Date'] = pd.to_datetime(df_trends['Date'])
            df_trends['Date'] = df_trends['Date'].dt.date

            # Sort by Ticker and Date to ensure proper order for rolling calculations
            df_trends = df_trends.sort_values(['Ticker', 'Date']).reset_index(drop=True)

            # Apply rolling average to each ticker group, using 'Close'
            df_trends['Rolling_3M_Avg'] = df_trends.groupby('Ticker')['Close'].transform(
                lambda x: x.rolling(window=90, min_periods=30, center=True).mean()
            )

            # Define base dates for percentage change calculations
            today = datetime.now().date()
            start_dates = {
                '10Y': (today - timedelta(days=365 * 10)),
                '5Y': (today - timedelta(days=365 * 5)),
                '3Y': (today - timedelta(days=365 * 3)),
                '1Y': (today - timedelta(days=365)),
                '6M': (today - timedelta(days=180)),
                '3M': (today - timedelta(days=90)),
                'YTD': datetime(today.year, 1, 1).date(),
            }

            # Function to get the base value from the nearest date
            def get_base_value_from_nearest_date(group, start_date, column_name):
                filtered = group[group['Date'] >= start_date]
                if not filtered.empty:
                    return filtered.iloc[0][column_name]
                else:
                    return None

            # Create a new DataFrame to store trends
            trends_df = df_trends[['Ticker', 'Fund_Name', 'Category', 'Date', 'Close', 'Rolling_3M_Avg']].copy()

            # Calculate and map base values and percentage changes
            for period, start_date in start_dates.items():
                base_close_values = trends_df.groupby('Ticker', group_keys=False).apply(
                    lambda g: get_base_value_from_nearest_date(g, start_date, 'Close'), include_groups=False
                )
                base_rolling_values = trends_df.groupby('Ticker', group_keys=False).apply(
                    lambda g: get_base_value_from_nearest_date(g, start_date, 'Rolling_3M_Avg'), include_groups=False
                )
                
                trends_df[f'Base_Close_{period}'] = trends_df['Ticker'].map(base_close_values)
                trends_df[f'Base_Rolling_{period}'] = trends_df['Ticker'].map(base_rolling_values)
                
                trends_df[f'Close_{period}_Pct'] = (
                    (trends_df['Close'] - trends_df[f'Base_Close_{period}']) / 
                    trends_df[f'Base_Close_{period}']
                ) * 100
                
                trends_df[f'Rolling_3M_Avg_{period}_Pct'] = (
                    (trends_df['Rolling_3M_Avg'] - trends_df[f'Base_Rolling_{period}']) / 
                    trends_df[f'Base_Rolling_{period}']
                ) * 100
            
            # Save the calculated trends to the new table, replacing any old data
            trends_to_save = trends_df.dropna(subset=['Rolling_3M_Avg'])
            quote_identifier(trends_table)
            trends_to_save.to_sql(trends_table, conn, if_exists='replace', index=False)
            logger.info(f"Successfully calculated and saved {len(trends_to_save)} new records to '{trends_table}'.")

    except Exception as e:
        logger.error(f"Error during trends calculation: {e}")
def calculate_and_store_returns(db_path: str, trends_table: str, returns_table: str) -> None:
    """
    Connects to the SQLite database, calculates various absolute returns
    based on a 3-month rolling average, and stores them in a new table.
    """
    if not os.path.exists(db_path):
        logger.error(f"Database file not found at {db_path}.")
        return

    try:
        quote_identifier(returns_table)
        with get_db_connection(db_path) as conn:
            logger.info("Connected to the database.")

            # Load the data, including the pre-calculated Rolling_3M_Avg
            query = (
                "SELECT Ticker, Fund_Name, Category, Date, Close, Rolling_3M_Avg "
                f"FROM {quote_identifier(trends_table)}"
            )
            df = pd.read_sql(query, conn)

            # Pre-process the data
            df['Date'] = pd.to_datetime(df['Date'])

            # Drop rows where rolling average is NaN (not enough data)
            df.dropna(subset=['Rolling_3M_Avg'], inplace=True)
            if df.empty:
                logger.warning(f"No trend data found in {trends_table} to calculate returns.")
                return

            # Get the latest date in the dataset
            latest_date = df['Date'].max()

            # Define a function to calculate absolute return between two dates
            def get_absolute_return(start_date, end_date, ticker_data):
                try:
                    # Find the value closest to the start date
                    start_value = ticker_data[ticker_data['Date'] >= start_date]['Rolling_3M_Avg'].iloc[0]

                    # Find the value closest to the end date
                    end_value = ticker_data[ticker_data['Date'] <= end_date]['Rolling_3M_Avg'].iloc[-1]

                    return ((end_value - start_value) / start_value) * 100 if start_value != 0 else 0
                except IndexError:
                    return None

            # Prepare a list to store the final returns data
            returns_data = []

            # Get unique tickers, fund names, and categories
            fund_info = df[['Ticker', 'Fund_Name', 'Category']].drop_duplicates()

            logger.info("Calculating yearly, quarterly, and YTD returns...")

            # Loop through each fund to calculate returns
            for index, row in fund_info.iterrows():
                ticker = row['Ticker']
                fund_name = row['Fund_Name']
                category = row['Category']
                ticker_df = df[df['Ticker'] == ticker].sort_values('Date').copy()

                # Initialize a dictionary for the current fund's returns
                fund_returns = {'Ticker': ticker, 'Fund_Name': fund_name, 'Category': category}

                # --- Calculate Yearly Returns (last 10 years) ---
                for year_offset in range(0, 11): # Loop from last 10 years to current
                    target_year = latest_date.year - year_offset
                    if target_year >= 2015:  # Start from 2015 as requested
                        start_of_year = datetime(target_year, 1, 1)
                        end_of_year = datetime(target_year, 12, 31)

                        # For the current year, the end date is the latest date
                        end_date = latest_date if target_year == latest_date.year else end_of_year

                        return_val = get_absolute_return(start_of_year, end_date, ticker_df)
                        if return_val is not None:
                            if target_year == latest_date.year:
                                fund_returns['YTD_Return'] = return_val
                            else:
                                fund_returns[f'Abs_Return_{target_year}'] = return_val

                # --- Calculate Quarterly Returns ---
                # Get latest quarter's start and end dates
                current_quarter = (latest_date.month - 1) // 3 + 1
                current_quarter_start = datetime(latest_date.year, (current_quarter - 1) * 3 + 1, 1)

                # QTD Return
                fund_returns['QTD_Return'] = get_absolute_return(current_quarter_start, latest_date, ticker_df)

                # Last 4 Quarters Returns
                for i in range(4):
                    end_quarter_date = latest_date - pd.DateOffset(months=3 * i)
                    start_quarter_date = end_quarter_date - pd.DateOffset(months=3)
                    
                    quarter_return_val = get_absolute_return(start_quarter_date, end_quarter_date, ticker_df)
                    if quarter_return_val is not None:
                        quarter_label = f"Q{ (end_quarter_date.month - 1) // 3 + 1}_{end_quarter_date.year}"
                        fund_returns[f'Abs_Return_{quarter_label}'] = quarter_return_val
                
                returns_data.append(fund_returns)

            # Convert the results to a DataFrame
            returns_df = pd.DataFrame(returns_data)
            
            # Reorder columns for readability
            returns_df_columns = ['Ticker', 'Fund_Name', 'Category'] + sorted([col for col in returns_df.columns if col not in ['Ticker', 'Fund_Name', 'Category']])
            returns_df = returns_df[returns_df_columns]

            # Write the new DataFrame to a SQLite table, overwriting if it exists
            logger.info(f"Saving calculated returns to '{returns_table}' table...")
            returns_df.to_sql(returns_table, conn, if_exists='replace', index=False)
            logger.info(f"Data successfully saved to '{returns_table}'.")

    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")

def vacuum_db(db_path: str) -> None:
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute('VACUUM;')
        logger.info(f"Database at {db_path} has been vacuumed successfully.")
    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")

def vaccum_db(db_path: str) -> None:
    """Backward-compatible alias for the previous misspelled function name."""
    vacuum_db(db_path)
