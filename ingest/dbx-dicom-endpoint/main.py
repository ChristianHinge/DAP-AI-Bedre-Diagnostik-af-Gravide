from fastapi import FastAPI, UploadFile, File
from databricks.sdk import WorkspaceClient
import os
import secrets

app = FastAPI()
_workspace = WorkspaceClient()  # auto-authenticates inside Databricks

UC_VOLUME_PATH = os.environ["UC_VOLUME_PATH"]
PROCESSING_JOB_ID = int(os.environ.get("PROCESSING_JOB_ID",0))


@app.post("/api/upload-study-zip")
async def upload_study_zip(file: UploadFile = File(...)):
    study_id = secrets.token_hex(5)
    file_bytes = await file.read()
    zip_path = f"{UC_VOLUME_PATH}/{study_id}.zip"
    with open(zip_path, "wb") as f:
        f.write(file_bytes)

    run = _workspace.jobs.run_now(
        job_id=PROCESSING_JOB_ID,
        job_parameters={"study_id": study_id},
    )
    return {
        "status": "processing_started",
        "study_id": study_id,
        "databricks_run_id": run.response.run_id,
    }
