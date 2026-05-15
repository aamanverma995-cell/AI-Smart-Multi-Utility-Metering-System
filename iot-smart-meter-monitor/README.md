# Smart Meter — Raspberry Pi 5

A multi-utility IoT smart meter that measures electricity (AC), water flow, and
natural gas concentration. Data is logged locally to SQLite, published to MQTT,
pushed to InfluxDB Cloud, and viewable on a local Flask web dashboard.
Threshold alerts are delivered via Telegram.

## Hardware

| Component | Purpose | Interface |
|-----------|---------|-----------|
| PZEM-004T V3.0 | AC voltage, current, power, energy, frequency, PF | UART via USB-TTL adapter |
| YF-S201 | Water flow rate (L/min) | GPIO pulse counting (BCM 17) |
| MQ-4 | Natural gas concentration | Analog → ADS1115 ADC → I2C |
| ADS1115 | 16-bit I2C ADC for MQ-4 | I2C (SDA=GPIO2, SCL=GPIO3) |

## Wiring Summary

### PZEM-004T (UART)
```
PZEM TX  →  Pi RX  (/dev/ttyUSB0 if using USB-TTL adapter)
PZEM RX  →  Pi TX
PZEM VCC →  5V
PZEM GND →  GND
WARNING: AC live wires must be connected by a qualified electrician.
```

### YF-S201 (Water Flow)
```
Red   →  5V  (pin 2)
Black →  GND (pin 6)
Yellow → GPIO17 (BCM, pin 11) + 10kΩ pull-up to 3.3V
```

### ADS1115 + MQ-4 (Gas)
```
ADS1115: VDD→3.3V, GND→GND, SDA→GPIO2 (pin 3), SCL→GPIO3 (pin 5), ADDR→GND
MQ-4:    VCC→5V, GND→GND, AOUT→ADS1115 A0
```

## Software Setup

```bash
# 1. Enable I2C and UART on the Pi
sudo raspi-config
# Interfaces > I2C > Enable
# Interfaces > Serial Port > Enable (disable login shell, keep hardware)

# 2. Clone / copy project to the Pi
cd /home/pi
git clone <repo-url> smart-meter
cd smart-meter

# 3. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 4. Install dependencies
pip install -r requirements.txt

# 5. Configure environment
cp env.example .env
nano .env          # Fill in your PZEM port, Telegram token, etc.

# 6. Create data directory
mkdir -p data

# 7. Run manually for testing
python src/main.py

# 8. Open dashboard
# http://<Pi-IP>:5000
```

## Run as a System Service

```bash
sudo cp smart_meter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable smart_meter
sudo systemctl start smart_meter
sudo journalctl -u smart_meter -f   # View logs
```

## Project Structure

```
smart-meter/
├── src/
│   ├── main.py                  # Entry point
│   ├── config.py                # All settings (reads .env)
│   ├── sensors/
│   │   ├── electricity.py       # PZEM-004T UART Modbus driver
│   │   ├── water_flow.py        # YF-S201 GPIO interrupt driver
│   │   └── gas.py               # MQ-4 via ADS1115 I2C ADC driver
│   ├── services/
│   │   ├── database.py          # SQLite persistence
│   │   ├── mqtt_service.py      # Paho MQTT publisher
│   │   ├── influx_service.py    # InfluxDB Cloud writer
│   │   └── alert_service.py     # Telegram threshold alerts
│   └── web/
│       ├── app.py               # Flask API + dashboard server
│       └── templates/
│           └── index.html       # Live Chart.js dashboard
├── data/                        # SQLite database (auto-created)
├── requirements             .txt
├── env.example     # Template .env file
├── smart_meter.service          # Systemd unit file
└── README.md
```

## Calibration

- **YF-S201**: Default 7.5 pulses/litre. Test with a measured container and
  adjust `WATER_PULSES_PER_LITRE` in `.env`.
- **MQ-4**: Power on for 24–48 h burn-in. Note the ADC count in clean air
  and set `GAS_ALERT_THRESHOLD` to a value 20–30% above it.
- **PZEM-004T**: No software calibration needed; hardware-calibrated at the factory.

## Dashboard

Open `http://<Pi-IP>:5000` from any browser on the local network.
The page auto-refreshes every 10 seconds and displays live KPI cards plus
scrolling line charts for power, water flow, voltage, and gas level.
