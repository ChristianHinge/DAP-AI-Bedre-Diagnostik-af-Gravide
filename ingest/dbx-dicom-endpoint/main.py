from fastapi import FastAPI, UploadFile, File
from databricks.sdk import WorkspaceClient
import os
import secrets

app = FastAPI()
_workspace = WorkspaceClient()  # auto-authenticates inside Databricks

UC_VOLUME_PATH = os.environ["UC_VOLUME_PATH"]
PROCESSING_JOB_ID = int(os.environ.get("PROCESSING_JOB_ID", 0))


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/upload-study-zip")
async def upload_study_zip(file: UploadFile = File(...)):
    study_id = secrets.token_hex(5)
    zip_path = f"{UC_VOLUME_PATH}/{study_id}/imgs.zip"

    # Stream the upload straight into the volume via the Files API.
    # file.file is a SpooledTemporaryFile (BinaryIO), so no need to read into memory.
    _workspace.files.upload(zip_path, file.file, overwrite=True)

    run = _workspace.jobs.run_now(
        job_id=PROCESSING_JOB_ID,
        job_parameters={"study_id": study_id},
    )
    
    return {
        "status": "processing_started",
        "study_id": study_id,
        "databricks_run_id": run.response.run_id,
    }