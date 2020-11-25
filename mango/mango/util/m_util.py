"""This module contains all helper function for the mango framework"""
import csv
import logging
import io
from datetime import datetime

def configure_logger(name, log_level, log_file=None,
                     csv_format=False, log_file_mode='w'):
    """
    Configure a custom python logger
    :param name: name of the logger
    :param log_level: current log level
    param log_file: file the log should be written to, if not specified the
    log will only be displayed in the terminal
    :param log_file: file the log should be written to, if not specified the
    log will only be displayed in the terminal
    :param csv_format: if true use a csv Formatter
    :param log_file_mode: mode to open log file. (Also set by env variable.)
    :return: the logger object
    """
    log = logging.getLogger(name)
    log.setLevel(log_level)

    if log_file is not None:
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)

        hdlr = logging.FileHandler(log_file, mode=log_file_mode)
        if csv_format:
            hdlr.setFormatter(CsvFormatter())
        log.addHandler(hdlr)
    return log

class CsvFormatter(logging.Formatter):
    """
    Formats logging messages in a csv style
    """
    def __init__(self):
        super().__init__()
        self.output = io.StringIO()
        self.writer = csv.writer(self.output, delimiter=';')

    def format(self, record):
        msg = record.msg.split(';')
        # put time stamp and datetime as first two items in seconds
        row = [int(record.created), datetime.fromtimestamp(record.created)]
        row.extend(msg)
        self.writer.writerow(row)
        data = self.output.getvalue()
        self.output.truncate(0)
        self.output.seek(0)
        return data.strip()
