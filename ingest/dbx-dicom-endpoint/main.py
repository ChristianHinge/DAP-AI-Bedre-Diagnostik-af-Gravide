from fastapi import FastAPI, UploadFile, File
from databricks.sdk import WorkspaceClient
import zipfile
import io
import os
import secrets

app = FastAPI()
_workspace = WorkspaceClient()  # auto-authenticates inside Databricks

UC_VOLUME_PATH = os.environ.get("UC_VOLUME_PATH", "/Volumes/pixels/dicom/files")
PROCESSING_JOB_ID = int(os.environ.get("PROCESSING_JOB_ID",0))


@app.post("/api/upload-study-zip")
async def upload_study_zip(file: UploadFile = File(...)):
    study_id = secrets.token_hex(5)
    file_bytes = await file.read()
    study_dir = f"{UC_VOLUME_PATH}"
    
    os.makedirs(study_dir, exist_ok=True)

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
