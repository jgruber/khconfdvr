#!/usr/bin/env python3

import binascii
import os
import json
import glob
import datetime
import requests
import uuid
import logging
import signal
import threading
import time
import subprocess

from PIL import Image
from PIL import ImageFont
from PIL import ImageDraw

from flask import Flask, request, jsonify, Response, render_template

KHCONF_BASE_URL = 'https://report.khconf.com/video_api.php'
UA = 'info[ua]=Mozilla/5.0+(X11;+Linux+x86_64)+AppleWebKit/537.36+(KHTML,+like+Gecko)+Chrome/79.0.3945.79+Safari/537.36'
BW = 'info[browser][name]=Chrome&info[browser][version]=79.0.3945.79&info[browser][major]=79'
EN = 'info[engine][name]=Blink'
OS = 'info[os][name]=Linux&info[os][version]=x86_64'
CPU = 'cpu[architecture]=amd_64'
BROWSER_INFO = "%s&%s&%s&%s&%s" % (UA, BW, EN, OS, CPU)
CLIENT_VERSION = '1.1.5'

CONFIG = {
    'WEB_SERVICE_PORT': 3100,
    'LOGLEVEL': logging.DEBUG,
    'POLL_INTERVAL': 20,
    'DEVICE_ID': None,
    'TOKEN': None,
    'ADMIN_PIN': None,
    'VIEWER_PIN': '000000',
    'CONGREGATION_NAME': None
}


CONFIG_FILE = None

LOGFORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

congregationName = None
liveMeetingVriId = None
liveMeetingVdrId = None
liveMeetingStreamUrl = None
liveMeetingCounts = {}
inMeeting = False

consoleLog = logging.StreamHandler()
consoleLog.setFormatter(logging.Formatter(LOGFORMAT))

LOG = logging.getLogger('KHConf DVR Services')
LOG.addHandler(consoleLog)

requests_log = logging.getLogger("requests.packages.urllib3")
requests_log.propagate = True

class KHConfDVRFlask(Flask):
    def run(self, host=None, port=None, **options):
        initialize()
        super(KHConfDVRFlask, self).run(host=host, port=port, **options)


app = KHConfDVRFlask('khconfdvr')
app.logger.addHandler(consoleLog)


class ClientError(Exception):
    status_code = 400
    payload = None

    def __init__(self, message, status_code=None, payload=None):
        Exception.__init__(self)
        self.message = message
        if status_code:
            self.status_code = status_code
        self.playload = payload

    def to_dict(self):
        rv = dict()
        if self.payload:
            rv = dict(self.payload)
        rv['message'] = self.message
        return rv


@app.errorhandler(ClientError)
def client_error(error):
    response = jsonify(error.to_dict())
    response.status_code = error.status_code
    return response


@app.route('/config', methods=['GET', 'POST'])
def config_service():
    if request.method == 'POST':
        config = request.json
        if 'adminpin' in config:
            if CONFIG['ADMIN_PIN']:
                if CONFIG['ADMIN_PIN'] == config['adminpin']:
                    if 'token' in config:
                        CONFIG['TOKEN'] = config['token']
                        save_config()
                else:
                    LOG.error('adminpin does not match')
                    raise ClientError(
                        'adminpin does not match', status_code=401)
            else:
                CONFIG['ADMIN_PIN'] = config['adminpin']
                if 'token' in config:
                    CONFIG['TOKEN'] = config['token']
                LOG.info('initial token and admin pin set')
                LOG.info('registring this device %s with KHConf with token: %s' % (
                    CONFIG['DEVICE_ID'], CONFIG['TOKEN']))
                registry = register_device(
                    CONFIG['TOKEN'], CONFIG['DEVICE_ID'])
                print("%s" % registry)
                if 'config' in registry:
                    LOG.info('KHConf registration complete for congregation: %s' %
                             registry['config']['cong'])
                    CONFIG['CONGREGATION_NAME'] = registry['config']['cong']
                    save_config()
                else:
                    error = Exception(
                        'could not register device.. %s' % registry)
                    raise ClientError(registry, status_code=400)
        return jsonify({'status': 'ok'})
    elif request.method == 'GET':
        return render_template('config.html')


@app.route('/video', methods=['GET'])
def current_video_service():
    if inMeeting:
        now = datetime.datetime.now()
        datestring = now.strftime('%m-%d-%Y')
        live = {
            'url': liveMeetingStreamUrl,
            'congregation': CONFIG['CONGREGATION_NAME'],
            'live': True,
            'poster': '/posters/%s' % make_live_poster(CONFIG['CONGREGATION_NAME']),
            'meetingDateString': datestring,
            'countNeeded': True,
            'pollInterval': (CONFIG['POLL_INTERVAL'] * 2)
        }
        clientip = request.remote_addr
        if clientip in liveMeetingCounts:
            live['countNeeded'] = False
        LOG.info('live meeting video request for %s - countNeeded: %s' %
                 (CONFIG['CONGREGATION_NAME'], live['countNeeded']))
        return jsonify(live)
    else:
        recdir = "%s/static/recordings" % os.path.dirname(
            os.path.realpath(__file__))
        list_of_recs = glob.glob("%s/*" % recdir)
        latest_rec = max(list_of_recs, key=os.path.getmtime)
        recording_file = os.path.basename(latest_rec)
        datestring = datetime.datetime.fromtimestamp(
            os.path.getmtime(latest_rec)).strftime('%m-%d-%Y')
        LOG.info('directing cliet to %s meeting recording %s from %s' %
                 (CONFIG['CONGREGATION_NAME'], recording_file, datestring))
        rec = {
            'url': "/recordings/%s" % recording_file,
            'congregation': CONFIG['CONGREGATION_NAME'],
            'live': False,
            'poster': '/posters/%s' % make_recording_poster(recording_file, CONFIG['CONGREGATION_NAME'], datestring),
            'meetingDateString': datestring,
            'countNeeded': False,
            'pollInterval': (CONFIG['POLL_INTERVAL'] * 2)
        }
        return jsonify(rec)


@app.route('/count', methods=['POST'])
def submit_count():
    if inMeeting and liveMeetingVriId:
        clientip = request.remote_addr
        count = request.json
        if 'count' in count:
            liveMeetingCounts[clientip] = int(count['count'])
            meetingCount = get_live_meeting_count()
            LOG.info('received a count of %d from %s.. total count is now: %d' % (
                liveMeetingCounts[clientip], clientip, meetingCount))
            get_vdr_id(CONFIG['DEVICE_ID'],
                       liveMeetingVriId, count=meetingCount)
    else:
        LOG.error('submitting count while no live meeting in progress')
        raise ClientError(
            'submitting count while no live meeting in progress', status_code=400)
    return jsonify({'status': 'ok'})


@app.route('/', methods=['GET'])
def index():
    if not CONFIG['TOKEN']:
        LOG.info('requesting initial config and admin pin')
        return render_template('config.html')
    else:
        return render_template('getvideo.html')


@app.route('/<path:path>')
def catch_all(path):
    return app.send_static_file(path)


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
        config_file = "%s/webapp_config.json" % os.path.dirname(
            os.path.realpath(__file__))
    CONFIG_FILE = config_file
    if os.path.exists(config_file):
        LOG.debug('loading config from %s' % config_file)
        with open(config_file) as json_data_file:
            CONFIG = json.load(json_data_file)


def get_live_meeting_count():
    meetingCount = 0
    if inMeeting:
        for ip in liveMeetingCounts.keys():
            meetingCount = meetingCount + int(liveMeetingCounts[ip])
    return meetingCount


def update_meeting_status():
    global CONFIG, inMeeting, liveMeetingVriId, liveMeetingStreamUrl, liveMeetingVdrId
    if CONFIG['TOKEN'] and CONFIG['DEVICE_ID']:
        if not CONFIG['CONGREGATION_NAME']:
            LOG.info('registring this device %s with KHConf with token: %s' % (
                CONFIG['DEVICE_ID'], CONFIG['TOKEN']))
            registry = register_device(
                CONFIG['TOKEN'], CONFIG['DEVICE_ID'])
            if 'config' in registry:
                LOG.info('KHConf registration complete for congregation: %s' %
                         registry['config']['cong'])
                CONFIG['CONGREGATION_NAME'] = registry['config']['cong']
                save_config()
            else:
                error = Exception('could get register device.. %s' % registry)
                raise error
        streams = get_streams(CONFIG['DEVICE_ID'])
        oldStreamId = liveMeetingStreamUrl
        if 'active' in streams and streams['active']:
            liveMeetingVriId = streams['streams'][0]['vri']
            liveMeetingStreamUrl = streams['streams'][0]['url']
            if not inMeeting:
                LOG.info('live meeting stream %s started with video relay id: %s' % (
                    liveMeetingStreamUrl, liveMeetingVriId))
                inMeeting = True
                LOG.debug('discovered live video stream')
            else:
                if not oldStreamId == liveMeetingStreamUrl:
                    LOG.error(
                        'meeting id changed within poll cycle..')
        else:
            LOG.debug('there is no current live video stream')
            try:
                if liveMeetingVdrId:
                    unregister_device(CONFIG['DEVICE_ID'], liveMeetingVdrId)
            except:
                pass
            inMeeting = False
            liveMeetingVriId = None
            liveMeetingVdrId = None
            liveMeetingStreamUrl = None
            liveMeetingCounts = {}


def submitting_count():
    global liveMeetingVdrId
    if liveMeetingVriId:
        meetingCount = get_live_meeting_count()
        LOG.info('submitting count for video relay id %s as %d' %
                 (liveMeetingVriId, meetingCount))
        vdr = get_vdr_id(
            CONFIG['DEVICE_ID'], liveMeetingVriId, count=meetingCount)
        if 'vdr_id' in vdr:
            liveMeetingVdrId = vdr['vdr_id']


def make_recording_poster(recording_file, congregation, datestring):
    posters_dir = "%s/static/posters" % os.path.dirname(
        os.path.realpath(__file__))
    file_name = "%s.jpg" % str(recording_file).replace('.', '_')
    if not os.path.exists("%s/%s" % (posters_dir, file_name)):
        LOG.debug('recording found without poster.. creating')
        poster_backgroud = "%s/resources/poster_background.jpg" % os.path.dirname(
            os.path.realpath(__file__))
        img = Image.open(poster_backgroud)
        width, height = img.size
        draw = ImageDraw.Draw(img)
        font = ImageFont.truetype('arial', 96)
        title = "%s Meeting" % congregation
        text_width, text_height = draw.textsize(title, font=font)
        draw.text(((width-text_width)/2, 100),
                  title, (255, 255, 255), font=font)
        font = ImageFont.truetype('arial', 72)
        text_width, text_height = draw.textsize(datestring, font=font)
        draw.text(((width-text_width)/2, 500),
                  datestring, (255, 255, 255), font=font)
        img.save("%s/%s" % (posters_dir, file_name))
    return file_name


def make_live_poster(congregation):
    posters_dir = "%s/static/posters" % os.path.dirname(
        os.path.realpath(__file__))
    file_name = "%s_live.jpg" % str(congregation).replace(' ', '_')
    if not os.path.exists("%s/%s" % (posters_dir, file_name)):
        LOG.debug('creating %s live meeting poster' % congregation)
        poster_backgroud = "%s/resources/poster_background.jpg" % os.path.dirname(
            os.path.realpath(__file__))
        img = Image.open(poster_backgroud)
        width, height = img.size
        draw = ImageDraw.Draw(img)
        font = ImageFont.truetype('arial', 96)
        title = "%s Meeting" % congregation
        text_width, text_height = draw.textsize(title, font=font)
        draw.text(((width-text_width)/2, 100),
                  title, (255, 255, 255), font=font)
        font = ImageFont.truetype('arial', 72)
        text_width, text_height = draw.textsize('Live', font=font)
        draw.text(((width-text_width)/2, 500),
                  'Live', (255, 255, 255), font=font)
        img.save("%s/%s" % (posters_dir, file_name))
    return file_name


def generate_fingerprint():
    return binascii.hexlify(os.urandom(16)).decode('ascii')


def register_device(token, device_id):
    REGISTER_URL = "%s/register/%s/%s" % (KHCONF_BASE_URL, token, device_id)
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    data = 'info=%s&fingerprint=%s' % (BROWSER_INFO, generate_fingerprint())
    resp = requests.post(url=REGISTER_URL, data=data, headers=headers)
    resp.raise_for_status()
    # Yep.. they are that messed up.. they can't do JSON
    # This is what the output looks like.. nested JSON in JSON.. nice log capture
    # {"config": "{\"name\":\"User\",\"cong\":\"Congregation\"}"}
    resp_obj = resp.json()
    resp_obj['config'] = json.loads(resp_obj['config'])
    return resp_obj


def get_streams(device_id):
    PLAYLIST_URL = "%s/video/%s" % (KHCONF_BASE_URL, device_id)
    resp = requests.get(url=PLAYLIST_URL)
    resp.raise_for_status()
    return resp.json()


def get_vdr_id(device_id, vri, count=0):
    CONF_ID_URL = "%s/vdr" % (KHCONF_BASE_URL)
    data = {'device_id': device_id, 'vri': vri, 'count': count,
            'duration': CONFIG['POLL_INTERVAL'], 'client_version': CLIENT_VERSION}
    resp = requests.post(url=CONF_ID_URL, data=data)
    resp.raise_for_status()
    return resp.json()


def unregister_device(device_id, vdr_id):
    UNREGISTER_URL = "%s/vdr/%s/delete" % (KHCONF_BASE_URL, vdr_id)
    data = {'device_id': device_id}
    resp = requests.post(url=UNREGISTER_URL, data=data)
    resp.raise_for_status()
    return resp.json()


class pollingThread (threading.Thread):
    pollExit = threading.Event()

    def __init__(self):
        threading.Thread.__init__(self)

    def run(self):
        LOG.debug('KHConf video services polling thread started')
        while not self.pollExit.is_set():
            try:
                update_meeting_status()
            except Exception as ex:
                LOG.error('could not update meeting status: %s' % ex)
            self.pollExit.wait(timeout=CONFIG['POLL_INTERVAL'])

    def join(self):
        self.pollExit.set()
        super().join()


def initialize():
    LOG.setLevel(logging.DEBUG)
    config_file = os.getenv('CONFIG_FILE', None)
    load_config(config_file)
    if not CONFIG['DEVICE_ID']:
        CONFIG['DEVICE_ID'] = str(uuid.uuid4())
        save_config()  
    log_file = os.getenv('LOGFILE', CONFIG['LOGFILE'])
    if log_file:
        LOG.info('switching to file logging: %s' % log_file)
        fileLog = logging.FileHandler(log_file)
        fileLog.setFormatter(logging.Formatter(LOGFORMAT))
        LOG.removeHandler(consoleLog)
        LOG.addHandler(fileLog)
        app.logger.removeHandler(consoleLog)
        app.logger.addHandler(fileLog)

    LOG.setLevel(CONFIG['LOGLEVEL'])
    requests_log.setLevel(CONFIG['LOGLEVEL'])
    app.logger.setLevel(CONFIG['LOGLEVEL'])

    polling_thread = pollingThread()
    polling_thread.start()


def main():
    app.run(host='0.0.0.0',
            port=CONFIG['WEB_SERVICE_PORT'],
            threaded=True)


if __name__ == '__main__':
    main()
