"""This module contains all helper function for the mango framework"""
import logging

FIELD_STYLES = dict(
    asctime=dict(color='cyan'),
    hostname=dict(color='magenta'),
    levelname=dict(color='blue', bold=False),
    programname=dict(color='cyan'),
    name=dict(color='magenta'),
    module=dict(color='cyan'),
    lineno=dict(color='magenta'),
    process=dict(color='blue'),
    message=dict(color='white'),
    message_type=dict(color='green')
)
MESSAGE_TYPES = ['received', 'handling', 'request', 'sending']

LEVEL_STYLES = {
    'DEBUG': {"color": "blue"},
    'INFO': {"color": "black"},
    'WARNING': {"color": "yellow"},
    'ERROR': {"color": "red"},
    'CRITICAL': {"color": 'red'}
}

LEVEL_FORMATS = {
    "INFO": "%(asctime)s - %(process)d: %(name)s  %(message_type)s  %("
            "message)s",
    # "INFO": "%(levelname)s - %(message)s",
    # "INFO": '%(asctime)s %(hostname)s %(name)s[%(process)d] %(levelname)s
    # %(message)s',
    "DEBUG": "%(asctime)s - %(levelname)s - "
             "%(module)s::%(funcName)s @ %(lineno)d - %(message)s",
    "WARNING": "%(message)s",
    "SIMULATION": "%(message)s",
}


def configure_logger(name, log_level):
    """
    configure a custom python logger
    :param name: name of the logger
    :param log_level: current log level
    :return:
    """
    log_format = LEVEL_FORMATS[logging.getLevelName(log_level)]
    log = logging.getLogger(name)
    log.setLevel(log_level)

    field_styles = FIELD_STYLES.copy()
    m_type_filter = ContextFilter(MESSAGE_TYPES)
    log.addFilter(m_type_filter)
    return log


class ContextFilter(logging.Filter):
    """
    This is a filter which injects contextual information into the log.
    """

    def __init__(self, custom_fields):
        super().__init__()
        self.custom_fields = custom_fields

    def filter(self, record):
        has_message_type = False
        for message_type in self.custom_fields:
            if message_type in record.msg.lower():
                has_message_type = True
                record.message_type = message_type
                break
        if not has_message_type:
            record.message_type = ''
        return True
