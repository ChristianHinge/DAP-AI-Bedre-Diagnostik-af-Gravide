#!/usr/bin/env python3
"""
Simulates: 
- Start study (MPPS CREATE)
- Capture and send 5 DICOM images
- End study (MPPS SET Complete)
- End study dupplicate (MPPS SET Complete)
- Reopen study (MPPS CREATE)
- Capture and send 2 DICOM images
- End study (MPPS SET Complete)
"""

from collections import defaultdict
from pathlib import Path

import time

import pydicom
from pynetdicom import AE
from pynetdicom.presentation import build_context
from pynetdicom.sop_class import ModalityPerformedProcedureStep

HOST, PORT, AET = "localhost", 4242, "ORTHANC"
MPPS_PORT  = 4243
MPPS_DIR   = Path(__file__).parent / "sample_data" / "mpps"
DICOM_DIR  = Path(__file__).parent / "sample_data" / "dicom"

ae = AE(ae_title="US_SIM")


def mpps_assoc():
    a = ae.associate(HOST, MPPS_PORT, ae_title=AET,
                     contexts=[build_context(ModalityPerformedProcedureStep)])
    if not a.is_established:
        raise ConnectionError(f"MPPS association failed")
    return a


def uid_from(path: Path, op: str) -> str:
    return path.stem.split(f"_{op}_", 1)[1]


def sessions():
    creates = {uid_from(f, "CREATE"): f for f in sorted(MPPS_DIR.glob("*_CREATE_*.dcm"))}
    sets = defaultdict(list)
    for f in sorted(MPPS_DIR.glob("*_SET_*.dcm")):
        sets[uid_from(f, "SET")].append(f)
    return [(cf, sets[uid]) for uid, cf in sorted(creates.items(), key=lambda x: x[1].name)
            if sets[uid]]


def run(create_file: Path, set_files: list[Path], index: dict) -> None:
    uid = uid_from(create_file, "CREATE")
    print(f"\n=== {uid} ===")

    a = mpps_assoc()
    a.send_n_create(pydicom.dcmread(create_file), ModalityPerformedProcedureStep, uid)
    a.release()
    print("N-CREATE OK")

    last_set = pydicom.dcmread(set_files[-1])
    sop_uids = [str(i.ReferencedSOPInstanceUID)
                for s in last_set.PerformedSeriesSequence
                for i in s.ReferencedImageSequence]
    datasets = [pydicom.dcmread(index[u]) for u in sop_uids if u in index]
    if datasets:
        contexts = list({build_context(ds.SOPClassUID, ds.file_meta.TransferSyntaxUID) for ds in datasets})
        sa = ae.associate(HOST, PORT, ae_title=AET, contexts=contexts)
        if not sa.is_established:
            raise ConnectionError(f"C-STORE association failed")
        for ds in datasets:
            status = sa.send_c_store(ds)
            print(f"C-STORE {'OK' if status and status.Status == 0 else 'FAILED'}  {ds.filename}")
        sa.release()

    for i, sf in enumerate(set_files):
        set_ds = pydicom.dcmread(sf)
        a = mpps_assoc()
        a.send_n_set(set_ds, ModalityPerformedProcedureStep, uid)
        a.release()
        print(f"N-SET OK  ({sf.name})")
        if i == 1:
            print("Sleeping 5s after 2nd N-SET...")
            time.sleep(5)


if __name__ == "__main__":
    index = {str(pydicom.dcmread(f, stop_before_pixels=True).SOPInstanceUID): f
             for f in DICOM_DIR.glob("*.dcm")}
    print(f"Indexed {len(index)} image(s)")
    for create_file, set_files in sessions():
        run(create_file, set_files, index)
