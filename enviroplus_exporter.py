#!/usr/bin/env python
import os
import random
import time
import logging
import argparse
from threading import Thread

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
from prometheus_client import start_http_server, Gauge, Histogram

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

# Get the temperature of the CPU for compensation
def get_cpu_temperature():
    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
        temp = f.read()
        temp = int(temp) / 1000.0
    return temp

def get_temperature(factor):
    """Get temperature from the weather sensor"""
    # Tuning factor for compensation. Decrease this number to adjust the
    # temperature down, and increase to adjust up
    raw_temp = bme280.get_temperature()

    if factor:
        cpu_temps = [get_cpu_temperature()] * 5
        cpu_temp = get_cpu_temperature()
        # Smooth out with some averaging to decrease jitter
        cpu_temps = cpu_temps[1:] + [cpu_temp]
        avg_cpu_temp = sum(cpu_temps) / float(len(cpu_temps))
        temperature = raw_temp - ((avg_cpu_temp - raw_temp) / factor)
    else:
        temperature = raw_temp

    TEMPERATURE.set(temperature)   # Set to a given value

def get_pressure():
    """Get pressure from the weather sensor"""
    pressure = bme280.get_pressure()
    PRESSURE.set(pressure)

def get_humidity():
    """Get humidity from the weather sensor"""
    humidity = bme280.get_humidity()
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
        logging.warn("Failed to read PMS5003")
    else:
        PM1.set(pms_data.pm_ug_per_m3(1.0))
        PM25.set(pms_data.pm_ug_per_m3(2.5))
        PM10.set(pms_data.pm_ug_per_m3(10))


def post_to_influxdb():
    """Post all sensor data to InfluxDB"""
    while True:
        time.sleep(INFLUXDB_TIME_BETWEEN_POSTS)
        name = 'enviroplus'
        tag = ['location', 'adelaide']
        fields = {}
        data_points = []
        epoch_time_now = round(time.time())
        fields['temperature'] = TEMPERATURE.collect()[0].samples[0].value
        fields['humidity'] = HUMIDITY.collect()[0].samples[0].value
        fields['pressure'] = PRESSURE.collect()[0].samples[0].value
        fields['oxidising'] = OXIDISING.collect()[0].samples[0].value
        fields['reducing'] = REDUCING.collect()[0].samples[0].value
        fields['nh3'] = NH3.collect()[0].samples[0].value
        fields['lux'] = LUX.collect()[0].samples[0].value
        fields['proximity'] = PROXIMITY.collect()[0].samples[0].value
        fields['pm1'] = PM1.collect()[0].samples[0].value
        fields['pm25'] = PM25.collect()[0].samples[0].value
        fields['pm10'] = PM10.collect()[0].samples[0].value
        for field_name in fields:
            data_points.append(Point('enviroplus').tag('location', INFLUXDB_SENSOR_LOCATION).field(field_name, fields[field_name]))
        influxdb_api.write(bucket=INFLUXDB_BUCKET, record=data_points)


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
    parser.add_argument("-f", "--factor", metavar='FACTOR', type=float, help="The compensation factor to get better temperature results when the Enviro+ pHAT is too close to the Raspberry Pi board")
    parser.add_argument("-i", "--influxdb", metavar='INFLUXDB', type=str_to_bool, default='false', help="Post sensor data to InfluxDB [default: false]")
    args = parser.parse_args()

    # Start up the server to expose the metrics.
    start_http_server(addr=args.bind, port=args.port)
    # Generate some requests.
    
    if args.factor:
        logging.info("Using compensating algorithm (factor={}) to account for heat leakage from Raspberry Pi board".format(args.factor))

    if args.influxdb:
        # Post to InfluxDB on another thread
        logging.info("Sensor data will be posted to InfluxDB every {} seconds".format(INFLUXDB_TIME_BETWEEN_POSTS))
        influx_thread = Thread(target=post_to_influxdb)
        influx_thread.start()

    logging.info("Listening on http://{}:{}".format(args.bind, args.port))

    while True:
        get_temperature(args.factor)
        get_pressure()
        get_humidity()
        get_gas()
        get_light()
        get_particulates()
