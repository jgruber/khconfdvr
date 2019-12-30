#!/usr/bin/env python3

import os
import shutil
import glob
import json
import signal
import logging
import requests
import subprocess
import tempfile
import threading
import time

FFMPEGCMD = '/usr/bin/ffmpeg -y'
ARGS = '-c copy'

LOGFORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

consoleLog = logging.StreamHandler()
consoleLog.setFormatter(logging.Formatter(LOGFORMAT))

LOG = logging.getLogger('HLS Recorder')
LOG.addHandler(consoleLog)

CONFIG = {
    'WEB_SERVICE_HOST': 'localhost',
    'WEB_SERVICE_PORT': 3100,
    'LOGLEVEL': logging.DEBUG,
    'POLL_INTERVAL': 20,
    'RECORDER_TEMP_DIR': None,
    'RECORDER_FILE_TYPE': 'mp4'
}

KEEP_RECORDING = True
CONFIG_FILE = None
DESTDIR = "%s/static/recordings" % os.path.dirname(
        os.path.realpath(__file__))
        

def get_temp_record_dir():
    global CONFIG
    if not CONFIG['RECORDER_TEMP_DIR']:
        CONFIG['RECORDER_TEMP_DIR'] = tempfile.mkdtemp('streamRecorder')
        save_config()
    return CONFIG['RECORDER_TEMP_DIR']


def get_recording_file_name(datestring, tmpdir):
    candidate = "%s-meeting.%s" % (datestring, CONFIG['RECORDER_FILE_TYPE'])
    destpath = os.path.join(DESTDIR, candidate)
    tmppath = os.path.join(tmpdir, candidate)
    if not ( os.path.exists(tmppath) or os.path.exists(destpath) ):
        return tmppath
    ls = set(os.listdir(DESTDIR))
    index = 0
    while candidate in ls:
        candidate = "%s-meeting_%s.%s" % (datestring, index, CONFIG['RECORDER_FILE_TYPE'])
        index += 1
    return os.path.join(tmpdir, candidate)


def query_stream():
    STREAM_URL = 'http://localhost:%s/video' % CONFIG['WEB_SERVICE_PORT']
    try:
        resp = requests.get('http://localhost:%s/video' % CONFIG['WEB_SERVICE_PORT'])
        resp.raise_for_status()
        return resp.json()
    except Exception as ex:
        LOG.error('error querying video streams: %s' % ex)
        return {}

def save_config():
    global CONFIG
    LOG.debug('saving configuration')
    if os.path.exists(CONFIG_FILE):
        LOG.debug('saving config to %s' % CONFIG_FILE)
        with open(CONFIG_FILE, 'w+') as json_data_file:
            json_data_file.write(json.dumps(CONFIG))


def load_config(config_file=None):
    global CONFIG, CONFIG_FILE
    if not config_file:
        config_file = "%s/streamrecorder_config.json" % os.path.dirname(
            os.path.realpath(__file__))
    CONFIG_FILE = config_file
    if os.path.exists(config_file):
        LOG.debug('loading config from %s' % config_file)
        with open(config_file) as json_data_file:
            CONFIG = json.load(json_data_file)


def record_stream(tmppath, url):
    LOG.info('recording live stream %s to %s' % (url, tmppath))
    cmd = "%s -i %s %s %s" % (FFMPEGCMD, url, ARGS, tmppath)
    LOG.debug('running blocking command: %s' % cmd)
    p = subprocess.Popen(cmd, shell=True)
    p_status = p.wait()


def publish_recordinging(tmppath, datestring):
    videofiles = []
    for filePath in glob.glob("%s/%s*.%s" % (DESTDIR, datestring, CONFIG['RECORDER_FILE_TYPE'])):
        videofiles.append(filePath)
    if not videofiles:
        dstpath = os.path.join(DESTDIR, os.path.basename(tmppath))
        LOG.debug('moving %s to %s' % (tmppath, dstpath))
        shutil.move(tmppath, dstpath)
    else:
        videofiles.append(tmppath)
        LOG.info('found %d video files from datestring %s' % (len(videofiles), datestring))
        inputpath = "%s/input.txt" % os.path.dirname(os.path.abspath(tmppath))
        input_file = open(inputpath, 'w+')
        for filePath in videofiles:
            input_file.write("file %s\n" % filePath)
        input_file.close()
        dstpath = "%s/%s_%s.%s" % (DESTDIR, datestring, str(int(time.time())), CONFIG['RECORDER_FILE_TYPE'])
        cmd = "%s -f concat -safe 0 -i %s -c copy %s" % (FFMPEGCMD, inputpath, dstpath)
        LOG.debug('running blocking command: %s' % cmd)
        p = subprocess.Popen(cmd, shell=True)
        p_status = p.wait()
        if p_status == 0:
            for filePath in videofiles:
                try:
                    os.remove(filePath)
                except:
                    LOG.error('error removing video fragment file %s' % filePath)
        else:
            LOG.error('error concatinating video files with datestring %s' % datestring)
            try:
                os.remove(dstpath)
            except:
                LOG.error('could not delete tempfile: %s' % dstpath)


class recorderThread (threading.Thread):
    recorderExit = threading.Event()

    def __init__(self):
        threading.Thread.__init__(self)

    def run(self):
        LOG.debug('HLS recorder thread started')
        while not self.recorderExit.is_set():
            try:
                streams = query_stream()
                if 'live' in streams and streams['live']:
                    moviefile = get_recording_file_name(streams['meetingDateString'], get_temp_record_dir())
                    record_stream(moviefile, streams['url'])
                    publish_recordinging(moviefile, streams['meetingDateString'])
                else:
                    LOG.debug('there is no live HLS at this time')
            except Exception as ex:
                LOG.error('could not live stream status: %s' % ex)
            self.recorderExit.wait(timeout=CONFIG['POLL_INTERVAL'])

    def join(self):
        self.recorderExit.set()
        super().join()

def recorder_exit(*args):
    global KEEP_RECORDING
    KEEP_RECORDING = False


def main():
    signal.signal(signal.SIGHUP, load_config)
    signal.signal(signal.SIGINT, recorder_exit)
    LOG.setLevel(logging.DEBUG)
    config_file = os.getenv('CONFIG_FILE', None)
    load_config(config_file)
    log_file = os.getenv('LOGFILE', CONFIG['LOGFILE'])
    if log_file:
        LOG.info('switching to file logging: %s' % log_file)
        fileLog = logging.FileHandler(log_file)
        fileLog.setFormatter(logging.Formatter(LOGFORMAT))
        LOG.removeHandler(consoleLog)
        LOG.addHandler(fileLog)

    LOG.setLevel(CONFIG['LOGLEVEL'])
    
    recorder_thread = recorderThread()
    recorder_thread.start()

    while KEEP_RECORDING:
        time.sleep(2)
    recorder_thread.join()


if __name__ == '__main__':
    main()