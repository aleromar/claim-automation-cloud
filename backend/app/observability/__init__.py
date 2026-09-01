"""Telemetry package (otel-observability spec, D28).

Manual OTel pipelines: TracerProvider + LoggerProvider with the Azure Monitor
exporters. No distro, no metrics, no live metrics. Never raises (REQ-6.3) —
a telemetry failure at function_app.py import time would fail indexing of
every function. The global TracerProvider installed here is the 5a2 LLM seam.
"""

from app.observability.setup import flush_telemetry, instrument_fastapi, setup_telemetry

__all__ = ["setup_telemetry", "instrument_fastapi", "flush_telemetry"]
