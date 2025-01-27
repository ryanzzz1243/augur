# from augur import ROOT_AUGUR_DIRECTORY
import multiprocessing
import logging
import os
from pathlib import Path
import shutil

from augur.application.db.session import DatabaseSession
from augur.application.config import AugurConfig

logger = logging.getLogger(__name__)
with DatabaseSession(logger) as session:

    augur_config = AugurConfig(logger, session)
    
        
    # ROOT_AUGUR_DIRECTORY = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

    # base_log_dir = ROOT_AUGUR_DIRECTORY + "/logs/"

    # Path(base_log_dir).mkdir(exist_ok=True)

    workers = multiprocessing.cpu_count() * 2 + 1
    umask = 0o007
    reload = True

    # set the log location for gunicorn    
    logs_directory = augur_config.get_value('Logging', 'logs_directory')
    accesslog = f"{logs_directory}/gunicorn.log"
    errorlog = f"{logs_directory}/gunicorn.log"

    ssl_bool = augur_config.get_value('Server', 'ssl')

    if ssl_bool is True: 

        workers = int(augur_config.get_value('Server', 'workers'))
        bind = '%s:%s' % (augur_config.get_value("Server", "host"), augur_config.get_value("Server", "port"))
        timeout = int(augur_config.get_value('Server', 'timeout'))
        certfile = str(augur_config.get_value('Server', 'ssl_cert_file'))
        keyfile = str(augur_config.get_value('Server', 'ssl_key_file'))
        
    else: 
        workers = int(augur_config.get_value('Server', 'workers'))
        bind = '%s:%s' % (augur_config.get_value("Server", "host"), augur_config.get_value("Server", "port"))
        timeout = int(augur_config.get_value('Server', 'timeout'))
