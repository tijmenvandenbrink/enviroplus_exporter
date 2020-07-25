#!/usr/bin/env python
import datetime
import os
import random
import requests
import time
import logging
import argparse
from threading import Thread

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
from prometheus_client import start_http_server, Gauge, Histogram
import SafecastPy
import notecard.notecard.notecard as notecard
from periphery import Serial

from bme280 import BME280
from enviroplus import gas
from pms5003 import PMS5003, ReadTimeoutError as pmsReadTimeoutError

try:
    from smbus2 import SMBus
except ImportError:
    from smbus import SMBus

try:
    # Transitional fix for breaking change in LTR559
    from ltr559 import LTR559
    ltr559 = LTR559()
except ImportError:
    import ltr559

logging.basicConfig(
    format='%(asctime)s.%(msecs)03d %(levelname)-8s %(message)s',
    level=logging.INFO,
    datefmt='%Y-%m-%d %H:%M:%S')

logging.info("""enviroplus_exporter.py - Expose readings from the Enviro+ sensor by Pimoroni in Prometheus format

Press Ctrl+C to exit!

""")

DEBUG = os.getenv('DEBUG', 'false') == 'true'

bus = SMBus(1)
bme280 = BME280(i2c_dev=bus)
pms5003 = PMS5003()

TEMPERATURE = Gauge('temperature','Temperature measured (*C)')
PRESSURE = Gauge('pressure','Pressure measured (hPa)')
HUMIDITY = Gauge('humidity','Relative humidity measured (%)')
OXIDISING = Gauge('oxidising','Mostly nitrogen dioxide but could include NO and Hydrogen (Ohms)')
REDUCING = Gauge('reducing', 'Mostly carbon monoxide but could include H2S, Ammonia, Ethanol, Hydrogen, Methane, Propane, Iso-butane (Ohms)')
NH3 = Gauge('NH3', 'mostly Ammonia but could also include Hydrogen, Ethanol, Propane, Iso-butane (Ohms)') 
LUX = Gauge('lux', 'current ambient light level (lux)')
PROXIMITY = Gauge('proximity', 'proximity, with larger numbers being closer proximity and vice versa')
PM1 = Gauge('PM1', 'Particulate Matter of diameter less than 1 micron. Measured in micrograms per cubic metre (ug/m3)')
PM25 = Gauge('PM25', 'Particulate Matter of diameter less than 2.5 microns. Measured in micrograms per cubic metre (ug/m3)')
PM10 = Gauge('PM10', 'Particulate Matter of diameter less than 10 microns. Measured in micrograms per cubic metre (ug/m3)')
CPU_TEMPERATURE = Gauge('cpu_temperature','CPU temperature measured (*C)')

OXIDISING_HIST = Histogram('oxidising_measurements', 'Histogram of oxidising measurements', buckets=(0, 10000, 15000, 20000, 25000, 30000, 35000, 40000, 45000, 50000, 55000, 60000, 65000, 70000, 75000, 80000, 85000, 90000, 100000))
REDUCING_HIST = Histogram('reducing_measurements', 'Histogram of reducing measurements', buckets=(0, 100000, 200000, 300000, 400000, 500000, 600000, 700000, 800000, 900000, 1000000, 1100000, 1200000, 1300000, 1400000, 1500000))
NH3_HIST = Histogram('nh3_measurements', 'Histogram of nh3 measurements', buckets=(0, 10000, 110000, 210000, 310000, 410000, 510000, 610000, 710000, 810000, 910000, 1010000, 1110000, 1210000, 1310000, 1410000, 1510000, 1610000, 1710000, 1810000, 1910000, 2000000))

# Setup InfluxDB
# You can generate an InfluxDB Token from the Tokens Tab in the InfluxDB Cloud UI
INFLUXDB_URL = os.getenv('INFLUXDB_URL', '')
INFLUXDB_TOKEN = os.getenv('INFLUXDB_TOKEN', '')
INFLUXDB_ORG_ID = os.getenv('INFLUXDB_ORG_ID', '')
INFLUXDB_BUCKET = os.getenv('INFLUXDB_BUCKET', '')
INFLUXDB_SENSOR_LOCATION = os.getenv('INFLUXDB_SENSOR_LOCATION', 'Adelaide')
INFLUXDB_TIME_BETWEEN_POSTS = int(os.getenv('INFLUXDB_TIME_BETWEEN_POSTS', '5'))
influxdb_client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG_ID)
influxdb_api = influxdb_client.write_api(write_options=SYNCHRONOUS)

# Setup Luftdaten
LUFTDATEN_TIME_BETWEEN_POSTS = int(os.getenv('LUFTDATEN_TIME_BETWEEN_POSTS', '30'))

# Setup Safecast
SAFECAST_TIME_BETWEEN_POSTS = int(os.getenv('SAFECAST_TIME_BETWEEN_POSTS', '300'))
SAFECAST_DEV_MODE = os.getenv('SAFECAST_DEV_MODE', 'false') == 'true'
SAFECAST_API_KEY = os.getenv('SAFECAST_API_KEY', '')
SAFECAST_API_KEY_DEV = os.getenv('SAFECAST_API_KEY_DEV', '')
SAFECAST_LATITUDE = os.getenv('SAFECAST_LATITUDE', '')
SAFECAST_LONGITUDE = os.getenv('SAFECAST_LONGITUDE', '')
SAFECAST_DEVICE_ID = int(os.getenv('SAFECAST_DEVICE_ID', '226'))
SAFECAST_LOCATION_NAME = os.getenv('SAFECAST_LOCATION_NAME', '')
if SAFECAST_DEV_MODE:
    # Post to the dev API
    safecast = SafecastPy.SafecastPy(
        api_key=SAFECAST_API_KEY_DEV,
        api_url=SafecastPy.DEVELOPMENT_API_URL,
    )
else:
    # Post to the production API
    safecast = SafecastPy.SafecastPy(
        api_key=SAFECAST_API_KEY,
    )

# Setup Blues Notecard
NOTECARD_TIME_BETWEEN_POSTS = int(os.getenv('NOTECARD_TIME_BETWEEN_POSTS', '600'))

def get_cpu_temperature():
    """Get the temperature from the Raspberry Pi CPU"""
    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
        temp = f.read()
        temp = int(temp) / 1000.0
        CPU_TEMPERATURE.set(temp)

def get_temperature(temperature_compensation):
    """Get temperature from the weather sensor"""
    # Increase the temperature_compensation to reduce the temperature.
    # Decrease it to increase the temperature.
    temperature = bme280.get_temperature()

    if temperature_compensation:
        temperature = temperature - temperature_compensation

    TEMPERATURE.set(temperature)   # Set to a given value

def get_pressure():
    """Get pressure from the weather sensor"""
    pressure = bme280.get_pressure()
    PRESSURE.set(pressure)

def get_humidity(humidity_compensation):
    """Get humidity from the weather sensor"""
    # Increase the humidity_compensation to increase the humidity.
    # Decrease it to decrease the humidity.
    humidity = bme280.get_humidity()

    if humidity_compensation:
        humidity = humidity + humidity_compensation

    HUMIDITY.set(humidity)

def get_gas():
    """Get all gas readings"""
    readings = gas.read_all()

    OXIDISING.set(readings.oxidising)
    OXIDISING_HIST.observe(readings.oxidising)

    REDUCING.set(readings.reducing)
    REDUCING_HIST.observe(readings.reducing)

    NH3.set(readings.nh3)
    NH3_HIST.observe(readings.nh3)

def get_light():
    """Get all light readings"""
    lux = ltr559.get_lux()
    prox = ltr559.get_proximity()

    LUX.set(lux)
    PROXIMITY.set(prox)

def get_particulates():
    """Get the particulate matter readings"""
    try:
        pms_data = pms5003.read()
    except pmsReadTimeoutError:
        logging.warning("Failed to read PMS5003")
    else:
        PM1.set(pms_data.pm_ug_per_m3(1.0))
        PM25.set(pms_data.pm_ug_per_m3(2.5))
        PM10.set(pms_data.pm_ug_per_m3(10))

def collect_all_data():
    """Collects all the data currently set"""
    sensor_data = {}
    sensor_data['temperature'] = TEMPERATURE.collect()[0].samples[0].value
    sensor_data['humidity'] = HUMIDITY.collect()[0].samples[0].value
    sensor_data['pressure'] = PRESSURE.collect()[0].samples[0].value
    sensor_data['oxidising'] = OXIDISING.collect()[0].samples[0].value
    sensor_data['reducing'] = REDUCING.collect()[0].samples[0].value
    sensor_data['nh3'] = NH3.collect()[0].samples[0].value
    sensor_data['lux'] = LUX.collect()[0].samples[0].value
    sensor_data['proximity'] = PROXIMITY.collect()[0].samples[0].value
    sensor_data['pm1'] = PM1.collect()[0].samples[0].value
    sensor_data['pm25'] = PM25.collect()[0].samples[0].value
    sensor_data['pm10'] = PM10.collect()[0].samples[0].value
    sensor_data['cpu_temperature'] = CPU_TEMPERATURE.collect()[0].samples[0].value
    return sensor_data

def post_to_influxdb():
    """Post all sensor data to InfluxDB"""
    name = 'enviroplus'
    tag = ['location', 'adelaide']
    while True:
        time.sleep(INFLUXDB_TIME_BETWEEN_POSTS)
        data_points = []
        epoch_time_now = round(time.time())
        sensor_data = collect_all_data()
        for field_name in sensor_data:
            data_points.append(Point('enviroplus').tag('location', INFLUXDB_SENSOR_LOCATION).field(field_name, sensor_data[field_name]))
        try:
            influxdb_api.write(bucket=INFLUXDB_BUCKET, record=data_points)
            if DEBUG:
                logging.info('InfluxDB response: OK')
        except Exception as exception:
            logging.warning('Exception sending to InfluxDB: {}'.format(exception))

def post_to_luftdaten():
    """Post relevant sensor data to luftdaten.info"""
    """Code from: https://github.com/sepulworld/balena-environ-plus"""
    LUFTDATEN_SENSOR_UID = 'raspi-' + get_serial_number()
    while True:
        time.sleep(LUFTDATEN_TIME_BETWEEN_POSTS)
        sensor_data = collect_all_data()
        values = {}
        values["P2"] = sensor_data['pm25']
        values["P1"] = sensor_data['pm10']
        values["temperature"] = "{:.2f}".format(sensor_data['temperature'])
        values["pressure"] = "{:.2f}".format(sensor_data['pressure'] * 100)
        values["humidity"] = "{:.2f}".format(sensor_data['humidity'])
        pm_values = dict(i for i in values.items() if i[0].startswith('P'))
        temperature_values = dict(i for i in values.items() if not i[0].startswith('P'))
        try:
            response_pin_1 = requests.post('https://api.luftdaten.info/v1/push-sensor-data/',
                json={
                    "software_version": "enviro-plus 0.0.1",
                    "sensordatavalues": [{"value_type": key, "value": val} for
                                        key, val in pm_values.items()]
                },
                headers={
                    "X-PIN":    "1",
                    "X-Sensor": LUFTDATEN_SENSOR_UID,
                    "Content-Type": "application/json",
                    "cache-control": "no-cache"
                }
            )

            response_pin_11 = requests.post('https://api.luftdaten.info/v1/push-sensor-data/',
                    json={
                        "software_version": "enviro-plus 0.0.1",
                        "sensordatavalues": [{"value_type": key, "value": val} for
                                            key, val in temperature_values.items()]
                    },
                    headers={
                        "X-PIN":    "11",
                        "X-Sensor": LUFTDATEN_SENSOR_UID,
                        "Content-Type": "application/json",
                        "cache-control": "no-cache"
                    }
            )

            if response_pin_1.ok and response_pin_11.ok:
                if DEBUG:
                    logging.info('Luftdaten response: OK')
            else:
                logging.warning('Luftdaten response: Failed')
        except Exception as exception:
            logging.warning('Exception sending to Luftdaten: {}'.format(exception))

def post_to_safecast():
    """Post all sensor data to Safecast.org"""
    while True:
        time.sleep(SAFECAST_TIME_BETWEEN_POSTS)
        sensor_data = collect_all_data()
        try:
            measurement = safecast.add_measurement(json={
                'latitude': SAFECAST_LATITUDE,
                'longitude': SAFECAST_LONGITUDE,
                'value': sensor_data['pm1'],
                'unit': 'PM1 ug/m3',
                'captured_at': datetime.datetime.now().astimezone().isoformat(),
                'device_id': SAFECAST_DEVICE_ID,  # Enviro+
                'location_name': SAFECAST_LOCATION_NAME,
                'height': None
            })
            if DEBUG:
                logging.info('Safecast PM1 measurement created, id: {}'.format(measurement['id']))

            measurement = safecast.add_measurement(json={
                'latitude': SAFECAST_LATITUDE,
                'longitude': SAFECAST_LONGITUDE,
                'value': sensor_data['pm25'],
                'unit': 'PM2.5 ug/m3',
                'captured_at': datetime.datetime.now().astimezone().isoformat(),
                'device_id': SAFECAST_DEVICE_ID,  # Enviro+
                'location_name': SAFECAST_LOCATION_NAME,
                'height': None
            })
            if DEBUG:
                logging.info('Safecast PM2.5 measurement created, id: {}'.format(measurement['id']))

            measurement = safecast.add_measurement(json={
                'latitude': SAFECAST_LATITUDE,
                'longitude': SAFECAST_LONGITUDE,
                'value': sensor_data['pm10'],
                'unit': 'PM10 ug/m3',
                'captured_at': datetime.datetime.now().astimezone().isoformat(),
                'device_id': SAFECAST_DEVICE_ID,  # Enviro+
                'location_name': SAFECAST_LOCATION_NAME,
                'height': None
            })
            if DEBUG:
                logging.info('Safecast PM10 measurement created, id: {}'.format(measurement['id']))

            measurement = safecast.add_measurement(json={
                'latitude': SAFECAST_LATITUDE,
                'longitude': SAFECAST_LONGITUDE,
                'value': sensor_data['temperature'],
                'unit': 'Temperature C',
                'captured_at': datetime.datetime.now().astimezone().isoformat(),
                'device_id': SAFECAST_DEVICE_ID,  # Enviro+
                'location_name': SAFECAST_LOCATION_NAME,
                'height': None
            })
            if DEBUG:
                logging.info('Safecast Temperature measurement created, id: {}'.format(measurement['id']))

            measurement = safecast.add_measurement(json={
                'latitude': SAFECAST_LATITUDE,
                'longitude': SAFECAST_LONGITUDE,
                'value': sensor_data['humidity'],
                'unit': 'Humidity %',
                'captured_at': datetime.datetime.now().astimezone().isoformat(),
                'device_id': SAFECAST_DEVICE_ID,  # Enviro+
                'location_name': SAFECAST_LOCATION_NAME,
                'height': None
            })
            if DEBUG:
                logging.info('Safecast Humidity measurement created, id: {}'.format(measurement['id']))

            measurement = safecast.add_measurement(json={
                'latitude': SAFECAST_LATITUDE,
                'longitude': SAFECAST_LONGITUDE,
                'value': sensor_data['cpu_temperature'],
                'unit': 'CPU temperature C',
                'captured_at': datetime.datetime.now().astimezone().isoformat(),
                'device_id': SAFECAST_DEVICE_ID,  # Enviro+
                'location_name': SAFECAST_LOCATION_NAME,
                'height': None
            })
            if DEBUG:
                logging.info('Safecast CPU temperature measurement created, id: {}'.format(measurement['id']))
        except Exception as exception:
            logging.warning('Exception sending to Safecast: {}'.format(exception))

def post_to_notehub():
    """Post all sensor data to Notehub.io"""
    while True:
        time.sleep(NOTECARD_TIME_BETWEEN_POSTS)
        notecard_port = Serial("/dev/ttyACM0", 9600)
        try:
            card = notecard.OpenSerial(notecard_port)
        except Exception as exception:
            raise Exception("Error opening notecard: {}".format(exception))
        # Setup data
        sensor_data = collect_all_data()
        for sensor_data_key in sensor_data:
            data_unit = None
            if 'temperature' in sensor_data_key:
                data_unit = '°C'
            elif 'humidity' in sensor_data_key:
                data_unit = '%RH'
            elif 'pressure' in sensor_data_key:
                data_unit = 'hPa'
            elif 'oxidising' in sensor_data_key or 'reducing' in sensor_data_key or 'nh3' in sensor_data_key:
                data_unit = 'kOhms'
            elif 'proximity' in sensor_data_key:
                pass
            elif 'lux' in sensor_data_key:
                data_unit = 'Lux'
            elif 'pm' in sensor_data_key:
                data_unit = 'ug/m3'
            request = {'req':'note.add','body':{sensor_data_key:sensor_data[sensor_data_key], 'units':data_unit}}
            try:
                response = card.Transaction(request)
                if DEBUG:
                    logging.info('Notecard response: {}'.format(response))
            except Exception as exception:
                logging.warning('Notecard data setup error: {}'.format(exception))
        # Sync data with Notehub
        request = {'req':'service.sync'}
        try:
            response = card.Transaction(request)
            if DEBUG:
                logging.info('Notecard response: {}'.format(response))
        except Exception as exception:
            logging.warning('Notecard sync error: {}'.format(exception))

def get_serial_number():
    """Get Raspberry Pi serial number to use as LUFTDATEN_SENSOR_UID"""
    with open('/proc/cpuinfo', 'r') as f:
        for line in f:
            if line[0:6] == 'Serial':
                return str(line.split(":")[1].strip())

def str_to_bool(value):
    if value.lower() in {'false', 'f', '0', 'no', 'n'}:
        return False
    elif value.lower() in {'true', 't', '1', 'yes', 'y'}:
        return True
    raise ValueError('{} is not a valid boolean value'.format(value))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("-b", "--bind", metavar='ADDRESS', default='0.0.0.0', help="Specify alternate bind address [default: 0.0.0.0]")
    parser.add_argument("-p", "--port", metavar='PORT', default=8000, type=int, help="Specify alternate port [default: 8000]")
    parser.add_argument("-t", "--temp", metavar='TEMPERATURE', type=float, help="The temperature compensation value to get better temperature results when the Enviro+ pHAT is too close to the Raspberry Pi board")
    parser.add_argument("-h", "--humid", metavar='HUMIDITY', type=float, help="The humidity compensation value to get better temperature results when the Enviro+ pHAT is too close to the Raspberry Pi board")
    parser.add_argument("-d", "--debug", metavar='DEBUG', type=str_to_bool, help="Turns on more vebose logging, showing sensor output and post responses [default: false]")
    parser.add_argument("-i", "--influxdb", metavar='INFLUXDB', type=str_to_bool, default='false', help="Post sensor data to InfluxDB Cloud [default: false]")
    parser.add_argument("-l", "--luftdaten", metavar='LUFTDATEN', type=str_to_bool, default='false', help="Post sensor data to Luftdaten.info [default: false]")
    parser.add_argument("-s", "--safecast", metavar='SAFECAST', type=str_to_bool, default='false', help="Post sensor data to Safecast.org [default: false]")
    parser.add_argument("-n", "--notecard", metavar='NOTECARD', type=str_to_bool, default='false', help="Post sensor data to Notehub.io via Notecard LTE [default: false]")
    args = parser.parse_args()

    # Start up the server to expose the metrics.
    start_http_server(addr=args.bind, port=args.port)
    # Generate some requests.

    if args.debug:
        DEBUG = True

    if args.temp:
        logging.info("Using temperature compensation, reducing the output value by {}° to account for heat leakage from Raspberry Pi board".format(args.temp))

    if args.humid:
        logging.info("Using humidity compensation, reducing the output value by {}° to account for heat leakage from Raspberry Pi board".format(args.temp))

    if args.influxdb:
        # Post to InfluxDB in another thread
        logging.info("Sensor data will be posted to InfluxDB every {} seconds".format(INFLUXDB_TIME_BETWEEN_POSTS))
        influx_thread = Thread(target=post_to_influxdb)
        influx_thread.start()

    if args.luftdaten:
        # Post to Luftdaten in another thread
        LUFTDATEN_SENSOR_UID = 'raspi-' + get_serial_number()
        logging.info("Sensor data will be posted to Luftdaten every {} seconds for the UID {}".format(LUFTDATEN_TIME_BETWEEN_POSTS, LUFTDATEN_SENSOR_UID))
        luftdaten_thread = Thread(target=post_to_luftdaten)
        luftdaten_thread.start()

    if args.safecast:
        # Post to Safecast in another thread
        safecast_api_url = SafecastPy.PRODUCTION_API_URL
        if SAFECAST_DEV_MODE:
            safecast_api_url = SafecastPy.DEVELOPMENT_API_URL
        logging.info("Sensor data will be posted to {} every {} seconds".format(safecast_api_url, SAFECAST_TIME_BETWEEN_POSTS))
        influx_thread = Thread(target=post_to_safecast)
        influx_thread.start()

    if args.notecard:
        # Post to Notehub via Notecard in another thread
        logging.info("Sensor data will be posted to Notehub via Notecard every {} seconds".format(NOTECARD_TIME_BETWEEN_POSTS))
        notecard_thread = Thread(target=post_to_notehub)
        notecard_thread.start()

    logging.info("Listening on http://{}:{}".format(args.bind, args.port))

    while True:
        get_temperature(args.temp)
        get_pressure()
        get_humidity(args.humid)
        get_gas()
        get_light()
        get_particulates()
        get_cpu_temperature()
        if DEBUG:
            logging.info('Sensor data: {}'.format(collect_all_data()))
