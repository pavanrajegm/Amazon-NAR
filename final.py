import asyncio
import os
import re
import shutil
import threading
from collections import defaultdict
import pandas as pd
import requests  # For direct image downloads
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
from playwright.async_api import async_playwright, Page, expect, TimeoutError
from werkzeug.utils import secure_filename
from datetime import datetime
import getpass
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables
load_dotenv()

# --- Flask and Socket.IO Setup ---
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
socketio = SocketIO(app, cors_allowed_origins="*")
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# --- CONFIGURATION ---
EDGE_EXECUTABLE_PATH = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
EDGE_USER_DATA_DIR = "C:/Temp/EdgeUserData"
DOWNLOAD_FOLDER = "C:/Downloads/Amazon"
BASE_URL = 'https://picassetimporter.k8s.genmills.com'
API_ENDPOINT_TEMPLATE = f"{BASE_URL}/api/v1/assets/product/{{gtin}}/json"

# DayOne Configuration
DAYONE_USERNAME = os.getenv("DAYONE_USER")
DAYONE_PASSWORD = os.getenv("DAYONE_PASS")
# Directory to store the downloaded DayOne catalog files
CATALOG_DOWNLOAD_DIR = Path("catalog_cache")
os.makedirs(CATALOG_DOWNLOAD_DIR, exist_ok=True)


# --- Global State Management ---
execution_status = {
    'running': False, 'logs': [], 'progress': 0, 'current_task': '',
    'total_items': 0, 'completed_items': 0, 'current_item_id': '', 'failed_items': [],
    'asin_lookup_progress': 0
}

class WebSocketLogger:
    def log(self, message, level='info'):
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = {'timestamp': timestamp, 'message': message, 'level': level}
        execution_status['logs'].append(log_entry)
        socketio.emit('log', log_entry)
        print(f"[{timestamp}] {message}")

logger = WebSocketLogger()

# --- NEW ASIN LOOKUP (FILE-BASED) ---

def _process_downloaded_files(core_path: Path, fresh_path: Path, logger: WebSocketLogger):
    """
    Reads downloaded Excel files separately. Normalizes UPCs by dropping the check digit
    and stripping leading zeros to create a reliable lookup key.
    """
    logger.log("Processing downloaded catalog files separately...")
    gtin_to_asins_map = defaultdict(set)

    files_to_process = [
        {'name': 'Core Catalog', 'path': core_path},
        {'name': 'Fresh Catalog', 'path': fresh_path}
    ]

    for file_info in files_to_process:
        name, path = file_info['name'], file_info['path']
        if not path.exists():
            logger.log(f"Catalog file not found, skipping: {path}", 'warning')
            continue

        logger.log(f"--- Reading {name} ---")
        try:
            df = pd.read_excel(path, dtype=str)
            df.columns = [col.strip().lower() for col in df.columns]

            required_cols = ['upc', 'asin', 'status']
            if not all(col in df.columns for col in required_cols):
                logger.log(f"Required columns missing in {name}. Skipping.", 'error')
                continue

            valid_statuses = ['Active', 'Inactive']
            df_filtered = df[df['status'].str.strip().str.title().isin(valid_statuses)]
            logger.log(f"Found {len(df_filtered)} active/inactive items in {name}.")
            
            for _, row in df_filtered.iterrows():
                upc_str = str(row.get('upc')).replace('.0', '').strip()
                asin = str(row.get('asin', '')).strip()

                # **NEW LOGIC**: Normalize by removing check digit, then stripping leading zeros.
                if len(upc_str) >= 12:
                    identifier_part = upc_str[:-1] # Remove last digit (check digit)
                    lookup_key = identifier_part.lstrip('0')
                    if lookup_key and asin:
                        gtin_to_asins_map[lookup_key].add(asin)
        except Exception as e:
            logger.log(f"Error processing {name}: {e}", "error")

    logger.log(f"Finished processing catalogs. Final map contains {len(gtin_to_asins_map)} unique identifiers.")
    return dict(gtin_to_asins_map)


async def download_and_process_catalogs_async(logger: WebSocketLogger):
    """
    Uses Playwright to log in to DayOne, download catalog files, and process them.
    """
    if not DAYONE_USERNAME or not DAYONE_PASSWORD:
        logger.log("DayOne username or password not found in .env file.", 'error')
        raise ValueError("Missing DayOne Credentials in .env file (DAYONE_USER, DAYONE_PASS)")

    core_catalog_path = CATALOG_DOWNLOAD_DIR / "core_catalog.xlsx"
    fresh_catalog_path = CATALOG_DOWNLOAD_DIR / "fresh_catalog.xlsx"

    logger.log("🔍 Starting catalog download process from DayOne Digital...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, slow_mo=50)
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()

        try:
            logger.log("Navigating to DayOne login page...")
            await page.goto("https://dayonedigital.com/portal/login")
            await page.get_by_role("textbox", name="Username*:").fill(DAYONE_USERNAME)
            await page.get_by_role("textbox", name="Password*:").fill(DAYONE_PASSWORD)
            await page.get_by_role("button", name="Submit").click()
            await page.wait_for_load_state("networkidle", timeout=60000)
            logger.log("Login successful.")

            logger.log("Downloading Core Catalog...")
            await page.get_by_role("button", name="Export").click()
            await page.get_by_role("link", name="Details").wait_for(state="visible")
            async with page.expect_download() as d1_info:
                await page.get_by_role("link", name="Details").click()
            download1 = await d1_info.value
            await download1.save_as(core_catalog_path)
            logger.log(f"✅ Core Catalog saved to {core_catalog_path}")

            logger.log("Navigating to and downloading Fresh Catalog...")
            await page.goto("https://dayonedigital.com/portal/freshcatalog")
            await page.wait_for_load_state("networkidle", timeout=60000)
            await page.get_by_role("button", name="Export").click()
            await page.get_by_role("link", name="Details").wait_for(state="visible")
            async with page.expect_download() as d2_info:
                await page.get_by_role("link", name="Details").click()
            download2 = await d2_info.value
            await download2.save_as(fresh_catalog_path)
            logger.log(f"✅ Fresh Catalog saved to {fresh_catalog_path}")

        except Exception as e:
            logger.log(f"An error occurred during catalog download: {e}", 'error')
            return {}
        finally:
            await browser.close()

    return _process_downloaded_files(core_catalog_path, fresh_catalog_path, logger)


async def lookup_asins_from_files(items_data, gtin_to_asins_map, logger: WebSocketLogger):
    """
    Looks up ASINs for given GTINs using the pre-compiled map from downloaded files.
    """
    logger.log("Matching provided GTINs against the downloaded catalog data...")
    expanded_items = []
    total_items = len(items_data)

    for i, item in enumerate(items_data):
        if not execution_status['running']: break
        
        progress = int(((i + 1) / total_items) * 100)
        execution_status['asin_lookup_progress'] = progress
        socketio.emit('asin_lookup_progress', {'progress': progress, 'current_gtin': item.get('gtin', ''), 'completed': i + 1, 'total': total_items})

        gtin_input_original = item.get('gtin')
        gtin_input_str = str(gtin_input_original).strip()
        bmn = item.get('bmn', '')

        if not gtin_input_str:
            logger.log(f"Skipping item {i + 1} - no GTIN provided", 'warning')
            continue

        # **NEW LOGIC**: Normalize the 14-digit GTIN by removing check digit and stripping leading zeros.
        if len(gtin_input_str) >= 12:
            identifier_part = gtin_input_str[:-1] # Remove last digit
            lookup_key = identifier_part.lstrip('0')
            asins = gtin_to_asins_map.get(lookup_key)
        else:
            asins = None # Input is too short to be a valid GTIN/UPC
        
        if not asins:
            logger.log(f"No active ASIN found for GTIN {gtin_input_original} (Lookup Key: {lookup_key})", 'warning')
            expanded_items.append({'gtin': gtin_input_original, 'asin': '', 'bmn': bmn, 'lookup_status': 'no_asin_found'})
        else:
            logger.log(f"Found {len(asins)} ASIN(s) for GTIN {gtin_input_original}: {', '.join(asins)}", 'info')
            for asin in asins:
                expanded_items.append({'gtin': gtin_input_original, 'asin': asin, 'bmn': bmn, 'lookup_status': 'success'})

    socketio.emit('asin_lookup_complete', {'total_processed': total_items, 'total_expanded': len(expanded_items)})
    logger.log(f"✅ ASIN lookup complete. Expanded {total_items} initial items to {len(expanded_items)} ASIN-specific items.")
    return expanded_items


# --- Helper Functions (Unchanged) ---
def create_asin_folders(asins):
    asin_folders = {}
    for asin in asins:
        folder_path = os.path.join(DOWNLOAD_FOLDER, asin)
        os.makedirs(folder_path, exist_ok=True)
        asin_folders[asin] = folder_path
    return asin_folders

def save_image_from_url(url, folder_path, file_name):
    try:
        with requests.get(url, stream=True, timeout=90) as r:
            r.raise_for_status()
            full_path = os.path.join(folder_path, file_name)
            with open(full_path, 'wb') as f:
                shutil.copyfileobj(r.raw, f)
        return True
    except requests.exceptions.RequestException as e:
        logger.log(f"Failed to download image from {url}. Error: {e}", 'error')
        return False

# --- Core Automation Logic (Unchanged) ---
async def process_single_gtin_api(page, gtin: str, asins: list):
    logger.log(f"--- Processing GTIN: {gtin} (Associated ASINs: {', '.join(asins)}) ---")
    
    asin_folders = create_asin_folders(asins)
    logger.log(f"Created/verified folders for {len(asins)} ASINs")

    api_url = API_ENDPOINT_TEMPLATE.format(gtin=gtin)
    logger.log(f"Fetching asset data from API: {api_url}")

    try:
        json_response = await page.evaluate(f'fetch("{api_url}").then(res => res.json())')
        
        if not json_response or 'assets' not in json_response:
            logger.log(f"No 'assets' key found in API response for GTIN {gtin}. Skipping.", 'warning')
            return False

        assets = json_response['assets']
        logger.log(f"Found {len(assets)} assets in API response for GTIN {gtin}.")

        carousel_assets = []
        nutrition_asset = None
        downloaded_priorities = set()

        for asset in assets:
            priority_str = asset.get('carouselPriority')
            if priority_str and priority_str.isdigit():
                priority = int(priority_str)
                if priority not in downloaded_priorities:
                    carousel_assets.append({'priority': priority, 'asset': asset})
                    downloaded_priorities.add(priority)

        max_carousel_seq = 0
        downloaded_any = False

        for item in sorted(carousel_assets, key=lambda x: x['priority']):
            priority = item['priority']
            asset = item['asset']
            max_carousel_seq = max(max_carousel_seq, priority)

            if asset.get('pimRenditions') and asset['pimRenditions'][0].get('url'):
                url = asset['pimRenditions'][0]['url']
                filename = f".PT{priority:02d}.jpeg"
                logger.log(f"  Downloading Carousel image pt{priority:02d}...")

                for asin in asins:
                    asin_filename = f"{asin}{filename}"
                    if save_image_from_url(url, asin_folders[asin], asin_filename):
                        downloaded_any = True
                        logger.log(f"  ✅ Successfully downloaded pt{priority:02d} to folder {asin_folders[asin]}.")

        if not downloaded_any:
            logger.log(f"❗️ No suitable assets were downloaded for GTIN {gtin}.", 'warning')
            return False

        return True

    except Exception as e:
        logger.log(f"❌ Critical error processing GTIN {gtin} via API: {e}", 'error')
        return False

async def process_all_items(items_to_process):
    gtin_groups = defaultdict(list)
    for item in items_to_process:
        if item.get('gtin') and item.get('asin'): gtin_groups[item['gtin']].append(item['asin'])
    execution_status.update({'total_items': len(gtin_groups), 'completed_items': 0})
    failed_gtins = set()
    async with async_playwright() as p:
        logger.log("🚀 Launching browser with persistent context for authentication...")
        try:
            browser_context = await p.chromium.launch_persistent_context(user_data_dir=EDGE_USER_DATA_DIR, executable_path=EDGE_EXECUTABLE_PATH, headless=True, accept_downloads=True, slow_mo=50, timeout=60000)
            page = browser_context.pages[0] if browser_context.pages else await browser_context.new_page()
            await page.goto(BASE_URL, wait_until="domcontentloaded")
            logger.log(f"Browser authenticated. Starting API processing loop.")
            for i, (gtin, asins) in enumerate(gtin_groups.items()):
                if not execution_status['running']: logger.log("Process stopped by user.", 'warning'); break
                item_id = f"GTIN {gtin}"; execution_status.update({'current_item_id': item_id, 'current_task': f'Processing {item_id} ({i+1}/{len(gtin_groups)})'})
                socketio.emit('progress', {'progress': int((i / len(gtin_groups)) * 100), 'task': execution_status['current_task']})
                if not await process_single_gtin_api(page, gtin, asins):
                    logger.log(f"❗️ GTIN processing failed to download assets: {gtin}", 'warning'); failed_gtins.add(gtin)
                execution_status['completed_items'] = i + 1
                socketio.emit('item_completed', {'completed': i + 1, 'total': len(gtin_groups), 'item_id': item_id, 'progress': int(((i + 1) / len(gtin_groups)) * 100)})
            logger.log("🎉 All tasks completed. Closing browser."); await browser_context.close()
        except Exception as e: logger.log(f"💥 Critical Error during browser/API processing: {e}", 'error')
    execution_status['failed_items'] = sorted(list(failed_gtins))
    socketio.emit('execution_summary', {'failed_items': execution_status['failed_items']})

def parse_file(file_path):
    try:
        df = pd.read_csv(file_path, dtype=str) if file_path.endswith('.csv') else pd.read_excel(file_path, dtype=str)
        df.columns = [col.strip().lower() for col in df.columns]
        if 'gtin' not in df.columns: logger.log(f"File missing required column: 'gtin'.", 'error'); return []
        if 'bmn' not in df.columns: df['bmn'] = ''
        df = df[['gtin', 'bmn']].dropna(subset=['gtin']).to_dict('records')
        logger.log(f"Successfully parsed {len(df)} items from file.")
        return df
    except Exception as e: logger.log(f"Failed to parse file: {e}", 'error'); return []

def run_download_task(items):
    def run_async():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            logger.log("--- STEP 1: DOWNLOADING & PROCESSING DAYONE CATALOGS ---")
            gtin_to_asins_map = loop.run_until_complete(download_and_process_catalogs_async(logger))
            if not gtin_to_asins_map: logger.log("Failed to create ASIN lookup map from catalogs. Halting.", 'error'); return
            if not execution_status['running']: logger.log("Process stopped after catalog download.", 'warning'); return
            
            logger.log("--- STEP 2: MATCHING INPUT GTINS TO CATALOG DATA ---")
            expanded_items = loop.run_until_complete(lookup_asins_from_files(items, gtin_to_asins_map, logger))
            if not execution_status['running'] or not expanded_items: logger.log("ASIN lookup stopped or yielded no results. Halting.", 'warning'); return
            
            items_with_asins = [item for item in expanded_items if item.get('asin') and item.get('lookup_status') == 'success']
            if not items_with_asins: logger.log("No items have valid ASINs for downloading.", 'error'); return
            
            logger.log(f"--- STEP 3: DOWNLOADING ASSETS FOR {len(items_with_asins)} ASIN-SPECIFIC ITEMS ---")
            loop.run_until_complete(process_all_items(items_with_asins))
        except Exception as e: logger.log(f"Execution failed in thread: {e}", 'error')
        finally:
            execution_status['running'] = False; socketio.emit('execution_complete'); loop.close()
    threading.Thread(target=run_async, daemon=True).start()

# --- Flask Routes (Unchanged) ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/execute', methods=['POST'])
def execute_download():
    if execution_status['running']: return jsonify({'error': 'Another task is already running'}), 400
    search_data = []
    if 'file' in request.files and request.files['file'].filename != '':
        file = request.files['file']; filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path); search_data = parse_file(file_path); os.remove(file_path)
    else:
        gtin = request.form.get('gtin')
        if gtin: search_data = [{'gtin': gtin, 'bmn': ''}]
        else: return jsonify({'error': 'For single entry, GTIN is required.'}), 400
    if not search_data: return jsonify({'error': 'No valid items to process.'}), 400
    execution_status.update({'running': True, 'logs': [], 'progress': 0, 'current_task': 'Starting catalog download...','total_items': 0, 'completed_items': 0, 'current_item_id': '', 'failed_items': [], 'asin_lookup_progress': 0})
    run_download_task(search_data)
    return jsonify({'success': True, 'message': 'Process started. Downloading DayOne catalogs first.', 'initial_items': len(search_data)})

@app.route('/stop', methods=['POST'])
def stop_execution():
    if execution_status['running']:
        execution_status['running'] = False; logger.log("Stop signal received. Process will halt shortly.", 'warning')
        return jsonify({'success': True, 'message': 'Stop signal sent.'})
    return jsonify({'error': 'No active process to stop.'}), 400

@socketio.on('connect')
def handle_connect(): emit('status_update', execution_status)

if __name__ == '__main__':
    socketio.run(app, debug=False, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)