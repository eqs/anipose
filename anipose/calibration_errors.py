#!/usr/bin/env python3

import cv2
from cv2 import aruco
from tqdm import trange
import numpy as np
import os, os.path
from glob import glob
from collections import defaultdict
import pandas as pd

from .common import \
    find_calibration_folder, make_process_fun, \
    get_cam_name, get_video_name, load_intrinsics, load_extrinsics
from .triangulate import triangulate_optim, triangulate_simple, reprojection_error
from .calibrate_extrinsics import detect_aruco

def expand_matrix(mtx):
    z = np.zeros((4,4))
    z[0:3,0:3] = mtx[0:3,0:3]
    z[3,3] = 1
    return z

def fill_points(corners, ids):
    # TODO: this should change with calibration board config
    # 16 comes from 4 boxes (2x2) with 4 corners each
    out = np.zeros((16, 2))
    out.fill(np.nan)

    if ids is None:
        return out

    for id_wrap, corner_wrap in zip(ids, corners):
        ix = id_wrap[0]
        corner = corner_wrap.flatten().reshape(4,2)
        if ix >= 4: continue
        out[ix*4:(ix+1)*4,:] = corner

    return out

def process_trig_errors(config, fname_dict, cam_intrinsics, extrinsics, skip=20):
    minlen = np.inf
    caps = dict()
    for cam_name, fname in fname_dict.items():
        cap = cv2.VideoCapture(fname)
        caps[cam_name] = cap
        length = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        minlen = min(length, minlen)

    cam_names = sorted(fname_dict.keys())

    cam_align = config['triangulation']['cam_align']

    cam_mats = []
    for cname in cam_names:
        left = expand_matrix(np.array(cam_intrinsics[cname]['camera_mat']))
        if cname == cam_align:
            right = np.identity(4)
        else:
            right = np.array(extrinsics[(cname, cam_align)])
        mat = np.matmul(left, right)
        cam_mats.append(mat)

    go = skip
    all_points = []
    framenums = []
    for framenum in trange(minlen, desc='detecting', ncols=70):
        row = []
        for cam_name in cam_names:
            intrinsics = cam_intrinsics[cam_name]
            cap = caps[cam_name]
            ret, frame = cap.read()

            if framenum % skip != 0 and go <= 0:
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            corners, ids = detect_aruco(gray, intrinsics)
            points = fill_points(corners, ids)
            row.append(points)

        if ~np.all(np.isnan(row)):
            all_points.append(row)
            framenums.append(framenum)
            go = skip

        go = max(0, go-1)

    all_points_raw = np.array(all_points)
    framenums = np.array(framenums)

    shape = all_points_raw.shape

    all_points_3d = np.zeros((shape[0], shape[2], 3))
    all_points_3d.fill(np.nan)

    errors = np.zeros((shape[0], shape[2]))
    errors.fill(np.nan)

    for i in trange(all_points_raw.shape[0], desc='triangulating', ncols=70):
        for j in range(all_points_raw.shape[2]):
            pts = all_points_raw[i, :, j, :]
            if ~np.any(np.isnan(pts)):
                p3d = triangulate_optim(pts, cam_mats)
                all_points_3d[i, j] = p3d[:3]
                errors[i,j] = reprojection_error(p3d, pts, cam_mats)

    dout = pd.DataFrame()
    for bp_num in range(shape[2]):
        bp = 'corner_{}'.format(bp_num)
        for ax_num, axis in enumerate(['x','y','z']):
            dout[bp + '_' + axis] = all_points_3d[:, bp_num, ax_num]
        dout[bp + '_error'] = errors[:, bp_num]

    dout['fnum'] = framenums

    return dout


def process_session(config, session_path):
    # pipeline_videos_raw = config['pipeline']['videos_raw']
    pipeline_calibration_videos = config['pipeline']['calibration_videos']
    pipeline_calibration_results = config['pipeline']['calibration_results']

    calibration_path = find_calibration_folder(config, session_path)

    if calibration_path is None:
        return

    videos = glob(os.path.join(calibration_path,
                               pipeline_calibration_videos,
                               '*.avi'))
    videos = sorted(videos)

    cam_videos = defaultdict(list)

    cam_names = set()

    for vid in videos:
        name = get_video_name(config, vid)
        cam_videos[name].append(vid)
        cam_names.add(get_cam_name(config, vid))

    vid_names = cam_videos.keys()
    cam_names = sorted(cam_names)

    outdir = os.path.join(calibration_path, pipeline_calibration_results)
    os.makedirs(outdir, exist_ok=True)

    intrinsics = load_intrinsics(outdir, cam_names)
    extrinsics = load_extrinsics(outdir)

    fname_dicts = dict()
    for name in vid_names:
        fnames = cam_videos[name]
        cam_names = [get_cam_name(config, f) for f in fnames]
        fname_dict = dict(zip(cam_names, fnames))
        fname_dicts[name] = fname_dict

    for vidname, fd in fname_dicts.items():
        outname_base = vidname + '.csv'
        outname = os.path.join(outdir, outname_base)

        if os.path.exists(outname):
            continue

        print(outname)
        dout = process_trig_errors(config, fd, intrinsics, extrinsics)
        dout.to_csv(outname, index=False)


get_errors_all = make_process_fun(process_session)
