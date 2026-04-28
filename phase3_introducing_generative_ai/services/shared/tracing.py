
from opentelemetry import trace, propagate
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
import os

# Global tracer (this is what your app.py expects)
tracer = None


def init_tracing(app, service_name):
    global tracer

    resource = Resource(attributes={
        "service.name": service_name,
        "service.namespace": "demo-travel",
        "service.version": "1.0"
    })

    provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(provider)

    tracer = trace.get_tracer(service_name)
    print(f"[Tracing] Initialized tracer for service: {service_name} -> {tracer}")

    otlp_exporter = OTLPSpanExporter(
        endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "otel-collector:4317"),
        insecure=True
    )

    provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

    FlaskInstrumentor().instrument_app(app)
    RequestsInstrumentor().instrument()


def get_tracer(name: str = None):
    if name:
        return trace.get_tracer(name)
    return trace.get_tracer(__name__)


def inject_trace_headers(span=None):
    headers = {}
    propagate.inject(headers)
    return headers
