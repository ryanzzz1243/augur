#SPDX-License-Identifier: MIT
"""
Metrics that provide data about messages (of any form) & their associated activity
"""

import datetime
import sqlalchemy as s
import pandas as pd
from augur.api.util import register_metric

from augur.application.db.engine import create_database_engine
engine = create_database_engine()

@register_metric()
def messages(repo_group_id, repo_id=None, period='day', begin_date=None, end_date=None):
    """
    Returns a timeseries of all the messages in a project.
    
    :param repo_id: The repository's id
    :param repo_group_id: The repository's group id
    :param period: To set the periodicity to 'day', 'week', 'month' or 'year', defaults to 'day'
    :param begin_date: Specifies the begin date, defaults to '1970-1-1 00:00:00'
    :param end_date: Specifies the end date, defaults to datetime.now()
    :return: DataFrame of persons/period
    """

    if not begin_date:
        begin_date = '1970-1-1 00:00:01'
    if not end_date:
        end_date = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if repo_id:
        messages = s.sql.text("""
            SELECT
	          COUNT(msg_id) AS msg_count
	          FROM message
	          WHERE msg_timestamp BETWEEN :begin_date AND :end_date
	          AND repo_id = :repo_id
	          GROUP BY repo_id;
        """)

        results = pd.read_sql(messages, engine, params={'repo_id': repo_id, 'period': period,
                                                                'begin_date': begin_date, 'end_date': end_date})
    else:
        messages = s.sql.text("""
            SELECT
	          repo_id,
	          COUNT(msg_id) AS msg_count
	          FROM message
	          WHERE msg_timestamp BETWEEN :begin_date AND :end_date
            AND repo_id IN
                (SELECT repo_id FROM repo WHERE repo_group_id = :repo_group_id)
	          GROUP BY repo_id;
        """)

        results = pd.read_sql(messages, engine, params={'repo_group_id': repo_group_id, 'period': period,
                                                                'begin_date': begin_date, 'end_date': end_date})
    return results
