import os

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "ndc_db")
DB_USER = os.getenv("DB_USER", "ndc_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "ndc_pass")

OPENFDA_API_KEY = os.getenv("OPENFDA_API_KEY", "")

NDC_ZIP_URL = "https://www.accessdata.fda.gov/cder/ndctext.zip"
OPENFDA_LABEL_URL = "https://api.fda.gov/drug/label.json"

# Pipeline tuning
BATCH_SIZE = 2000           # Rows per DB batch insert
OPENFDA_BATCH_SIZE = 50     # NDCs per openFDA API call
OPENFDA_REQUEST_DELAY = 0.27 # Seconds between API calls (~220 req/min with API key)
OPENFDA_MAX_RETRIES = 3
TMP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tmp")
