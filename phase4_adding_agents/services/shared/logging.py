import logging
import sys
import os
from opentelemetry.sdk._logs import LoggingHandler, LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry import _logs


def configure_logging(service_name):
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")

    provider = LoggerProvider()
    _logs.set_logger_provider(provider)

    otlp_exporter = OTLPLogExporter(endpoint=otlp_endpoint, insecure=True)
    provider.add_log_record_processor(BatchLogRecordProcessor(otlp_exporter))

    formatter = logging.Formatter(f"%(asctime)s | {service_name} | %(levelname)s | %(message)s")

    otlp_handler = LoggingHandler(level=logging.INFO, logger_provider=provider)
    otlp_handler.setFormatter(formatter)

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.addHandler(otlp_handler)
    logger.addHandler(stdout_handler)
    return logger
