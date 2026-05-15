from fastapi import FastAPI, UploadFile, File
from databricks.sdk import WorkspaceClient
import zipfile
import io
import os
import uuid

app = FastAPI()
_workspace = WorkspaceClient()  # auto-authenticates inside Databricks

UC_VOLUME_PATH = os.environ.get("UC_VOLUME_PATH", "/Volumes/main/medical_data/dicom_storage")
PROCESSING_JOB_ID = int(os.environ["PROCESSING_JOB_ID"])


@app.post("/api/upload-study-zip")
async def upload_study_zip(file: UploadFile = File(...)):
    study_id = str(uuid.uuid4())
    study_dir = f"{UC_VOLUME_PATH}/{study_id}"
    os.makedirs(study_dir, exist_ok=True)

    file_bytes = await file.read()
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
        z.extractall(study_dir)

    run = _workspace.jobs.run_now(
        job_id=PROCESSING_JOB_ID,
        job_parameters={"study_id": study_id},
    )
    return {
        "status": "processing_started",
        "study_id": study_id,
        "databricks_run_id": run.response.run_id,
    }
