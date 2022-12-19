#SPDX-License-Identifier: MIT
from flask import Response
import sqlalchemy as s
import pandas as pd
import json

AUGUR_API_VERSION = 'api/unstable'

def create_routes(server):
    @server.app.route('/{}/iota/get-net-contributions'.format(AUGUR_API_VERSION), methods=["GET"])
    def get_net_contributions():
        repo_info_sql = s.sql.text("""
            SELECT
                cntrb_email,SUM(cmt_added)-SUM(cmt_removed) AS net_contributions 
            FROM contributors, commits
            WHERE cntrb_email=cmt_committer_email
            GROUP BY cntrb_email;
        """)
        results = pd.read_sql(repo_info_sql,  server.engine)
        data = results.to_json(orient="records", date_format='iso', date_unit='ms')
        parsed_data = json.loads(data)
        return Response(response=data,
                    status=200,
                    mimetype="application/json")
