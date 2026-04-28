from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
import os
import time

def register_metrics(app, service_name):
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=otlp_endpoint, insecure=True)
    )
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)
    meter = metrics.get_meter(service_name)

    request_counter = meter.create_counter(
        "http_requests_total",
        description="Total HTTP requests",
    )
    request_duration = meter.create_histogram(
        "http_request_duration_seconds",
        description="HTTP request duration in seconds",
    )

    @app.before_request
    def before_request():
        from flask import g, request
        g.start_time = time.time()

    @app.after_request
    def after_request(response):
        from flask import g, request
        duration = time.time() - g.get("start_time", time.time())
        labels = {"method": request.method, "endpoint": request.path, "status": str(response.status_code)}
        request_counter.add(1, labels)
        request_duration.record(duration, labels)
        return response

    return meter
