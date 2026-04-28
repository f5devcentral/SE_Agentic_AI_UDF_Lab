from opentelemetry import trace, propagate
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.trace import SpanKind

try:
    from opentelemetry.instrumentation.flask import FlaskInstrumentor
    HAS_FLASK = True
except:
    HAS_FLASK = False

try:
    from opentelemetry.instrumentation.requests import RequestsInstrumentor
    HAS_REQUESTS = True
except:
    HAS_REQUESTS = False

try:
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    HAS_HTTPX = True
except:
    HAS_HTTPX = False

import os

tracer = None


def init_tracing(app=None, service_name="service"):
    global tracer

    resource = Resource(attributes={
        "service.name": service_name,
        "service.namespace": "demo-travel",
        "service.version": "1.0"
    })

    provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(provider)

    tracer = trace.get_tracer(service_name)

    otlp_exporter = OTLPSpanExporter(
        endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "otel-collector:4317"),
        insecure=True
    )

    provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

    if app is not None and HAS_FLASK:
        FlaskInstrumentor().instrument_app(app)

    if HAS_REQUESTS:
        RequestsInstrumentor().instrument()

    if HAS_HTTPX:
        HTTPXClientInstrumentor().instrument()

    return tracer


def inject_trace_headers(span=None, trace_context=None):
    headers = {}
    propagate.inject(headers)
    return headers


def extract_context(headers):
    return propagate.extract(headers)


def start_span_from_context(tracer_arg=None, span_name=None, trace_context=None, kind=SpanKind.INTERNAL):
    global tracer
    used_tracer = tracer_arg or tracer

    if used_tracer is None:
        raise RuntimeError("Tracer is not initialized. Call init_tracing() first.")

    ctx = propagate.extract(trace_context) if trace_context else None

    if ctx:
        return used_tracer.start_as_current_span(span_name, context=ctx, kind=kind)

    return used_tracer.start_as_current_span(span_name, kind=kind)
