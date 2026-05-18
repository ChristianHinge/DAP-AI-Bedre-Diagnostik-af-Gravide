import io
import json
import os
import time
import datetime
import hashlib
import secrets
import threading
import traceback
import orthanc
from databricks.sdk import WorkspaceClient
from pynetdicom import AE, evt
from pynetdicom.sop_class import ModalityPerformedProcedureStep, Verification

_server = None
MPPS_LOG_DIR = os.environ["MPPS_LOG_DIR"]
DATABRICKS_HOST = os.environ["DATABRICKS_HOST"]
PROCESSING_JOB_ID = int(os.environ["PROCESSING_JOB_ID"])
DATABRICKS_STORE_VOLUME_ROOT = os.environ["DATABRICKS_STORE_VOLUME_ROOT"]

_wc = WorkspaceClient(
    host=DATABRICKS_HOST,
    client_id=os.environ["DATABRICKS_CLIENT_ID"],
    client_secret=os.environ["DATABRICKS_CLIENT_SECRET"],
)

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

    threading.Thread(
        target=_wait_and_forward,
        args=(sop_uids,),
        daemon=True,
        name="mpps-wait-forward",
    ).start()


def _wait_and_forward(sop_uids, max_retries=10, delay=2.0):
    orthanc.LogWarning(f"MPPS: waiting for {len(sop_uids)} instances to arrive")
    try:
        for attempt in range(max_retries):
            missing = {
                uid for uid in sop_uids
                if not json.loads(orthanc.RestApiPost(
                    "/tools/find",
                    json.dumps({"Level": "Instance", "Query": {"SOPInstanceUID": uid}})
                ))
            }
            if not missing:
                break
            orthanc.LogWarning(f"MPPS: {len(missing)}/{len(sop_uids)} instances missing (attempt {attempt + 1}/{max_retries})")
            time.sleep(delay)
        else:
            orthanc.LogError(f"MPPS: timed out, {len(missing)} instances never arrived")
            return

        orthanc.LogWarning(f"MPPS: all {len(sop_uids)} instances present, resolving study")
        study_uid = _get_study_uid(next(iter(sop_uids)))
        if not study_uid:
            return

        known = _study_state.setdefault(study_uid, set())
        if not (sop_uids - known):
            orthanc.LogWarning(f"MPPS: study {study_uid} already forwarded, skipping")
            return

        _forward_study(study_uid)
        known.update(sop_uids)
        orthanc.LogWarning(f"MPPS: forward queued for study {study_uid}")

    except Exception:
        orthanc.LogError(f"MPPS forward failed:\n{traceback.format_exc()}")


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


def _build_study_dir(study_uid):
    instance_ids = json.loads(orthanc.RestApiPost(
        "/tools/find",
        json.dumps({"Level": "Instance", "Query": {"StudyInstanceUID": study_uid}})
    ))
    tags = json.loads(orthanc.RestApiGet(f"/instances/{instance_ids[0]}/tags?simplify"))
    patient_id = tags.get("PatientID", "unknown")

    patient_hash = hashlib.sha256(patient_id.encode()).hexdigest()[:10]
    study_hash = hashlib.sha256(study_uid.encode()).hexdigest()[:10]
    random_part = secrets.token_hex(5)
    study_dir = f"{DATABRICKS_STORE_VOLUME_ROOT}/patient-{patient_hash}/study-{study_hash}/files-{random_part}"
    return study_dir


def _upload_and_trigger(instance_ids, study_dir):
    try:
        for i, iid in enumerate(instance_ids):
            dicom_bytes = bytes(orthanc.RestApiGet(f"/instances/{iid}/file"))
            _wc.files.upload(f"{study_dir}/file-{i:03d}.dcm", io.BytesIO(dicom_bytes), overwrite=True)
        _wc.jobs.run_now(
            job_id=PROCESSING_JOB_ID,
            job_parameters={"volume_input_dir": study_dir},
        )
        orthanc.LogWarning(f"MPPS upload complete: {study_dir}")
    except Exception as e:
        orthanc.LogError(f"MPPS upload failed for {study_dir}: {e}")


def _forward_study(study_uid):
    study_dir = _build_study_dir(study_uid)
    instance_ids = json.loads(orthanc.RestApiPost(
        "/tools/find",
        json.dumps({"Level": "Instance", "Query": {"StudyInstanceUID": study_uid}})
    ))
    threading.Thread(
        target=_upload_and_trigger,
        args=(instance_ids, study_dir),
        daemon=True,
        name=f"dbx-upload-{study_uid[:8]}",
    ).start()


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
