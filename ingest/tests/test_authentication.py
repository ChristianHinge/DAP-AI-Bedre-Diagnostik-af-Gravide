import requests
from databricks.sdk import WorkspaceClient
import requests
import dotenv
import os
from dicomweb_client.api import DICOMwebClient
from pydicom import dcmread

dotenv.load_dotenv()

WORKSPACE_URL = "https://adb-7405613569486293.13.azuredatabricks.net/"
CLIENT_ID="1978d310-542a-4e02-b374-fb23b3a58e49"
TENANT_ID="421c9c18-32c7-4250-84e0-c1626f963b1f"
CLIENT_SECRET=os.environ["CLIENT_SECRET"]
APP_NAME      = "pixels-dicomweb-gateway"


#Fungerer 
wc = WorkspaceClient(
    host                = WORKSPACE_URL,
    azure_client_id     = CLIENT_ID,
    azure_client_secret = CLIENT_SECRET,
    azure_tenant_id     = TENANT_ID,
)


me = wc.current_user.me()
print(me.user_name, me.active)

app         = wc.apps.get(APP_NAME)
entra_token = wc.config.authenticate()["Authorization"].split(" ", 1)[1]

#print(wc.config.authenticate())

DICOM_FILES   = ["/homes/hinge/Projects/DAP-AI-Bedre-Diagnostik-af-Gravide/ingest/tests/ultrasound_simulation/sample_data/dicom/MPPS1_001.dcm"]

# --- STOW-RS ---
client = DICOMwebClient(
    url     = f"{app.url}/api/dicomweb",
    headers = {"Authorization": f"Bearer {entra_token}"},
)




datasets = [dcmread(p) for p in DICOM_FILES]
response = client.store_instances(datasets)
print(response)   # DICOM dataset summarising what was stored






#Fejler
r = requests.post(
    f"{WORKSPACE_URL}/oidc/v1/token",
    data={
        "client_id":          CLIENT_ID,        # SP application/client ID
        "subject_token":      entra_token,      # JWT from login.microsoftonline.com
        "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
        "grant_type":         "urn:ietf:params:oauth:grant-type:token-exchange",
        "scope":              "all-apis",
    },
)
print(r.status_code)
print(r.text)
