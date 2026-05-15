import io
import json
import os
import time
import datetime
import zipfile
import requests
import orthanc
from databricks.sdk import WorkspaceClient
from pynetdicom import AE, evt
from pynetdicom.sop_class import ModalityPerformedProcedureStep, Verification

_server = None
DBX_ENDPOINT_URL = os.environ["DBX_ENDPOINT_URL"]
MPPS_LOG_DIR = os.environ["MPPS_LOG_DIR"]
DATABRICKS_HOST = os.environ["DATABRICKS_HOST"]

_workspace_client = WorkspaceClient(
    host=DATABRICKS_HOST,
    azure_client_id=os.environ["AZURE_CLIENT_ID"],
    azure_client_secret=os.environ["AZURE_CLIENT_SECRET"],
    azure_tenant_id=os.environ["AZURE_TENANT_ID"],
)
_token_cache = {"value": None, "expires_at": 0.0}

os.makedirs(MPPS_LOG_DIR, exist_ok=True)

# StudyInstanceUID → set of SOP UIDs seen in prior COMPLETED messages
_study_state: dict[str, set] = {}


def _dump_mpps(op, uid, ds):
    try:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = os.path.join(MPPS_LOG_DIR, f"{ts}_{op}_{uid}.txt")
        with open(path, "w") as f:
            f.write(str(ds))
    except Exception as e:
        orthanc.LogWarning(f"MPPS dump failed: {e}")


def handle_echo(event):
    return 0x0000


def handle_create(event):
    return 0x0000, None


def handle_set(event):
    attr = event.attribute_list
    status = getattr(attr, "PerformedProcedureStepStatus", "").upper()

    _dump_mpps("SET", event.request.RequestedSOPInstanceUID, attr)
    orthanc.LogWarning(f"MPPS N-SET: Status={status}")

    if status == "COMPLETED":
        _on_completed(attr)

    return 0x0000, None


def _on_completed(attr):
    sop_uids = {
        str(ref.ReferencedSOPInstanceUID)
        for series in getattr(attr, "PerformedSeriesSequence", [])
        for ref in getattr(series, "ReferencedImageSequence", [])
        if hasattr(ref, "ReferencedSOPInstanceUID")
    }
    if not sop_uids:
        return

    try:
        study_uid = _get_study_uid(next(iter(sop_uids)))
        if not study_uid:
            return

        known = _study_state.setdefault(study_uid, set())
        if not (sop_uids - known):
            return

        _forward_study(study_uid)
        known.update(sop_uids)
        orthanc.LogWarning(f"MPPS forward: study {study_uid}")

    except Exception as e:
        orthanc.LogWarning(f"MPPS forward failed: {e}")


def _get_study_uid(sop_uid):
    results = json.loads(orthanc.RestApiPost(
        "/tools/find",
        json.dumps({"Level": "Instance", "Query": {"SOPInstanceUID": sop_uid}})
    ))
    if not results:
        orthanc.LogWarning(f"MPPS forward: {sop_uid} not in Orthanc yet")
        return None
    tags = json.loads(orthanc.RestApiGet(f"/instances/{results[0]}/tags?simplify"))
    return tags["StudyInstanceUID"]


def _get_token():
    if time.time() < _token_cache["expires_at"] - 30:
        return _token_cache["value"]
    entra_token = _workspace_client.config.authenticate()["Authorization"].split(" ", 1)[1]
    resp = requests.post(
        f"{DATABRICKS_HOST}/oidc/v1/token",
        data={
            "client_id":          os.environ["AZURE_CLIENT_ID"],
            "subject_token":      entra_token,
            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
            "grant_type":         "urn:ietf:params:oauth:grant-type:token-exchange",
            "scope":              "all-apis",
        },
    )
    resp.raise_for_status()
    payload = resp.json()
    _token_cache["value"] = payload["access_token"]
    _token_cache["expires_at"] = time.time() + payload["expires_in"]
    return _token_cache["value"]


def _forward_study(study_uid):
    instance_ids = json.loads(orthanc.RestApiPost(
        "/tools/find",
        json.dumps({"Level": "Instance", "Query": {"StudyInstanceUID": study_uid}})
    ))

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for iid in instance_ids:
            z.writestr(f"{iid}.dcm", bytes(orthanc.RestApiGet(f"/instances/{iid}/file")))
    zip_buffer.seek(0)

    resp = requests.post(
        f"{DBX_ENDPOINT_URL}/api/upload-study-zip",
        headers={"Authorization": f"Bearer {_get_token()}"},
        files={"file": ("study.zip", zip_buffer, "application/zip")},
    )
    resp.raise_for_status()

def start():
    global _server
    aet = "MPPS_SCP"
    port = 4243

    ae = AE(ae_title=aet)
    ae.add_supported_context(ModalityPerformedProcedureStep)
    ae.add_supported_context(Verification)
    _server = ae.start_server(("0.0.0.0", port), block=False, evt_handlers=[
        (evt.EVT_N_CREATE, handle_create),
        (evt.EVT_N_SET, handle_set),
        (evt.EVT_C_ECHO, handle_echo),
    ])
    orthanc.LogWarning(f"MPPS SCP started on port {port} with AET '{aet}'")


def stop():
    global _server
    if _server is not None:
        _server.shutdown()
        orthanc.LogWarning("MPPS SCP stopped")
