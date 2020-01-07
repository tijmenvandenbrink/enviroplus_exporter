#!/usr/bin/env python
import random
import time
import logging

from prometheus_client import start_http_server, Gauge, Histogram

from bme280 import BME280
from enviroplus import gas

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

logging.info("""enviroplus-exporter.py - Print readings from the Enviro+ sensor by Pimoroni.

Press Ctrl+C to exit!

""")

bus = SMBus(1)
bme280 = BME280(i2c_dev=bus)

TEMPERATURE = Gauge('temperature','Temperature measured (*C)')
PRESSURE = Gauge('pressure','Pressure measured (hPa)')
HUMIDITY = Gauge('humidity','Relative humidity measured (%)')
OXIDISING = Gauge('oxidising','Mostly nitrogen dioxide but could include NO and Hydrogen (Ohms)')
REDUCING = Gauge('reducing', 'Mostly carbon monoxide but could include H2S, Ammonia, Ethanol, Hydrogen, Methane, Propane, Iso-butane (Ohms)')
NH3 = Gauge('NH3', 'mostly Ammonia but could also include Hydrogen, Ethanol, Propane, Iso-butane (Ohms)') 
LUX = Gauge('lux', 'current ambient light level (lux)')
PROXIMITY = Gauge('proximity', 'proximity, with larger numbers being closer proximity and vice versa')

OXIDISING_HIST = Histogram('oxidising_measurements', 'Histogram of oxidising measurements', buckets=(0, 10000, 15000, 20000, 25000, 30000, 35000, 40000, 50000, 75000))

# Get the temperature of the CPU for compensation
def get_cpu_temperature():
    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
        temp = f.read()
        temp = int(temp) / 1000.0
    return temp

def get_temperature():
    """Get temperature from the weather sensor"""
    # Tuning factor for compensation. Decrease this number to adjust the
    # temperature down, and increase to adjust up
    factor = 0.85

    cpu_temps = [get_cpu_temperature()] * 5

    cpu_temp = get_cpu_temperature()
    # Smooth out with some averaging to decrease jitter
    cpu_temps = cpu_temps[1:] + [cpu_temp]
    avg_cpu_temp = sum(cpu_temps) / float(len(cpu_temps))
    raw_temp = bme280.get_temperature()
    temperature = raw_temp - ((avg_cpu_temp - raw_temp) / factor)
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
    NH3.set(readings.nh3)

def get_light():
    """Get all light readings"""
    lux = ltr559.get_lux()
    prox = ltr559.get_proximity()

    LUX.set(lux)
    PROXIMITY.set(prox)


if __name__ == '__main__':
# Start up the server to expose the metrics.
    start_http_server(addr='0.0.0.0', port=8000)
    # Generate some requests.

    while True:
        get_temperature()
        get_pressure()
        get_humidity()
        get_gas()
        get_light()
