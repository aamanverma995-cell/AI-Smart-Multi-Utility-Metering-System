# AI Smart Multi-Utility Metering & Anomaly Detection System

An AI-enabled embedded infrastructure platform designed for simultaneous monitoring of electricity, water, and gas utilities using edge-based analytics, mixed-signal sensing, and resilient IoT communication architectures.

This system integrates:

* Electrical telemetry acquisition
* Water-flow analytics
* Methane/gas monitoring
* Edge anomaly classification
* MQTT cloud synchronization
* SQLite offline persistence
* Real-time alerting systems

into a unified embedded framework optimized for:

* Smart-city deployments
* Industrial IoT infrastructure
* Smart-grid systems
* Residential utility automation
* Distributed telemetry environments

---

# Intellectual Property

## Patent Filing

This repository is associated with an independently authored Indian utility patent related to embedded multi-utility telemetry and anomaly detection architectures.

Patent Status: Request for Examination (RQ) Filed
Patent Type: Indian Utility Patent

The patent focuses on:

* AI-enabled utility monitoring
* Embedded edge analytics
* Mixed-signal sensing architectures
* Local anomaly detection
* Resilient telemetry persistence
* Smart-city infrastructure deployment

This repository documents the research, embedded architecture, and implementation concepts associated with the patented system.

---

# Intellectual Property

Indian Utility Patent Filed
Status: Request for Examination (RQ) Filed

Title:

> AI-Enabled Smart Multi-Utility Metering and Anomaly Detection System

The patent architecture focuses on:

* Unified utility sensing
* Edge-based telemetry analytics
* Local anomaly classification
* Resilient embedded persistence
* Scalable smart infrastructure deployments

---

# System Architecture

The embedded platform combines heterogeneous sensing systems into a unified edge-computing framework.

## Integrated Utility Subsystems

### Electrical Monitoring

* PZEM-004T power monitoring module
* Current sensing and voltage telemetry
* Real-time electrical diagnostics
* Power consumption analytics

### Water Monitoring

* YF-S201 Hall-effect water flow sensor
* Interrupt-driven pulse acquisition
* Real-time flow estimation
* Utility consumption monitoring

### Gas Monitoring

* MQ-4 methane sensor
* Analog acquisition through ADS1115 ADC
* Threshold-based anomaly detection
* Environmental safety monitoring

---

# Edge-Based Processing Architecture

To reduce cloud dependency and improve resilience:

* Utility analytics execute locally on-device
* SQLite persistence maintains offline telemetry retention
* MQTT synchronization asynchronously pushes telemetry to cloud dashboards
* Telegram Bot integration provides real-time anomaly alerts

The architecture continues operating during:

* Network failures
* Cloud interruptions
* Telemetry desynchronization events

---

# Engineering Challenges Solved

## Mixed-Signal Interfacing

Designed a custom acquisition pipeline to integrate:

* UART telemetry
* Analog sensor streams
* Interrupt-driven digital sensing

## EMI Mitigation

Resolved localized electromagnetic interference through:

* Grounding redesign
* Common-reference architecture
* Pull-down stabilization
* Signal-conditioning strategies

## Resilient Telemetry

Implemented local persistence and asynchronous synchronization to ensure system continuity during connectivity failures.

---

# Hardware Components

| Component       | Purpose                   |
| --------------- | ------------------------- |
| Raspberry Pi 5  | Embedded processing core  |
| PZEM-004T       | Electrical diagnostics    |
| MQ-4 Gas Sensor | Methane monitoring        |
| YF-S201         | Water flow telemetry      |
| ADS1115 ADC     | Analog signal acquisition |
| ACS712          | Current sensing           |
| ZMPT101B        | Voltage sensing           |

---

# Software Stack

## Programming & Databases

* Python
* SQLite
* Embedded Linux
* Linux Shell

## Communication & Cloud

* MQTT
* InfluxDB Cloud
* Telegram Bot API

## Embedded Architecture

* GPIO Interrupt Handling
* UART Communication
* I2C Sensor Integration
* Edge Analytics

---

# Research & Academic Relevance

This project aligns with research domains including:

* Embedded Systems
* Edge AI
* Smart Infrastructure
* Industrial IoT
* Smart Grid Telemetry
* Mixed-Signal Embedded Design
* Cyber-Physical Systems
* Resilient Edge Computing

---

# Future Enhancements

* TinyML anomaly prediction
* Edge neural inference
* FPGA-based telemetry acceleration
* Distributed utility mesh networks
* VLSI-integrated sensing modules
* Predictive maintenance analytics

---

# Prototype Circuit Diagram

![Prototype Circuit Diagram](documentation/diagram.png)

**Figure 1 — Prototype Circuit Diagram**

The embedded architecture integrates heterogeneous sensing systems into a unified edge-computing framework using Raspberry Pi 5 infrastructure. The diagram illustrates:

* Mixed-signal sensor interfacing
* UART-based power telemetry acquisition
* I2C ADS1115 analog acquisition architecture
* GPIO interrupt-driven pulse sensing
* MQTT telemetry synchronization
* Local SQLite persistence layer
* Cloud analytics and alerting framework

---

# Repository Structure

```bash
AI-Smart-Multi-Utility-Metering-System/
│
├── hardware/
│   ├── circuit_diagrams/
│   ├── sensor_interfaces/
│   └── architecture_designs/
│
├── software/
│   ├── telemetry_engine/
│   ├── mqtt_sync/
│   ├── anomaly_detection/
│   └── database_layer/
│
├── datasets/
├── documentation/
├── patent/
├── README.md
└── LICENSE
```

---

# Vision

The long-term vision of this platform is to create scalable intelligent utility infrastructure capable of supporting next-generation smart-city ecosystems through resilient, decentralized, and low-cost embedded sensing architectures.

---

# Author

Aman Rahul Verma
Mumbai, Maharashtra, India
GitHub: github.com/aamanverma995-cell/OmniUtility-AI
Email: [aamanverma995@gmail.com](mailto:aamanverma995@gmail.com)
