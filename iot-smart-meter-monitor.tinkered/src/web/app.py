"""
web/app.py — Flask web dashboard
Serves live readings and historical charts.

Endpoints:
  GET /            — Dashboard HTML page
  GET /api/latest  — Latest 50 readings as JSON
  GET /api/summary — Today's kWh + litres totals
"""

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from services.database import DatabaseService

from flask import Flask, jsonify, render_template

logger = logging.getLogger(__name__)

app   = Flask(__name__, template_folder="templates")
_db   = DatabaseService()


def init_web(db: DatabaseService) -> None:
    """Inject the shared database service instance."""
    global _db
    _db = db


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/latest")
def api_latest():
    rows = _db.get_latest(50)
    return jsonify(rows)


@app.route("/api/summary")
def api_summary():
    return jsonify({
        "energy_today_kwh": _db.get_energy_today(),
        "water_today_litres": _db.get_water_today(),
    })


def run_dashboard(db: DatabaseService) -> None:
    init_web(db)
    app.run(
        host=config.FLASK_HOST,
        port=config.FLASK_PORT,
        debug=config.FLASK_DEBUG,
        use_reloader=False,
    )
