import datetime
import json
import threading
import time
import orthanc


def clean_old_studies():
    orthanc.LogWarning("Running daily cleaner: targeting studies older than 4 hours...")
    cutoff_time = datetime.datetime.now() - datetime.timedelta(hours=4)
    deleted_count = 0

    try:
        studies = json.loads(orthanc.RestApiGet("/studies?expand=true"))

        for study in studies:
            last_update_str = study.get("LastUpdate", "")

            if last_update_str:
                try:
                    last_update = datetime.datetime.strptime(last_update_str, "%Y%m%dT%H%M%S")

                    if last_update < cutoff_time:
                        orthanc.RestApiDelete(f"/studies/{study['ID']}")
                        deleted_count += 1
                        orthanc.LogWarning(f"Cleaner: Deleted study {study['ID']} (Last updated: {last_update_str})")
                except ValueError:
                    pass

        orthanc.LogWarning(f"Cleaner finished! Deleted {deleted_count} old studies.")

    except Exception as e:
        orthanc.LogWarning(f"Cleaner encountered an error: {e}")


def _run_scheduler():
    while True:
        now = datetime.datetime.now()
        target = now.replace(hour=2, minute=30, second=0, microsecond=0)
        if now >= target:
            target += datetime.timedelta(days=1)
        sleep_seconds = (target - now).total_seconds()
        orthanc.LogWarning(f"Native scheduler: Sleeping for {sleep_seconds:.0f} seconds until next cleanup at {target.strftime('%Y-%m-%d %H:%M:%S')}.")
        time.sleep(sleep_seconds)
        clean_old_studies()


def start():
    thread = threading.Thread(target=_run_scheduler, daemon=True)
    thread.start()
