/*jshint esversion: 6 */

var playerElement = document.getElementById("player");
var defaultPoster = '/posters/blank.jpg';
var player = null;
var bindKeyboard = true;
var watchForLiveStream = null;
var waitForLiveStreamPlay = null;

var showMenu = function () {
    var osmenu = document.getElementById('osmenu');
    osmenu.classList.add('osmenuclicked');
    console.log('showing menu');
    var menuclicked = setInterval(function () {
        osmenu.classList.remove('osmenuclicked');
        clearInterval(menuclicked);
    }, 100);
};

var getVideoUrl = function () {
    var videoReq = new XMLHttpRequest();
    videoReq.addEventListener('load', function () {
        if (this.status == 200 && this.responseText) {
            var respObj = JSON.parse(this.responseText);
            if (!respObj.live) {
                if (waitForLiveStreamPlay) {
                    console.log('clearing live stream play poller');
                    clearInterval(waitForLiveStreamPlay);
                    console.log('live stream ended waiting for user to play.. reverting to last meeting recording');
                    watchForLiveStream = null;
                    showVideo(respObj.url, respObj.poster);
                }
                if (!watchForLiveStream) {
                    console.log('starting live stream updates poller');
                    watchForLiveStream = setInterval(getVideoUrl, respObj.pollInterval * 1000);
                    console.log('setting player to latest meeting recording');
                    showVideo(respObj.url, respObj.poster);
                }
            } else {
                if (watchForLiveStream) {
                    console.log('clearing live stream updates poller');
                    clearInterval(watchForLiveStream);
                }
                console.log('setting player to live video');
                showVideo(respObj.url, respObj.poster);
                setTimeout(function () {
                    if (!player.isPlaying()) {
                        console.log('live stream not playing yet.. starting live stream play poller');
                        if (!waitForLiveStreamPlay) {
                            waitForLiveStreamPlay = setInterval(getVideoUrl, respObj.pollInterval * 1000);
                        }
                    } else {
                        if (waitForLiveStreamPlay) {
                            console.log('clearing live stream play poller');
                            clearInterval(waitForLiveStreamPlay);
                        }
                    }
                }, 2000);
                if (respObj.countNeeded) {
                    getCount();
                }
            }
        }
    });
    videoReq.open('GET', '/video');
    videoReq.send();
};

var showVideo = function (url, poster) {
    console.log('starting player for ' + url);
    if (!poster) {
        poster = defaultPoster;
    }
    if (player) {
        player.destroy();
    }
    player = new Clappr.Player({
        source: url,
        poster: poster,
        mute: false,
        height: '100%',
        width: '100%',
        autoPlay: true,
        loop: true,
        playbackNotSupportedMessage: 'Please stand by.. stream playback interupted'
    });
    player.on(Clappr.Events.PLAYER_ERROR, function () {
        showVideo('/processing_en.mp4');
        setTimeout(function () {
            getVideoUrl();
        }, 10000);
    });
    player.on(Clappr.Events.PLAYER_PLAY, function () {
        console.log('playing');
        if (waitForLiveStreamPlay) {
            clearInterval(waitForLiveStreamPlay);
        }
    });
    player.attachTo(playerElement);
    keyboardBindings();
    bindKeyboard = true;
    window.location.hash = '#player';
};

var togglePlay = function () {
    if (player) {
        return player.isPlaying() ? player.pause() : player.play();
    }
};

osdContent = function (content) {
    var osd = document.getElementById('osd');
    var osmenu = document.getElementById('osmenu');
    var osdContent = document.getElementById('osdContent');
    if (content) {
        osmenu.style.setProperty('display', 'none');
        osdContent.innerHTML = content;
        bindKeyboard = false;
        osd.style.setProperty('opacity', 0);
        osd.style.setProperty('display', 'block');
        osd.style.setProperty('opacity', 1);
        osd.style.setProperty('z-index', 1000);
    } else {
        osmenu.style.setProperty('display', 'block');
        osd.style.setProperty('opacity', 0);
        osd.style.setProperty('z-index', 10);
        var clearScreen = setInterval(function () {
            osdContent.innerHTML = '';
            osd.style.setProperty('display', 'none');
            bindKeyboard = true;
            clearInterval(clearScreen);
        }, 2000);
    }
};

var keyboardBindings = function () {
    document.onkeydown = function (evt) {
        if (bindKeyboard) {
            evt = evt || window.event;
            if (evt.ctrlKey && evt.keyCode == 67) {
                configure();
            } else {
                togglePlay();
            }
        }
    };
};


var configure = function () {
    var formContent = `
<form id='config' onSubmit='setConfig()'>
    <label>TOKEN</label></br><input id='token' maxlength='12' size='12'><br />
    <label>ADMIN PIN</label></br><input id='adminpin' maxlength='6' size='6'
        onkeypress='return event.charCode >= 48 && event.charCode <= 57'><br />
    <label>VIEWER PIN</label></br><input id='viewerpin' maxlength='6' size='6'
        value='000000' onkeypress='return event.charCode >= 48 && event.charCode <= 57'>
</form>
<button type='submit' value='Submit' form='config'>Enter</button>
`;
    osdContent(formContent);
    if (player) {
        player.destroy();
    }
    document.getElementById('token').focus();
};


var setConfig = function () {
    console.log('submitting configuration');
    var tokenEl = document.getElementById('token');
    var adminpinEl = document.getElementById('adminpin');
    var viewerpinEl = document.getElementById('viewerpin');
    var token = null;
    var adminpin = null;
    var viewerpin = null;
    if (tokenEl) {
        token = tokenEl.value;
    }
    if (adminpinEl) {
        adminpin = adminpinEl.value;
    }
    if (viewerpinEl) {
        viewerpin = viewerpinEl.value;
    }
    if (tokenEl && adminpinEl) {
        var configReq = new XMLHttpRequest();
        configReq.open('POST', '/config');
        configReq.setRequestHeader("Content-Type", "application/json;charset=UTF-8");
        configReq.send(JSON.stringify({ 'token': token, 'adminpin': adminpin, "viewerpin": viewerpin }));
        configReq.addEventListener('load', function () {
            if (this.status == 200) {
                osdContent(null);
                getVideoUrl();
            } else {
                console.log('could not set config.. is your adminpin correct?');
            }
        });
    }
};


var getViewerPin = function (message) {
    var formContent = `
<form id='viewerPinForm' onSubmit='setViewerPin()'>
    <label> PIN </label> <input id='viewerpin' maxlength='6' size='6'
        onkeypress='return event.charCode == 13 || (event.charCode >= 48 && event.charCode <= 57)'>
</form>
<button type='submit' value='Submit' form='viewerPinForm'>Enter</button>
`;
    if (message) {
        formContent = '<p>' + message + '</p>' + formContent;
    }
    osdContent(formContent);
    var pininput = document.getElementById('viewerpin')
    if(pininput) pininput.focus();
};


var setViewerPin = function () {
    console.log('submitting viewer PIN');
    var viewerpinEl = document.getElementById('viewerpin');
    if (viewerpinEl) {
        var viewerpin = viewerpinEl.value;
        var vpReq = new XMLHttpRequest();
        vpReq.addEventListener('load', function () {
            if (this.status == 200) {
                osdContent(null);
                getVideoUrl();
            } else {
                console.log('wrong viewer pin');
                getViewerPin('Incorrect PIN');
            }
        });
        vpReq.open('POST', '/viewerpin');
        vpReq.setRequestHeader("Content-Type", "application/json;charset=UTF-8");
        vpReq.send(JSON.stringify({ 'viewerpin': viewerpin }));
    }
};


var getCount = function () {
    var formContent = `
<form id='countForm' onSubmit='setCount()'>
    <label> COUNT </label> <input id='count' type='number' min='1', max='99', style='width=4vw;'>
</form>
<button type='submit' value='Submit' form='countForm'>Enter</button>
`;
    osdContent(formContent);
    document.getElementById('count').focus();
};


var setCount = function () {
    console.log('submitting Count');
    var countEl = document.getElementById('count');
    if (countEl) {
        var count = countEl.value;
        var countReq = new XMLHttpRequest();
        countReq.addEventListener('load', function () {
            osdContent(null);
        });
        countReq.open('POST', '/count');
        countReq.setRequestHeader("Content-Type", "application/json;charset=UTF-8");
        countReq.send(JSON.stringify({ 'count': count }));
    }
};



