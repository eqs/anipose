#!/usr/bin/env python3

import cv2
from cv2 import aruco
import re
import os, os.path
from collections import deque
from glob import glob
import skvideo.io
from subprocess import check_output
import toml

def atoi(text):
    return int(text) if text.isdigit() else text

def natural_keys(text):
    '''
    alist.sort(key=natural_keys) sorts in human order
    http://nedbatchelder.com/blog/200712/human_sorting.html
    '''
    return [ atoi(c) for c in re.split('(\d+)', text) ]

def wc(filename):
    out = check_output(["wc", "-l", filename])
    num = out.decode('utf8').split(' ')[0]
    return int(num)

def get_data_length(fname):
    return wc(fname) - 1

def get_video_params(fname):
    cap = cv2.VideoCapture(fname)

    params = dict()
    params['width'] = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    params['height'] = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    params['fps'] = cap.get(cv2.CAP_PROP_FPS)

    cap.release()

    return params

def get_folders(path):
    folders = next(os.walk(path))[1]
    return sorted(folders)


def true_basename(fname):
    basename = os.path.basename(fname)
    basename = os.path.splitext(basename)[0]
    return basename


def get_cam_name(config, fname):
    basename = true_basename(fname)

    cam_regex = config['triangulation']['cam_regex']
    match = re.search(cam_regex, basename)

    if not match:
        return None
    else:
        name = match.groups()[0]
        return name.strip()

def get_video_name(config, fname):
    basename = true_basename(fname)

    cam_regex = config['triangulation']['cam_regex']
    vidname = re.sub(cam_regex, '', basename)
    return vidname.strip()

def get_duration(vidname):
    metadata = skvideo.io.ffprobe(vidname)
    duration = float(metadata['video']['@duration'])
    return duration

def get_nframes(vidname):
    metadata = skvideo.io.ffprobe(vidname)
    length = int(metadata['video']['@nb_frames'])
    return length

def full_path(path):
    path_user = os.path.expanduser(path)
    path_full = os.path.abspath(path_user)
    path_norm = os.path.normpath(path_full)
    return path_norm


def process_all(config, process_session, **args):

    pipeline_prefix = config['path']
    nesting = config['nesting']

    output = dict()

    if nesting == 0:
        output[()] = process_session(config, pipeline_prefix, **args)
        return output

    folders = get_folders(pipeline_prefix)
    level = 1

    q = deque()

    next_folders = [ (os.path.join(pipeline_prefix, folder),
                      (folder,),
                      level)
                     for folder in folders ]
    q.extend(next_folders)

    while len(q) != 0:
        path, past_folders, level = q.pop()
        if level == nesting:
            output[past_folders] = process_session(config, path, **args)
        elif level > nesting:
            continue
        elif level < nesting:
            folders = get_folders(path)
            next_folders = [ (os.path.join(path, folder),
                              past_folders + (folder,),
                              level+1)
                             for folder in folders ]
            q.extend(next_folders)

    return output

def make_process_fun(process_session, **args):
    def fun(config):
        return process_all(config, process_session, **args)
    return fun

def find_calibration_folder(config, session_path):
    pipeline_calibration_videos = config['pipeline']['calibration_videos']
    nesting = config['nesting']

    level = nesting
    curpath = session_path

    while level >= 0:
        checkpath = os.path.join(curpath, pipeline_calibration_videos)
        print(checkpath)
        videos = glob(os.path.join(checkpath, '*.avi'))
        if len(videos) > 0:
            return curpath

        curpath = os.path.dirname(curpath)
        level -= 1

def load_intrinsics(folder, cam_names):
    intrinsics = {}
    for cname in cam_names:
        fname = os.path.join(folder, 'intrinsics_{}.toml'.format(cname))
        intrinsics[cname] = toml.load(fname)
    return intrinsics

def load_extrinsics(folder):
    extrinsics = toml.load(os.path.join(folder, 'extrinsics.toml'))
    extrinsics_out = dict()
    for k, v in extrinsics.items():
        new_k = tuple(k.split('_'))
        extrinsics_out[new_k] = v
    return extrinsics_out

ARUCO_DICTS = {
    (4, 50): aruco.DICT_4X4_50,
    (5, 50): aruco.DICT_5X5_50,
    (6, 50): aruco.DICT_6X6_50,
    (7, 50): aruco.DICT_7X7_50,

    (4, 100): aruco.DICT_4X4_100,
    (5, 100): aruco.DICT_5X5_100,
    (6, 100): aruco.DICT_6X6_100,
    (7, 100): aruco.DICT_7X7_100,

    (4, 250): aruco.DICT_4X4_250,
    (5, 250): aruco.DICT_5X5_250,
    (6, 250): aruco.DICT_6X6_250,
    (7, 250): aruco.DICT_7X7_250,

    (4, 1000): aruco.DICT_4X4_1000,
    (5, 1000): aruco.DICT_5X5_1000,
    (6, 1000): aruco.DICT_6X6_1000,
    (7, 1000): aruco.DICT_7X7_1000
}

def get_calibration_board(config):
    dkey = (config['calibration']['board_marker_bits'],
            config['calibration']['board_marker_dict_number'])
    dictionary = aruco.getPredefinedDictionary(ARUCO_DICTS[dkey])
    board_size = config['calibration']['board_size']
    board_type = config['calibration']['board_type'].lower()
    if board_type == 'aruco':
        board = aruco.GridBoard_create(
            board_size[0], board_size[1],
            config['calibration']['board_marker_length'],
            config['calibration']['board_marker_separation_length'],
            dictionary)
    elif board_type == 'charuco':
        board = aruco.CharucoBoard_create(
            board_size[0], board_size[1],
            config['calibration']['board_square_side_length'],
            config['calibration']['board_marker_length'],
            dictionary)
    else:
        raise ValueError("board_type should be either "
                         "'aruco' or 'charuco', not '{}'".format(
                             board_type))

    return board

def get_calibration_board_image(config):
    board = get_calibration_board(config)
    numx, numy = board.getGridSize()
    size = numx*200, numy*200
    img = board.draw(size)
    return img
