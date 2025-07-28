# Amazon Asset Downloader

## Overview
The Amazon Asset Downloader is a Flask-based web application designed to automate the process of downloading and managing product assets from the DayOne Digital platform. It provides a user-friendly interface for uploading GTINs or catalog files, matching them with ASINs, and downloading associated assets such as images and metadata.

## Features
- **GTIN to ASIN Mapping**: Automatically matches GTINs to ASINs using catalog files.
- **DayOne Digital Integration**: Logs into the DayOne Digital platform to download core and fresh catalog files.
- **Asset Downloading**: Fetches product assets such as carousel images and nutrition information via API.
- **Real-Time Progress Updates**: Displays progress and logs in real-time using WebSocket.
- **File Upload Support**: Allows users to upload GTIN files in CSV or Excel format.
- **Error Handling**: Provides detailed logs for troubleshooting.

## Prerequisites
- Python 3.8 or higher
- DayOne Digital credentials (stored in a `.env` file)

## Installation
1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd Amazon-final
   ```

2. Create a virtual environment and activate it:
   ```bash
   python -m venv venv
   .\venv\Scripts\activate  # On Windows
   source venv/bin/activate   # On macOS/Linux
   ```

3. Install the required Python packages:
   ```bash
   pip install -r requirements.txt
   ```

4. Install Playwright and its dependencies:
   ```bash
   playwright install
   ```

5. Create a `.env` file in the root directory and add your DayOne Digital credentials:
   ```env
   DAYONE_USER=<your-username>
   DAYONE_PASS=<your-password>
   ```

6. Ensure the following directories exist:
   - `uploads/` for file uploads
   - `catalog_cache/` for downloaded catalog files
   - `static/img/` for static assets

## Usage
1. Start the Flask application:
   ```bash
   python final.py
   ```

2. Open your browser and navigate to `http://localhost:5000`.

3. Upload a GTIN file or enter a single GTIN to start the process.

4. Monitor the progress and logs in real-time.

## Project Structure
```
Amazon-final/
├── final.py                # Main application file
├── templates/
│   └── index.html         # Frontend HTML template
├── static/
│   └── img/               # Static images
├── uploads/               # Uploaded files
├── catalog_cache/         # Downloaded catalog files
├── requirements.txt       # Python dependencies
├── .env                   # Environment variables
└── README.md              # Project documentation
```

## Technologies Used
- **Backend**: Flask, Flask-SocketIO
- **Frontend**: HTML, CSS, JavaScript
- **Automation**: Playwright
- **Data Processing**: Pandas

## Known Issues
- Ensure the DayOne Digital credentials are valid and up-to-date.
- The application requires Microsoft Edge to be installed for Playwright.

## Contributing
Contributions are welcome! Please follow these steps:
1. Fork the repository.
2. Create a new branch for your feature or bug fix.
3. Commit your changes and push them to your fork.
4. Submit a pull request with a detailed description of your changes.

