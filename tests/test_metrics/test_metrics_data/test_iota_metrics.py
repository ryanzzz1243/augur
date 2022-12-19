#SPDX-License-Identifier: MIT

import pytest
from augur.api.metrics.pull_request import pull_request_ratio_merged_to_closed
from augur.api.metrics.release import release_frequency
from augur.api.metrics.contributor import sustained_contributors
from augur.api.metrics.message import messages

def test_pull_request_ratio_merged_to_closed():
    # repo
    assert pull_request_ratio_merged_to_closed(1, 1).iloc[0]['ratio_merged_to_closed'] > 0

    # repo_group
    assert pull_request_ratio_merged_to_closed(10).iloc[0]['ratio_merged_to_closed'] > 0

def test_release_frequency():
    # repo
    assert release_frequency(1, 1, period='year').iloc[0]['releases_over_time'] > 0

    # repo_group
    assert release_frequency(10, period='year').iloc[0]['releases_over_time'] > 0

    # begin_date & end_date
    assert release_frequency(1, 1, period='month', begin_date='2020-01-01',
            end_date='2020-02-01').iloc[0]['releases_over_time'] > 0

    assert release_frequency(10, period='week', begin_date='2022-10-01',
            end_date='2022-11-01').iloc[0]['releases_over_time'] > 0

def test_sustained_contributors():
    # repo
    assert sustained_contributors(10, 25430, period='year').iloc[0]['total_contributions'] > 0

    # repo_group
    assert sustained_contributors(10, period='year').iloc[0]['total_contributions'] > 0

    # begin_date & end_date
    assert sustained_contributors(10, 25430, period='month',
            begin_date='2021-10-01',
            end_date='2021-11-01').iloc[0]['total_contributions'] > 0

    assert sustained_contributors(10, period='week', begin_date='2021-10-01',
            end_date='2021-11-01').iloc[0]['total_contributions'] > 0

def test_messages():
    # repo
    assert messages(10, 25430, period='year').iloc[0]['msg_count'] > 0

    # repo_group
    assert messages(10, period='year').iloc[0]['msg_count'] > 0

    # begin_date & end_date
    assert messages(10, 25430, period='month', begin_date='2021-10-01',
            end_date='2021-11-01').iloc[0]['msg_count'] > 0

    assert messages(10, period='week', begin_date='2021-10-01',
            end_date='2021-11-01').iloc[0]['msg_count'] > 0
