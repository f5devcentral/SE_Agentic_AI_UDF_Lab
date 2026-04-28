import logging
import sys
from opentelemetry.sdk._logs import LoggingHandler
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry import _logs

import os

def configure_logging(service_name):
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")

    provider = LoggerProvider()
    _logs.set_logger_provider(provider)

    otlp_exporter = OTLPLogExporter(endpoint=otlp_endpoint, insecure=True)
    provider.add_log_record_processor(BatchLogRecordProcessor(otlp_exporter))

    handler = LoggingHandler(level=logging.INFO, logger_provider=provider)
    formatter = logging.Formatter(f"%(asctime)s | {service_name} | %(levelname)s | %(message)s")
    handler.setFormatter(formatter)

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return logger

