#SPDX-License-Identifier: MIT
import requests
import pytest
from tests import server_port


def test_get_net_contributions_api_data():
    response = requests.get(f'http://localhost:{server_port}/api/unstable/iota/get_net_contributions')
    data = response.json()
    assert response.status_code == 200
    assert len(data) >= 1

