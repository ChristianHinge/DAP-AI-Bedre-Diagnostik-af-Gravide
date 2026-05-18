import os
import logging
import pydicom
from copy import deepcopy
import numpy as np
from PIL import Image
from pydicom.uid import ExplicitVRLittleEndian
import time
import cv2
import databricks

logger = logging.getLogger(__name__)


BBOX_COORDS_CONFIGS = {
    '1202' : ((0.0, 0.0), (0.15, 1.0)),
    '2202': ((0.0, 0.0), (0.15, 1.0)),
    '2300': ((0.0, 0.0), (0.15, 1.0)),
    'Antares' : ((0.0, 0.0), (0.08, 1.0)),
    'ARIETTA 850' : ((0.0, 0.0), (0.08, 1.0)),
    'EPIQ 7G' : ((0.0, 0.0), (0.0, 0.0)), 
    'iU22' : ((0.0, 0.0), (0.1, 1.0)),
    'LOGIQ5' : ((0.0, 0.0), (0.09, 1.0)),
    'LOGIQ7' : ((0.0, 0.0), (0.09, 1.0)),
    'LOGIQ9' : ((0.0, 0.0), (0.09, 1.0)),
    'S2000' : ((0.0, 0.0), (0.08, 1.0)),
    'S3000' : ((0.0, 0.0), (0.08, 1.0)),
    'SEQUOIA' : ((0.0, 0.0), (0.07, 1.0)),
    'SSD-ALPHA7' : ((0.0, 0.0), (0.08, 1.0)), # could use lower bbox
    'SSD-ALPHA10' : ((0.0, 0.0), (0.08, 1.0)), # could use lower bbox
    'TUS-A500' : ((0.0, 0.0), (0.085, 1.0)),
    'TUS-AI800' : ((0.0, 0.0), (0.08, 1.0)),
    'TUS-AI900' : ((0.0, 0.0), (0.08, 1.0)),
    'V730' : ((0.0, 0.0), (0.085, 1.0)),
    'V830' : ((0.0, 0.0), (0.1, 1.0)), # some images are cropped in, and the bbox for those images do not match the native scan res :<
    'Voluson E6' : ((0.0, 0.0), (0.08, 1.0)),
    'Voluson E8' : ((0.0, 0.0), (0.08, 1.0)),
    'Voluson E10' : ((0.0, 0.0), (0.08, 1.0)), #greenout instead of blackout, prob due to ybr
    'Voluson Expert 22' : ((0.0, 0.0), (0.065, 1.0)), # same cropped in issues
    'Voluson P8' : ((0.0, 0.0), (0.08, 1.0)),
    'Voluson S' : ((0.0, 0.0), (0.1, 1.0)),
    'Voluson S6' : ((0.0, 0.0), (0.08, 1.0)),
    'Voluson S8' : ((0.0, 0.0), (0.08, 1.0)),
    'Voluson S10 Expert' : ((0.0, 0.0), (0.08, 1.0)),
    'Voluson SWIFT' : ((0.0, 0.0), (0.08, 1.0)),
    'Z_ONE': ((0.0, 0.0), (0.065, 1.0)) # has weird info dcm's
}
# WORKING_MODEL = '2300'

def get_image_from_pixels(photometric, planar, pixel_array):
    try:
        # Validate pixel array
        if pixel_array is None or pixel_array.size == 0:
            logger.warning("Invalid or empty pixel array")
            return None
            
        if photometric == "RGB":
            # fix planar configuration if needed
            if planar == 1:
                pixel_array = np.moveaxis(pixel_array, 0, -1)

            # validate shape (must be HxWx3)
            if pixel_array.ndim != 3 or pixel_array.shape[-1] != 3:
                raise ValueError(f"Unexpected RGB shape: {pixel_array.shape}")
            
            if pixel_array.dtype != np.uint8:
                pixel_array = np.clip(pixel_array, 0, 255).astype(np.uint8)

        elif photometric in ["YBR_FULL_422", "YBR_FULL", "YBR_PARTIAL_422", "YBR_PARTIAL_420"]:
            # Ensure dtype and layout are compatible
            if planar == 1:
                pixel_array = np.moveaxis(pixel_array, 0, -1)
            if pixel_array.dtype != np.uint8:
                pixel_array = np.clip(pixel_array, 0, 255).astype(np.uint8)
            # Convert YBR/YCbCr to RGB for downstream processing/saving

            pixel_array = np.array(Image.fromarray(pixel_array, mode='YCbCr').convert('RGB'))
       

        else:
            pixel_array = pixel_array.astype(np.float32)
            # handle MONOCHROME1 (invert)
            if photometric == "MONOCHROME1":
                pixel_array = pixel_array.max() - pixel_array

            # normalize
            pixel_array -= pixel_array.min()
            if pixel_array.max() > 0:
                pixel_array = (pixel_array / pixel_array.max()) * 255.0

            pixel_array = pixel_array.astype(np.uint8)

        return pixel_array
    except Exception as e:
        logger.error("CONVERT_ERROR|photometric=%s|planar=%s|error=%s", photometric, planar, str(e))
        return None

def dicom_to_png(dicom_path, 
                 output_png_path, 
                 anonymise=False, 
                 bbox_frac_coords=((0.0, 0.0), (0.08, 1.0)),
                 resize=False,
                 IO_save=False,
                 profile=False):
    try: 
        start_time = time.time()
        ds_copy = read_dicom(dicom_path)
        if ds_copy is None:
            return None
        photometric = ds_copy.get("PhotometricInterpretation", "").upper()
        # dirty hack to change YBR_FULL_422 to RGB
        if photometric == "YBR_FULL_422":
            ds_copy.PhotometricInterpretation="RGB"
            photometric = "RGB"
        t_read = time.time()
        final_image = ds_copy.pixel_array
        if ds_copy.get('NumberOfFrames', 0) > 1:
            mid_slice = final_image.shape[0] // 2
            final_image = final_image[mid_slice]
        t_pixel = time.time()
        # planar = ds_copy.get("PlanarConfiguration", 0)

        # # Convert to RGB/Grayscale early so anonymization operates in correct color space
        # final_image = get_image_from_pixels(photometric, planar, pixel_data)
        if final_image is None:
            logger.warning('Failed to convert pixel data for %s', dicom_path)
            return None
        t_convert = time.time()
        
        if anonymise and bbox_frac_coords:
            is_color = final_image.ndim == 3
            height, width = final_image.shape[:2]
            
            # get predetermined bbox if applicable
            model = ds_copy.get('ManufacturerModelName', "")
            if model in BBOX_COORDS_CONFIGS.keys():
                bbox_frac_coords = BBOX_COORDS_CONFIGS[model]
        
            (top_frac, left_frac), (bottom_frac, right_frac) = bbox_frac_coords
            r1 = int(top_frac * height)
            r2 = int(bottom_frac * height)
            c1 = int(left_frac * width)
            c2 = int(right_frac * width)

            modified_array = final_image.copy()
            if is_color:
                modified_array[r1:r2, c1:c2, :] = 0
            else:
                modified_array[r1:r2, c1:c2] = 0
            final_image = modified_array
        t_anon = time.time()
        # check for resizing if flag is set
        if resize:
            final_image = resize_image(final_image)
        t_resize = time.time()
        if not IO_save:
            # Use cv2 for faster saving
            if isinstance(final_image, Image.Image):
                final_image = np.array(final_image)
            if final_image.ndim == 3:
                final_image = cv2.cvtColor(final_image, cv2.COLOR_RGB2BGR)
            cv2.imwrite(output_png_path, final_image)
        t_save = time.time()
        if profile:
            logger.info("[PROFILE] dicom_to_png: read=%.3fs, pixel=%.3fs, convert=%.3fs, anonymize=%.3fs, resize=%.3fs, save=%.3fs, total=%.3fs | file=%s",
                        t_read-start_time, t_pixel-t_read, t_convert-t_pixel, t_anon-t_convert, t_resize-t_anon, t_save-t_resize, t_save-start_time, dicom_path)
        return final_image
    except Exception as e:
        logger.error("DICOM_TO_PNG_ERROR|file=%s|error=%s", dicom_path, str(e))
        return None

def read_dicom(path):
    try:
        ds = pydicom.dcmread(path)
        # MAKE SURE WE ONLY WORK ON DATA COPY (maybe...)
        ds_copy = deepcopy(ds)
        del ds
        ds_copy = decompress_if_needed(ds_copy)

        if ds_copy is None:
            logger.warning('DICOM file %s has corrupted or missing pixel data', path)
            return None
            
        return ds_copy
    except Exception as e:
        logger.error('READ_DICOM_ERROR|file=%s|error=%s', path, str(e))
        return None
    
def dicom_to_dcm(dicom_path, 
                 output_path, 
                 anonymise=True, 
                 bbox_frac_coords=((0.0, 0.0), (0.08, 1.0))):
    try: 
        start_time = time.time()
        ds_copy = read_dicom(dicom_path)
        if ds_copy is None:
            return None
        t_read = time.time()
        
        pixel_array = ds_copy.pixel_array 
        if ds_copy.get('NumberOfFrames', 0) > 1:
            mid_slice = pixel_array.shape[0] // 2
            pixel_array = pixel_array[mid_slice]
        t_pixel = time.time()

        if anonymise and bbox_frac_coords:
            is_color = pixel_array.ndim == 3 and ds_copy.SamplesPerPixel == 3
            height, width = pixel_array.shape[:2]
            
            # get predetermined bbox if applicable
            model = ds_copy.get('ManufacturerModelName', "")
            if model in BBOX_COORDS_CONFIGS.keys():
                bbox_frac_coords = BBOX_COORDS_CONFIGS[model]
        
            (top_frac, left_frac), (bottom_frac, right_frac) = bbox_frac_coords
            r1 = int(top_frac * height)
            r2 = int(bottom_frac * height)
            c1 = int(left_frac * width)
            c2 = int(right_frac * width)
    
            modified_array = pixel_array.copy()
            if is_color:
                modified_array[r1:r2, c1:c2, :] = 0
            else:
                modified_array[r1:r2, c1:c2] = 0
            pixel_array = modified_array
        
        # idk if we need this shit, happy suggestions from the AI overlords
        # if pixel_array.dtype != np.uint8:
        #     pixel_array = np.clip(pixel_array, 0, 255).astype(np.uint8)

        # if pixel_array.ndim == 3 and pixel_array.shape[2] == 3:
        #     ds_copy.SamplesPerPixel = 3
        #     ds_copy.PhotometricInterpretation = "RGB"
        #     ds_copy.PlanarConfiguration = 0
        # else:
        #     ds_copy.SamplesPerPixel = 1
        #     ds_copy.PhotometricInterpretation = "MONOCHROME2"
        #     if hasattr(ds_copy, 'PlanarConfiguration'):
        #         del ds_copy["PlanarConfiguration"]

        ds_copy.PixelData = pixel_array.tobytes()
        ds_copy.save_as(output_path, enforce_file_format=True)

    except Exception as e:
        logger.error("DICOM_TO_DCM_ERROR|file=%s|error=%s", dicom_path, str(e))
        return None

def resize_image(image):
    # Support both PIL Image and numpy arrays
    if isinstance(image, Image.Image):
        w, h = image.size
        shortest_side = min(h, w)
        if shortest_side > 600:
            scale = 600 / shortest_side
            new_w, new_h = int(w * scale), int(h * scale)
            return image.resize((new_w, new_h), Image.Resampling.LANCZOS)
        return image
    else:
        # Assume numpy array
        height, width = image.shape[:2]
        shortest_side = min(height, width)
        if shortest_side > 600:
            scale = 600 / shortest_side
            new_w, new_h = int(width * scale), int(height * scale)
            interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
            return cv2.resize(image, (new_w, new_h), interpolation=interpolation)
        return image



def decompress_if_needed(ds):
    """ Decompress a compressed DICOM using GDCM. """
    if ds.file_meta.TransferSyntaxUID.is_compressed:
        try:
            # Force decompression
            ds.decompress()
            ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
            ds['PixelData'].is_undefined_length = False
        except Exception as e:
            logger.error("DECOMPRESS_ERROR|error=%s", str(e))
            raise RuntimeError(f"Failed to decompress: {e}")
    return ds


def process_folder(input_folder: str, output_folder: str) -> None:
    out_anonymized = os.path.join(output_folder, "anonymized_img")
    out_resized = os.path.join(output_folder, "anonymized_resized_img")
    for d in (out_anonymized, out_resized):
        os.makedirs(d, exist_ok=True)

    files = [f for f in os.listdir(input_folder) if f.lower().endswith(".dcm")]

    if files:
        first_ds = pydicom.dcmread(os.path.join(input_folder, files[0]), stop_before_pixels=True)
        patient_id = str(getattr(first_ds, "PatientID", "unknown"))
        with open(os.path.join(output_folder, "patient_id.txt"), "w") as f:
            f.write(patient_id)

    for i, file in enumerate(files):
        dicom_path = os.path.join(input_folder, file)
        try:
            #dicom_to_png(dicom_path, os.path.join(out_original, f"img_{i:03d}.png"), anonymise=False)
            dicom_to_png(dicom_path, os.path.join(out_anonymized, f"img_{i:03d}.png"), anonymise=True)
            dicom_to_png(dicom_path, os.path.join(out_resized, f"img_{i:03d}.png"), anonymise=True, resize=True)
        except Exception as e:
            logger.error("PROCESSING_ERROR|file=%s|error=%s", file, str(e))

import sys
input_folder = sys.argv[1]
output_folder = os.path.join(sys.argv[2], "/".join(input_folder.split("/")[-3:]))

process_folder(input_folder, output_folder)

try:
    from databricks.sdk.runtime import dbutils
    dbutils.jobs.taskValues.set(key   = "preprocessed_folder", \
                                value = output_folder)
except ValueError:
    logger.error("Could not import databricks and set output folder variable")