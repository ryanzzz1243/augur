#SPDX-License-Identifier: MIT

import pytest
import pandas as pd
from augur.api.metrics.pull_request import pull_request_ratio_merged_to_closed
from augur.api.metrics.release import release_frequency
from augur.api.metrics.contributor import sustained_contributors
from augur.api.metrics.message import messages

df_type = type(pd.DataFrame())

def test_pull_request_ratio_merged_to_closed():
    assert isinstance(pull_request_ratio_merged_to_closed(1, 1), df_type)
    assert isinstance(pull_request_ratio_merged_to_closed(10), df_type)

def test_release_frequency():
    # repo
    assert isinstance(release_frequency(1, 1, period='year'), df_type)

    # repo_group
    assert isinstance(release_frequency(10, period='year'), df_type)

    # begin_date & end_date
    assert isinstance(release_frequency(1, 1, period='month',
        begin_date='2020-01-01', end_date='2020-02-01'), df_type)
    assert isinstance(release_frequency(10, period='week',
        begin_date='2022-10-01', end_date='2022-11-01'), df_type)

def test_sustained_contributors():
    # repo
    assert isinstance(sustained_contributors(10, 25430, period='year'),
            df_type)

    # repo_group
    assert isinstance(sustained_contributors(10, period='year'), df_type)

    # begin_date & end_date
    assert isinstance(sustained_contributors(10, 25430, period='month',
        begin_date='2021-10-01', end_date='2021-11-01'), df_type)

    assert isinstance(sustained_contributors(10, period='week',
        begin_date='2021-10-01', end_date='2021-11-01'), df_type)

def test_messages():
    # repo
    assert isinstance(messages(10, 25430, period='year'), df_type)

    # repo_group
    assert isinstance(messages(10, period='year'), df_type)

    # begin_date & end_date
    assert isinstance(messages(10, 25430, period='month',
        begin_date='2021-10-01', end_date='2021-11-01'), df_type)

    assert isinstance(messages(10, period='week', begin_date='2021-10-01',
        end_date='2021-11-01'), df_type)
