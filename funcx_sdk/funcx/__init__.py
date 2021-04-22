""" funcX : Fast function serving for clouds, clusters and supercomputers.

"""
import logging
from funcx.sdk.version import VERSION

__author__ = "The funcX team"
__version__ = VERSION

from funcx.sdk.client import FuncXClient
from funcx.utils.loggers import file_format_string, stream_format_string
from funcx.utils.loggers import set_file_logger, set_stream_logger


logging.getLogger('funcx').addHandler(logging.NullHandler())
