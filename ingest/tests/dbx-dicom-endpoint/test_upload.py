import os

import dotenv
import requests
from databricks.sdk import WorkspaceClient

ENDPOINT_URL = "https://dbx-dicom-endpoint-3055293005770013.13.azure.databricksapps.com"
dotenv.load_dotenv()

ZIP_PATH = os.path.join(os.path.dirname(__file__), "package.zip")

wc = WorkspaceClient()
token = wc.config.authenticate()["Authorization"].split(" ")[1]

with open(ZIP_PATH, "rb") as f:
    resp = requests.post(
        f"{ENDPOINT_URL}/api/upload-study-zip",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("package.zip", f, "application/zip")},
    )

print(resp.status_code)
print(resp)
