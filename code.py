"""
Circuitpython program using adafruit leds "noods" strands to indicate when a twitch
streamer is live.
"""
# This uses an Adafruit QT PY ESP32S2 to control "noods" strands to give an
# indication when a specified twitch streamer goes live.
#
# https://www.adafruit.com/product/5325
# https://www.adafruit.com/product/5508
#
# Two strands are connected via current limiting resistors to board.A0 and board.A1
# but you can change the definitions to something else if you like.
#
# When the streamer goes live there is an accelerating animation of the two strands
# (inner and outer part of the diamond) leading to a steady state slow "pulse" when
# the streamer is live.  When they go offline it ramps down to led's off.
#
# I made a version of this that uses a small custom pcb to hold the current limiting resistors
# for the LED strings but you can just inline-connect them from the QT pad to the
# led string.
#
# This uses ticks rather than time_monotonic() because time_monotonic() becomes less precise
# over time and this would impact the speed of the animation.   Also this will reboot the
# microcontroller on schedule to keep the timing values fresh.
#
# If you attach a Adafruit 128x64 OLED featherwing to the stemma-qt port of the ESP32S2
# it will receive debug messages (not required). It should auto-detect if a display is connected
# and work in either case.
# https://www.adafruit.com/product/4650
#
# Wifi parameters and twitch oAuth secrets go in secrets.py
# The streamer you want to be notified of is in streamer.py
# The neopixel on the qt py is used to indicate network status.  Most network errors
# will cause a reboot (ie - twitch token expiry errors) so if it continually reboots
# check your settings in secrets.py and streamer.py.
#
# Debug messages are sent to the serial usb.
#
# Because of the way the twitch oAuth API works you will have to generate twitch oAuth keys.
# To get and generate the twitch_client_id and twitch_client_secret:
# https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/#oauth-client-credentials-flow
# https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/#client-credentials-grant-flow
# Register a new app with:
#  https://dev.twitch.tv/docs/authentication/register-app/
# Logging into your twitch dev console https://dev.twitch.tv/console
# Register your app as category "other", and use "http://localhost" for the oauth callback.
#
# This was developed with Circuitpython 8.x, although it probably works fine with 7.x
# You need these libraries in /CIRCUITPY/lib:
# adafruit_bus_device, adafruit_display_text, adafruit_displayio_sh1107, adafruit_ntp
# adafruit_requests, adafruit_ticks, neopixel
# https://circuitpython.org/libraries

# pylint: disable=invalid-name

import math
import time
import ssl
import wifi
import pwmio
import neopixel
import rtc
import adafruit_requests
import socketpool
import adafruit_ntp
import microcontroller
import board
import displayio
import terminalio
from adafruit_display_text import label
import adafruit_displayio_sh1107
from adafruit_ticks import ticks_ms, ticks_add, ticks_less, ticks_diff

DEBUG=True

# Number of seconds between status checks, if this is too quick the query quota will run out
UPDATE_DELAY = 63*1000   # units are ms
CYCLE_DELAY = 20*1000
REBOOT_DELAY = int(22*60*60*1000)  # arbitrary 22h restart period

try:
    # If you hook an adafruit OLED Featherwing via the stemma-qt
    # connector it will display debug messages
    # https://learn.adafruit.com/adafruit-128x64-oled-featherwing
    #
    # If you don't have one connected it's okay, it just doesn't
    # try to use it. You don't have to configure it.
    displayio.release_displays()
    i2c = board.STEMMA_I2C()
    display_bus = displayio.I2CDisplay(i2c, device_address=0x3C)
    OLED=True
except Exception as e:
    OLED = False

if OLED:
    print("Using OLED display for debug")
    DISPLAY_WIDTH=128
    DISPLAY_HEIGHT=64
    display = adafruit_displayio_sh1107.SH1107(
        display_bus, width=DISPLAY_WIDTH, height=DISPLAY_HEIGHT, rotation=180)
    oled = displayio.Group()
    display.show(oled)
    text1 = "Starting up"
    text_1 = label.Label(terminalio.FONT, text=text1, color=0xFFFFFF, x=0, y=8)
    text_2 = label.Label(terminalio.FONT, text=text1, scale=1, color=0xFFFFFF, x=0, y=19)
    text_3 = label.Label(terminalio.FONT, text=text1, scale=1, color=0xFFFFFF,x=0,y=30)
    text_4 = label.Label(terminalio.FONT, text=text1, scale=1, color=0xFFFFFF,x=0,y=41)
    oled.append(text_1)
    oled.append(text_2)
    oled.append(text_3)
    oled.append(text_4)
    display.auto_refresh=False
else:
    print("No external display found")

def update_oled(t):
    if OLED:
        text_1.text = text_2.text
        text_2.text = text_3.text
        text_3.text = text_4.text
        text_4.text = t
        display.refresh()

def reboot_if_error(delay):
    """
    reboot the microcontroller after delay seconds delay
    """
    update_oled("Reboot in "+str(delay)+" seconds")
    status_light.fill((255,0,0))
    print("Reboot in",delay,"seconds")
    time.sleep(delay)
    microcontroller.reset()

# Setup neopixel status light
status_light = neopixel.NeoPixel(board.NEOPIXEL, 1, brightness=0.2)

# Set the outputs to the led strands all to 0 to start.
PINS = (board.A0, board.A1) # List of pins, one per nOOd
pin_list = [pwmio.PWMOut(pin, frequency=1000, duty_cycle=0) for pin in PINS]
for pin in pin_list:
    pin.duty_cycle = 0

GAMMA = 2.6  # For perceptually-linear brightness
TWITCH_AUTH_URL = "https://id.twitch.tv/oauth2/token"
TWITCH_STREAM_URL = "https://api.twitch.tv/helix/streams?user_login="

update_oled("Reading secrets")
# Get wifi details and more from a secrets.py file
try:
    from secrets import secrets
except ImportError:
    print("WiFi secrets are kept in secrets.py, please add them there!")
    update_oled("No Wifi secrets info")
    status_light.fill((0,0,255))  # Status light red
    raise

update_oled("Getting Streamer")
try:
    from streamer import STREAMER_NAME
    print("Monitoring status for",STREAMER_NAME)
except ImportError:
    update_oled("No streamer in streamer.py")
    print("Set twitch stream to monitor as STREAMER_NAME in streamer.py")
    raise

try:
    from streamer import TIMEZONE_OFFSET
except ImportError:
    print("Set TIMEZONE_OFFSET in streamer.py if you want times displayed in your time zone")
    TIMEZONE_OFFSET = 0    # Will display UTC time

def get_twitch_status(twitch_token, streamer_name):
    """
    Get status of specified twitch streamer to determine
code.py    if they are currently live or not.
    Uses secrets['twitch_client_id'] from globals and previously-acquired token
    """
    headers = {
        'Client-ID': secrets['twitch_client_id'],
        'Authorization': 'Bearer ' + twitch_token
    }
    if DEBUG:
        print("Headers are",headers)
    try:
        stream = requests.get(TWITCH_STREAM_URL + streamer_name, headers=headers)
        stream_data = stream.json()
    except Exception as error:  # pylint: disable=broad-except
        print("Exception during status request: ",error)
        update_oled("Twitch Status Error")
        reboot_if_error(30)
    if DEBUG:
        print("Data is",stream_data['data'])
    if stream_data['data']:
        return True
    return False

def get_twitch_token():
    """
    Get a twitch oAuth token.
    Uses secrets['twitch_client_id'] and secrets['twitch_client_secret'] from globals
    """
    body = {
        'client_id': secrets['twitch_client_id'],
        'client_secret': secrets['twitch_client_secret'],
        "grant_type": 'client_credentials'
    }
    try:
        r = requests.post(TWITCH_AUTH_URL, data=body)
        keys = r.json()
        if DEBUG:
            print("Twitch token keys:",keys)
    except Exception as error:  # pylint: disable=broad-except
        print("Exception getting twitch token:",error)
        return None
    if not "access_token" in keys:
        print("Didn't get proper access token from twitch")
        return None
    return keys['access_token']

def format_datetime(datetime):
    """
    Simple pretty-print for a datetime object
    """
    # pylint: disable=consider-using-f-string
    return "{:02}/{:02}/{} {:02}:{:02}:{:02}".format(
        datetime.tm_mon,
        datetime.tm_mday,
        datetime.tm_year,
        datetime.tm_hour,
        datetime.tm_min,
        datetime.tm_sec,
    )
# Wireless setup
update_oled("Connect to Wireless")
print("Connecting to Wireless...")
status_light.fill((0,0,255))  # Status light blue
try:
    wifi.radio.connect(secrets["ssid"], secrets["password"])
except Exception as e:
    print("Wifi connection error",e)
    update_oled("Wifi Error")
    reboot_if_error(30)

print("Connected to", str(wifi.radio.ap_info.ssid, "utf-8"))
print("My IP address is",str(wifi.radio.ipv4_address))
pool = socketpool.SocketPool(wifi.radio)

# Time setup
update_oled("Setting NTP time")
print("Setting time...")
ntp = adafruit_ntp.NTP(pool,tz_offset=TIMEZONE_OFFSET)
try:
    rtc.RTC().datetime = ntp.datetime
except Exception as e: # pylint: disable=broad-except
    print("Error getting time, oh well")
status_light.fill((0,255,0))  # status light green

# Requests setup for getting twitch tokens and status
requests = adafruit_requests.Session(pool, ssl.create_default_context())

# Get a twitch OAuth token from credentials in secrets.py
update_oled("Get Twitch Token")
print("Getting twitch authorization token")
status_light.fill((0,255,255))
token = get_twitch_token()
if token is None:
    update_oled("Twitch Token Error")
    reboot_if_error(30)
status_light.fill((0,255,0))  # status light green
update_oled("Twitch Token OK")

# Intial status
streamer_status = False
cycle_sequence = 0
# Set the update such that it will guarantee a twitch status check on first pass through while loop
last_update_time = ticks_add(ticks_ms(),-2*UPDATE_DELAY)

cycle_start_time = last_update_time
reboot_time = ticks_add(ticks_ms(), REBOOT_DELAY)

while True:
    time_now = ticks_ms()

    # Only check periodically since it requires a request to twitch api
    if ticks_diff(time_now,last_update_time) > UPDATE_DELAY:

        last_update_time = ticks_ms()

        print("Checking status at ",format_datetime(time.localtime()))
        update_oled(format_datetime(time.localtime()))
        status_light.fill((255,255,0))
        # If we get an error reading twitch status it can be anything from network
        # to the oauth token has expired to who knows what, so if one happens
        # we'll just reset the board and start over.   Reset will generate a new
        # oauth token.
        try:
            update_oled("Get Status "+STREAMER_NAME)
            streamer_status = get_twitch_status(token,STREAMER_NAME)
        except Exception as e:
            update_oled("Error getting status, resetting")
            status_light.fill((0,0,255))
            print("Error getting streamer status:",e)
            reboot_if_error(30)
        status_light.fill((0,255,0))
        if streamer_status:
            update_oled("Live: "+STREAMER_NAME)
            print(STREAMER_NAME,"is live")
        else:
            update_oled("offline: "+STREAMER_NAME)
            print(STREAMER_NAME,"is offline")

        # ramp the lights down to zero if the streamer was live and went offline
        if streamer_status is False and cycle_sequence != 0:
            cycle_sequence = 0
            for t in range(65535,0,-1):
                for i, pin in enumerate(pin_list):
                    pin.duty_cycle = t

        # Circuitpython boards are great, but if they run for really long
        # times the timing on clocks gets weird and slow.   We fix this by
        # just automatically resetting every day or so.  Only do it if
        # the streamer is offline so it doesn't interrupt the animation
        if  streamer_status is False and ticks_less(reboot_time,time_now) is True:
            print("Programmed reboot")
            update_oled("Programmed Reboot")
            reboot_if_error(5)


    # Figure out which pattern to display on the two strings of lights
    # 0 = streamer not live (off)
    # 1 = streamer starting up (animation)
    # 2 = streamer is live (animation)
    if streamer_status is True and cycle_sequence < 2:
        if ticks_diff(time_now,cycle_start_time) > CYCLE_DELAY:
            cycle_sequence = cycle_sequence + 1
            cycle_start_time = time_now
            print("Changing to cycle pattern",cycle_sequence)
            update_oled("Change to Cycle "+str(cycle_sequence))

    if cycle_sequence == 0:
        for pin in pin_list:
            # everything off, streamer is not live
            pin.duty_cycle = 0
    if cycle_sequence == 1:
        # Streamer has just gone live, so a startup sequence pattern
        # determine how far as a fraction 0-1 are we into the time of this cycle
        cycle_fraction = ticks_diff(time_now,cycle_start_time)/CYCLE_DELAY
        for i, pin in enumerate(pin_list):
            # an accelerating sine wave
            t = (ticks_diff(ticks_ms(),cycle_start_time) * 2.0 * math.pi * cycle_fraction * 4)/1000
            brightness = int(((math.sin(t-i*math.pi*0.5) + 1.0)*0.5) ** GAMMA  * 65535 + 0.5)
            pin.duty_cycle = brightness
    if cycle_sequence == 2:
        # Streamer has been live, slow pulse
        for i, pin in enumerate(pin_list):
            t = (ticks_diff(ticks_ms(),cycle_start_time) * math.pi * 0.25)/1000
            # A slow slightly pulsing sine wave (starts bright)
            brightness = int((math.cos(t)*0.2 + 0.8) ** GAMMA  * 65535 + 0.5)
            pin.duty_cycle = brightness