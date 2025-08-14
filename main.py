import os
import json
import logging
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv
import time

# Import functions from your modules
from modules import (
    get_last_date_for_fund,
    save_master_data_to_db,
    save_nav_data_to_db,
    get_fund_info,
    get_price_data,
    delete_records_from_date,
    calculate_and_save_trends,
    calculate_and_store_returns
)

# Load environment variables from .env file
load_dotenv()

# --- Configuration and Class Setup -------------------------------------------

class FundCollector:
    """
    Orchestrates the process of fetching and storing fund data.
    """
    def __init__(self):
        # Load configurations from .env
        self.db_path: str = os.getenv("DB_PATH", "db/eqt.db")
        # Updated table names
        self.master_table_name: str = os.getenv("MF_TABLE_NAME", "mf")
        self.nav_table_name: str = os.getenv("NAV_TABLE_NAME", "mf_nav")
        self.trends_table_name: str = os.getenv("TRENDS_TABLE_NAME", "mf_nav_trends")
        self.returns_table_name: str = os.getenv("RETURNS_TABLE_NAME", "mf_nav_returns") # Added new table name
        self.log_path: str = os.getenv("LOG_PATH", "logs/app.log")
        # Correcting the file path based on user input
        self.funds_file_path: str = os.getenv("FUNDS_FILE_PATH", "mutual_funds.json")

        # Derived configurations
        self.start_date: str = self._get_full_history_start_date()
        self.end_date: str = datetime.now().strftime("%Y-%m-%d")
        
        # Setup logging first so other methods can use it
        self._setup_logging()

        # Load funds after logging is set up
        self.funds: dict = self._load_funds_from_json(self.funds_file_path)

    def _setup_logging(self):
        """Configures the logging for the application."""
        log_directory = os.path.dirname(self.log_path)
        os.makedirs(log_directory, exist_ok=True)
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.log_path),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

    def _get_full_history_start_date(self) -> str:
        """Calculates the start date for full history pull based on .env values."""
        years = int(os.getenv("START_DATE_YEARS", "10"))
        days = int(os.getenv("START_DATE_DAYS", "45"))
        return (datetime.now() - timedelta(days=365 * years + days)).strftime("%Y-%m-%d")

    def _load_funds_from_json(self, file_path: str) -> dict:
        """Loads fund data from a JSON file, with a check for file existence."""
        if not os.path.exists(file_path):
            self.logger.error(f"Funds JSON file not found at {file_path}")
            return {}
        try:
            with open(file_path, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            self.logger.error(f"Error loading funds JSON from {file_path}: {e}")
            return {}

    def run(self) -> None:
        """Main orchestration method to fetch and save fund data."""
        db_directory = os.path.dirname(self.db_path)
        os.makedirs(db_directory, exist_ok=True)
        
        # New lists to hold the separated dataframes
        all_master_data_to_save: list[pd.DataFrame] = []
        all_nav_data_to_save: list[pd.DataFrame] = []

        self.logger.info("Starting data collection process...")
        
        for category, funds_dict in self.funds.items():
            self.logger.info(f"--- Processing Category: {category} ---")
            for fund_name, ticker in funds_dict.items():
                self.logger.info(f"Processing: {fund_name} ({ticker})")
                
                # We now check against the new NAV table
                last_date = get_last_date_for_fund(ticker, self.db_path, self.nav_table_name)
                
                if last_date:
                    fetch_start_date = pd.to_datetime(last_date).strftime("%Y-%m-%d")
                    if fetch_start_date >= self.end_date:
                        self.logger.info("  Data is up to date, skipping.")
                        continue
                    
                    self.logger.info(f"  Last data: {last_date}, fetching from: {fetch_start_date}")
                    # Delete data from the last recorded date onwards to prepare for fresh data
                    delete_records_from_date(ticker, fetch_start_date, self.db_path, self.nav_table_name)
                else:
                    fetch_start_date = self.start_date
                    self.logger.info(f"  No existing data, fetching full history from: {fetch_start_date}")

                fund_info = get_fund_info(ticker)
                price_data = get_price_data(ticker, fetch_start_date, self.end_date)
                
                # Prepare and append the master data
                master_data = {
                    'Fund_Name': [fund_name],
                    'Ticker': [ticker],
                    'Category': [category],
                    **{key: [value] for key, value in fund_info.items() if key not in ['Expense_Ratio', 'Net_Assets', 'PE_Ratio', 'Holdings']}
                }
                master_df = pd.DataFrame(master_data)
                all_master_data_to_save.append(master_df)

                if not price_data.empty:
                    # Prepare and append the NAV data
                    # Removed 'Adj_Close' as it's not present when auto_adjust is True
                    nav_data_columns = ['Date', 'Close', 'Open', 'High', 'Low', 'Volume']
                    nav_df = price_data[nav_data_columns].copy()
                    nav_df['Ticker'] = ticker
                    nav_df['Fund_Name'] = fund_name
                    nav_df['Category'] = category
                    all_nav_data_to_save.append(nav_df)
                    
                # time.sleep(0.5) # Uncomment to prevent API rate limiting

        # Define columns for empty dataframes to prevent syntax errors on first run
        master_cols = ['Fund_Name', 'Ticker', 'Category', 'Fund_Family', 'Long_Name', 'Fund_Type', 'Currency',
                        'Market_Cap', 'NAV', 'Fund_Manager', 'Management_Company', 'Inception_Date', 'Yield',
                        'Beta', 'Investment_Strategy', 'Category_Name', 'Fund_Description']
        nav_cols = ['Date', 'Close', 'Open', 'High', 'Low', 'Volume', 'Ticker', 'Fund_Name', 'Category']

        # Always create the master table, even if empty
        final_master_df = pd.concat(all_master_data_to_save, ignore_index=True) if all_master_data_to_save else pd.DataFrame(columns=master_cols)
        save_master_data_to_db(final_master_df, self.db_path, self.master_table_name)

        # Always create the NAV table, even if empty
        final_nav_df = pd.concat(all_nav_data_to_save, ignore_index=True) if all_nav_data_to_save else pd.DataFrame(columns=nav_cols)
        save_nav_data_to_db(final_nav_df, self.db_path, self.nav_table_name)
        
        
if __name__ == '__main__':
    collector = FundCollector()
    # Download nav data
    collector.run()
    # Calculate the trends
    calculate_and_save_trends(
        db_path=collector.db_path,
        nav_table=collector.nav_table_name,
        master_table=collector.master_table_name,
        trends_table=collector.trends_table_name
    )
    # Calculate the returns 
    calculate_and_store_returns(
        db_path=collector.db_path,
        trends_table=collector.trends_table_name,
        returns_table=collector.returns_table_name
    )
