import orthanc
import plugin_mpps
import plugin_nightly_cleanup

def OnChange(changeType, level, resourceId):
    try:
        if changeType == orthanc.ChangeType.ORTHANC_STARTED:
            plugin_mpps.start()
            plugin_nightly_cleanup.start()
        elif changeType == orthanc.ChangeType.ORTHANC_STOPPED:
            plugin_mpps.stop()
    except Exception as e:
        orthanc.LogWarning(f"Plugin error: {str(e)}")


orthanc.RegisterOnChangeCallback(OnChange)
