from multiprocessing import Process, Queue
from urllib.parse import urlparse
import requests, sys
import pandas as pd
import sqlalchemy as s
from sqlalchemy.ext.automap import automap_base
from sqlalchemy import MetaData, and_
import statistics, logging, os, json, time
import numpy as np
import datetime
from workers.standard_methods import * #init_oauths, get_table_values, get_max_id, register_task_completion, register_task_failure, connect_to_broker, update_gh_rate_limit, record_model_process, paginate
import warnings
warnings.filterwarnings('ignore')

class ContributorWorker:
    """ Worker that detects anomalies on a select few of our metrics
    task: most recent task the broker added to the worker's queue
    child: current process of the queue being ran
    queue: queue of tasks to be fulfilled
    config: holds info like api keys, descriptions, and database connection strings
    """
    def __init__(self, config, task=None):
        self.config = config
        logging.basicConfig(filename='worker_{}.log'.format(self.config['id'].split('.')[len(self.config['id'].split('.')) - 1]), filemode='w', level=logging.INFO)
        logging.info('Worker (PID: {}) initializing...\n'.format(str(os.getpid())))
        self._task = task
        self._child = None
        self._queue = Queue()
        self.db = None
        self.tool_source = 'Contributor Worker'
        self.tool_version = '0.0.1' # See __init__.py
        self.data_source = 'Augur API'
        self.finishing_task = False
        
        self.specs = {
            "id": self.config['id'],
            "location": self.config['location'],
            "qualifications":  [
                {
                    "given": [["git_url"]],
                    "models":["contributors"]
                }
            ],
            "config": [self.config]
        }

        self.results_counter = 0

        self.DB_STR = 'postgresql://{}:{}@{}:{}/{}'.format(
            self.config['user'], self.config['password'], self.config['host'], self.config['port'], self.config['database']
        )

        dbschema = 'augur_data'
        self.db = s.create_engine(self.DB_STR, poolclass=s.pool.NullPool,
            connect_args={'options': '-csearch_path={}'.format(dbschema)})

        helper_schema = 'augur_operations'
        self.helper_db = s.create_engine(self.DB_STR, poolclass = s.pool.NullPool,
            connect_args={'options': '-csearch_path={}'.format(helper_schema)})
        
        # produce our own MetaData object
        metadata = MetaData()
        helper_metadata = MetaData()

        # we can reflect it ourselves from a database, using options
        # such as 'only' to limit what tables we look at...
        metadata.reflect(self.db, only=['contributors', 'contributors_aliases', 'contributor_affiliations',
            'issue_events', 'pull_request_events', 'issues', 'message', 'issue_assignees',
            'pull_request_assignees', 'pull_request_reviewers', 'pull_request_meta', 'pull_request_repo'])
        helper_metadata.reflect(self.helper_db, only=['worker_history', 'worker_job'])

        # we can then produce a set of mappings from this MetaData.
        Base = automap_base(metadata=metadata)
        HelperBase = automap_base(metadata=helper_metadata)

        # calling prepare() just sets up mapped classes and relationships.
        Base.prepare()
        HelperBase.prepare()

        # mapped classes are ready
        self.contributors_table = Base.classes.contributors.__table__
        self.contributors_aliases_table = Base.classes.contributors_aliases.__table__
        self.contributor_affiliations_table = Base.classes.contributor_affiliations.__table__
        self.issue_events_table = Base.classes.issue_events.__table__
        self.pull_request_events_table = Base.classes.pull_request_events.__table__
        self.issues_table = Base.classes.issues.__table__
        self.message_table = Base.classes.message.__table__
        self.issue_assignees_table = Base.classes.issue_assignees.__table__
        self.pull_request_assignees_table = Base.classes.pull_request_assignees.__table__
        self.pull_request_reviewers_table = Base.classes.pull_request_reviewers.__table__
        self.pull_request_meta_table = Base.classes.pull_request_meta.__table__
        self.pull_request_repo_table = Base.classes.pull_request_repo.__table__

        self.history_table = HelperBase.classes.worker_history.__table__
        self.job_table = HelperBase.classes.worker_job.__table__

        self.cntrb_id_inc = get_max_id(self, 'contributors', 'cntrb_id')

        # Increment so we are ready to insert the 'next one' of each of these most recent ids
        self.history_id = get_max_id(self, 'worker_history', 'history_id', operations_table=True) + 1

        # Organize different api keys/oauths available
        init_oauths(self)

        # Send broker hello message
        connect_to_broker(self)

    def update_config(self, config):
        """ Method to update config and set a default
        """
        self.config = {
            'database_connection_string': 'psql://{}:5432/augur'.format(self.config['broker_host']),
            "display_name": "",
            "description": "",
            "required": 1,
            "type": "string"
        }
        self.config.update(config)

    @property
    def task(self):
        """ Property that is returned when the worker's current task is referenced
        """
        return self._task
    
    @task.setter
    def task(self, value):
        """ entry point for the broker to add a task to the queue
        Adds this task to the queue, and calls method to process queue
        """
        if value['job_type'] == "UPDATE" or value['job_type'] == "MAINTAIN":
            self._queue.put(value)
        
        self._task = value
        self.run()

    def cancel(self):
        """ Delete/cancel current task
        """
        self._task = None

    def run(self):
        """ Kicks off the processing of the queue if it is not already being processed
        Gets run whenever a new task is added
        """
        logging.info("Running...\n")
        self._child = Process(target=self.collect, args=())
        self._child.start()

    def collect(self):
        """ Function to process each entry in the worker's task queue
        Determines what action to take based off the message type
        """
        while True:
            if not self._queue.empty():
                message = self._queue.get() # Get the task off our MP queue
            else:
                break
            logging.info("Popped off message: {}\n".format(str(message)))

            if message['job_type'] == 'STOP':
                break

            # If task is not a valid job type
            if message['job_type'] != 'MAINTAIN' and message['job_type'] != 'UPDATE':
                raise ValueError('{} is not a recognized task type'.format(message['job_type']))
                pass

            # Query repo_id corresponding to repo url of given task 
            repoUrlSQL = s.sql.text("""
                SELECT min(repo_id) as repo_id FROM repo WHERE repo_git = '{}'
                """.format(message['given']['git_url']))
            repo_id = int(pd.read_sql(repoUrlSQL, self.db, params={}).iloc[0]['repo_id'])

            # Model method calls wrapped in try/except so that any unexpected error that occurs can be caught
            #   and worker can move onto the next task without stopping
            try:
                # Call method corresponding to model sent in task
                if message['models'][0] == 'contributors':
                    self.contributors_model(message, repo_id)
            except Exception as e:
                register_task_failure(self, message, repo_id, e)
                pass

    def contributors_model(self, entry_info, repo_id):

        # Get and insert all users (emails) found by the facade worker
        self.insert_facade_contributors(entry_info, repo_id)

        # Get and insert all users github considers to be contributors for this repo
        query_github_contributors(self, entry_info, repo_id)

        logging.info("Searching users for commits from the facade worker for repo with entry info: {}\n".format(entry_info))

        # Get all distinct combinations of emails and names by querying the repo's commits
        userSQL = s.sql.text("""
            SELECT cmt_author_name AS commit_name, cntrb_id, cmt_author_raw_email AS commit_email, cntrb_email, 
                cntrb_full_name, cntrb_login, cntrb_canonical, 
                cntrb_company, cntrb_created_at::timestamp, cntrb_type, cntrb_fake, cntrb_deleted, cntrb_long, 
                cntrb_lat, cntrb_country_code, cntrb_state, cntrb_city, cntrb_location, gh_user_id, 
                gh_login, gh_url, gh_html_url, gh_node_id, gh_avatar_url, gh_gravatar_id, gh_followers_url, 
                gh_following_url, gh_gists_url, gh_starred_url, gh_subscriptions_url, gh_organizations_url, 
                gh_repos_url, gh_events_url, gh_received_events_url, gh_type, gh_site_admin, cntrb_last_used
            FROM commits, contributors
            WHERE repo_id = {}
            AND contributors.cntrb_full_name = cmt_author_name
                UNION
            SELECT cmt_author_name AS commit_name, cntrb_id, cmt_author_raw_email AS commit_email, cntrb_email, 
                cntrb_full_name, cntrb_login, cntrb_canonical, 
                cntrb_company, cntrb_created_at::timestamp, cntrb_type, cntrb_fake, cntrb_deleted, cntrb_long, 
                cntrb_lat, cntrb_country_code, cntrb_state, cntrb_city, cntrb_location, gh_user_id, 
                gh_login, gh_url, gh_html_url, gh_node_id, gh_avatar_url, gh_gravatar_id, gh_followers_url, 
                gh_following_url, gh_gists_url, gh_starred_url, gh_subscriptions_url, gh_organizations_url, 
                gh_repos_url, gh_events_url, gh_received_events_url, gh_type, gh_site_admin, cntrb_last_used
            FROM commits, contributors
            WHERE repo_id = {}
            AND contributors.cntrb_email = cmt_author_raw_email
                UNION
            SELECT cmt_committer_name AS commit_name, cntrb_id, cmt_committer_raw_email AS commit_email, 
                cntrb_email, cntrb_full_name, cntrb_login, cntrb_canonical, 
                cntrb_company, cntrb_created_at::timestamp, cntrb_type, cntrb_fake, cntrb_deleted, cntrb_long, 
                cntrb_lat, cntrb_country_code, cntrb_state, cntrb_city, cntrb_location, gh_user_id, 
                gh_login, gh_url, gh_html_url, gh_node_id, gh_avatar_url, gh_gravatar_id, gh_followers_url, 
                gh_following_url, gh_gists_url, gh_starred_url, gh_subscriptions_url, gh_organizations_url, 
                gh_repos_url, gh_events_url, gh_received_events_url, gh_type, gh_site_admin, cntrb_last_used
            FROM commits, contributors
            WHERE repo_id = {}
            AND contributors.cntrb_full_name = cmt_committer_name
                UNION
            SELECT cmt_committer_name AS commit_name, cntrb_id, cmt_committer_raw_email AS commit_email, 
                cntrb_email, cntrb_full_name, cntrb_login, cntrb_canonical, 
                cntrb_company, cntrb_created_at::timestamp, cntrb_type, cntrb_fake, cntrb_deleted, cntrb_long, 
                cntrb_lat, cntrb_country_code, cntrb_state, cntrb_city, cntrb_location, gh_user_id, 
                gh_login, gh_url, gh_html_url, gh_node_id, gh_avatar_url, gh_gravatar_id, gh_followers_url, 
                gh_following_url, gh_gists_url, gh_starred_url, gh_subscriptions_url, gh_organizations_url, 
                gh_repos_url, gh_events_url, gh_received_events_url, gh_type, gh_site_admin, cntrb_last_used
            FROM commits, contributors
            WHERE repo_id = {}
            AND contributors.cntrb_email = cmt_committer_raw_email
                ORDER BY cntrb_id
        """.format(repo_id,repo_id,repo_id,repo_id))

        commit_cntrbs = json.loads(pd.read_sql(userSQL, self.db, params={}).to_json(orient="records"))
        logging.info("We found {} distinct emails to search for in this repo (repo_id = {})".format(
            len(commit_cntrbs), repo_id))

        # For every unique commit contributor info combination...
        for tuple in commit_cntrbs:

            # Set default starting cntrb_id for fk's (may change in alias method)
            self.cntrb_id_inc = tuple['cntrb_id']
            contributor = tuple.copy()

            """ Determine if this contributor/email is an alias for a different canonical, if it is
                then we will insert the appropriate info in the database """

            # If these are not equal, then it is an alias bc the commit has diff email than canonical
            if contributor['commit_email'] != contributor['cntrb_email']:
                self.handle_alias(tuple)


            """ Fill incomplete columns (including cntrb_full_name and cntrb_canonical) """

            if not contributor['cntrb_created_at'] or not contributor['cntrb_last_used']:

                commit_times_sql = s.sql.text("""
                        SELECT min(cmt_author_date) as min_author, max(cmt_author_date) as max_author,
                            min(cmt_committer_date) as min_committer, max(cmt_committer_date) as max_committer
                        FROM commits 
                        WHERE cmt_author_raw_email = :commit_email
                        OR cmt_committer_raw_email = :commit_email
                    """)
                date_info = pd.read_sql(commit_times_sql, self.db, params={'commit_email': contributor['commit_email']}).iloc[0]

                times_used_tuple = {
                    'cntrb_created_at': min(date_info['min_author'], date_info['min_committer']),
                    'cntrb_last_used': max(date_info['max_author'], date_info['max_committer']),
                    'tool_source': self.tool_source,
                    'tool_version': self.tool_version,
                    'data_source': self.data_source
                }
                result = self.db.execute(self.contributors_table.update().where(
                    self.contributors_table.c.cntrb_id==contributor['cntrb_id']).values(times_used_tuple))
                self.results_counter += 1
                logging.info("Updated cntrb_created_at and cntrb_last_used columns for existing "
                    "tuple in the contributors table with email: {}\n".format(contributor['commit_email']))

            # If cntrb_full_name column is not filled, go ahead and fill it bc we have that info
            if not contributor['cntrb_full_name'] and contributor['commit_name']: #and tuple['cntrb_id']:
                name_col = {
                    'cntrb_full_name': contributor['commit_name'],
                    'tool_source': self.tool_source,
                    'tool_version': self.tool_version,
                    'data_source': self.data_source
                }

                result = self.db.execute(self.contributors_table.update().where(
                    self.contributors_table.c.cntrb_id==contributor['cntrb_id']).values(name_col))
                logging.info("Inserted cntrb_full_name column for existing tuple in the contributors "
                    "table with email: {}\n".format(contributor['cntrb_email']))

            # If cntrb_canonical column is not filled, go ahead and fill it w main email bc 
            #   an old version of the worker did not
            if not contributor['cntrb_canonical'] and contributor['cntrb_email']:
                canonical_col = {
                    'cntrb_canonical': contributor['cntrb_email'],
                    'tool_source': self.tool_source,
                    'tool_version': self.tool_version,
                    'data_source': self.data_source
                }

                result = self.db.execute(self.contributors_table.update().where(
                    self.contributors_table.c.cntrb_id==contributor['cntrb_id']).values(canonical_col))
                logging.info("Inserted cntrb_canonical column for existing tuple in the contributors "
                    "table with email: {}\n".format(contributor['cntrb_email']))


            """ Attempt to fill cntrb_login (github login) column by performing a github api search of the 
                user's name and email """

            # If the contributor already has a login, there is no use in performing the github search
            if not contributor['cntrb_login']:
                """ Handles search of user through github api """

                # try/except to handle case of a first/last split or just first name
                try:
                    cmt_cntrb = {
                        'fname': contributor['commit_name'].split()[0], 
                        'lname': contributor['commit_name'].split()[1], 
                        'email': contributor['commit_email']
                    }
                    url = 'https://api.github.com/search/users?q={}+in:email+fullname:{}+{}'.format(
                        cmt_cntrb['email'],cmt_cntrb['fname'],cmt_cntrb['lname'])
                except:
                    cmt_cntrb = {'fname': contributor['commit_name'].split()[0], 'lname': '',
                        'email': contributor['commit_email']}
                    url = 'https://api.github.com/search/users?q={}+in:email+fullname:{}'.format(
                        cmt_cntrb['email'],cmt_cntrb['fname'])

                logging.info("Hitting endpoint: " + url + " ...\n")
                r = requests.get(url=url, headers=self.headers)
                update_gh_rate_limit(self, r)
                results = r.json()

                # If no matches or bad response, continue with other contributors
                if 'total_count' not in results:
                    logging.info("Search query returned an empty response, moving on...\n")
                    continue
                if results['total_count'] == 0:
                    logging.info("Search query did not return any results, moving on...\n")
                    continue

                logging.info("When searching for a contributor with info {}, we found the following users: {}\n".format(
                    cmt_cntrb, results))

                # Grab first result and make sure it has the highest match score
                match = results['items'][0]
                for item in results['items']:
                    if item['score'] > match['score']:
                        match = item

                cntrb_url = ("https://api.github.com/users/" + match['login'])
                logging.info("Hitting endpoint: " + cntrb_url + " ...\n")
                r = requests.get(url=cntrb_url, headers=self.headers)
                update_gh_rate_limit(self, r)
                contributor = r.json()

                # Fill in all github information
                cntrb_gh_info = {
                    "cntrb_login": contributor['login'],
                    "cntrb_created_at": contributor['created_at'],
                    "cntrb_email": cmt_cntrb['email'],
                    "cntrb_company": contributor['company'] if 'company' in contributor else None,
                    "cntrb_location": contributor['location'] if 'location' in contributor else None,
                    # "cntrb_type": , dont have a use for this as of now ... let it default to null
                    "gh_user_id": contributor['id'],
                    "gh_login": contributor['login'],
                    "gh_url": contributor['url'],
                    "gh_html_url": contributor['html_url'],
                    "gh_node_id": contributor['node_id'],
                    "gh_avatar_url": contributor['avatar_url'],
                    "gh_gravatar_id": contributor['gravatar_id'],
                    "gh_followers_url": contributor['followers_url'],
                    "gh_following_url": contributor['following_url'],
                    "gh_gists_url": contributor['gists_url'],
                    "gh_starred_url": contributor['starred_url'],
                    "gh_subscriptions_url": contributor['subscriptions_url'],
                    "gh_organizations_url": contributor['organizations_url'],
                    "gh_repos_url": contributor['repos_url'],
                    "gh_events_url": contributor['events_url'],
                    "gh_received_events_url": contributor['received_events_url'],
                    "gh_type": contributor['type'],
                    "gh_site_admin": contributor['site_admin'],
                    "tool_source": self.tool_source,
                    "tool_version": self.tool_version,
                    "data_source": self.data_source
                }
                result = self.db.execute(self.contributors_table.update().where(
                    self.contributors_table.c.cntrb_id==contributor['cntrb_id']).values(cntrb_gh_info))
                logging.info("Updated existing tuple in the contributors table with github info after "
                    "a successful search query on a facade commit's author : {} {}\n".format(contributor, cntrb_gh_info))

        # Register this task as completed
        register_task_completion(self, entry_info, repo_id, "contributors")

    def insert_facade_contributors(self, entry_info, repo_id):
        logging.info("Beginning process to insert contributors from facade commits for repo w entry info: {}\n".format(entry_info))

        # Get all distinct combinations of emails and names by querying the repo's commits
        userSQL = s.sql.text("""
            SELECT cmt_author_email as email, cmt_author_date as date, cmt_author_name as name
            FROM commits 
            WHERE repo_id = :repo_id
            AND not exists (SELECT cntrb_email FROM contributors where cntrb_email = cmt_author_email)
            and (cmt_author_date, cmt_author_name) in (
                select Max(cmt_author_date) as date, cmt_author_name
                from commits as c 
                where c.cmt_author_email = commits.cmt_author_email
                and repo_id = :repo_id
                group by cmt_author_name
                order by date desc
                limit 1
            )
            group by cmt_author_email, cmt_author_date, commits.cmt_author_name
                UNION
            SELECT cmt_committer_email as email, cmt_committer_date as date, cmt_committer_name as name
            FROM augur_data.commits
            WHERE repo_id = :repo_id
            AND not exists (SELECT cntrb_email FROM augur_data.contributors where cntrb_email = cmt_committer_email)
            and (cmt_committer_date, cmt_committer_name) in (
                select Max(cmt_committer_date) as date, cmt_committer_name
                from augur_data.commits as c 
                where c.cmt_committer_email = commits.cmt_committer_email
                and repo_id = :repo_id
                group by cmt_committer_name
                order by date desc
                limit 1
            )
            group by cmt_committer_email, cmt_committer_date, cmt_committer_name
        """)

        commit_cntrbs = json.loads(pd.read_sql(userSQL, self.db, params={'repo_id': repo_id}).to_json(orient="records"))
        logging.info("We found {} distinct contributors needing insertion (repo_id = {})".format(
            len(commit_cntrbs), repo_id))

        for cntrb in commit_cntrbs:
            cntrb_tuple = {
                    "cntrb_email": cntrb['email'],
                    "cntrb_canonical": cntrb['email'],
                    "tool_source": self.tool_source,
                    "tool_version": self.tool_version,
                    "data_source": self.data_source,
                    'cntrb_full_name': cntrb['name']
                }
            result = self.db.execute(self.contributors_table.insert().values(cntrb_tuple))
            logging.info("Primary key inserted into the contributors table: {}".format(result.inserted_primary_key))
            self.results_counter += 1

            logging.info("Inserted contributor: {}\n".format(cntrb['email']))

    def handle_alias(self, tuple):
        cntrb_email = tuple['cntrb_email'] # canonical
        commit_email = tuple['commit_email'] # alias email
        cntrb_id = tuple['cntrb_id']

        # Check existing contributors table tuple
        existing_tuples = retrieve_tuple(self, {'cntrb_email': tuple['commit_email']}, ['contributors'])

        if len(existing_tuples) == 0:
            """ Insert alias tuple into the contributor table """

            # Prepare tuple for insertion to contributor table (build it off of the tuple queried)
            cntrb = tuple
            try:
                created_at = datetime.fromtimestamp(cntrb['cntrb_created_at']/1000)
            except:
                created_at = None
            cntrb['cntrb_created_at'] = created_at
            cntrb['cntrb_email'] = tuple['commit_email']
            cntrb["tool_source"] = self.tool_source
            cntrb["tool_version"] = self.tool_version
            cntrb["data_source"] = self.data_source
            del cntrb['commit_name']
            del cntrb['commit_email']
            del cntrb['cntrb_id']
            
            result = self.db.execute(self.contributors_table.insert().values(cntrb))
            logging.info("Inserted alias into the contributors table with email: {}\n".format(cntrb['cntrb_email']))
            self.results_counter += 1
            self.cntrb_id_inc = int(result.inserted_primary_key[0])
            alias_id = self.cntrb_id_inc

        elif len(existing_tuples) > 1:
            # fix all dupe references to dupe cntrb ids before we delete them 
            logging.info("THERE IS A CASE FOR A DUPLICATE CONTRIBUTOR in the contributors table, we will delete all tuples with this cntrb_email and re-insert only 1\n")
            logging.info("For cntrb_email: {}".format(tuple['commit_email']))
            
            """ Insert alias tuple into the contributor table """

            # Prepare tuple for insertion to contributor table (build it off of the tuple queried)
            cntrb = tuple.copy()
            try:
                cntrb['cntrb_created_at'] = datetime.fromtimestamp(cntrb['cntrb_created_at']/1000)
            except:
                cntrb['cntrb_created_at'] = None

            try:
                cntrb['cntrb_last_used'] = datetime.fromtimestamp(cntrb['cntrb_last_used']/1000)
            except:
                cntrb['cntrb_last_used'] = None

            cntrb['cntrb_email'] = cntrb['commit_email']
            cntrb["tool_source"] = self.tool_source
            cntrb["tool_version"] = self.tool_version
            cntrb["data_source"] = self.data_source
            del cntrb['commit_name']
            del cntrb['commit_email']
            del cntrb['cntrb_id']
            
            result = self.db.execute(self.contributors_table.insert().values(cntrb))
            logging.info("Inserted alias into the contributors table with email: {}\n".format(cntrb['cntrb_email']))
            self.results_counter += 1
            self.cntrb_id_inc = int(result.inserted_primary_key[0])
            alias_id = self.cntrb_id_inc

            dupeIdsSQL = s.sql.text("""
                SELECT cntrb_id from contributors
                WHERE
                    cntrb_email = '{0}'
                AND
                    cntrb_id NOT IN (SELECT cntrb_id FROM contributors_aliases);
            """.format(commit_email))

            dupe_ids = json.loads(pd.read_sql(dupeIdsSQL, self.db, params={}).to_json(orient="records"))

            alias_update_col = {'cntrb_a_id': self.cntrb_id_inc}
            update_col = {'cntrb_id': self.cntrb_id_inc}
            reporter_col = {'reporter_id': self.cntrb_id_inc}
            pr_assignee_col = {'contrib_id': self.cntrb_id_inc}
            pr_repo_col = {'pr_cntrb_id': self.cntrb_id_inc}

            # def delete_fk(table, column):

            # tables_with_fk = {
            #         'contributors_aliases_table': ['cntrb_a_id', alias_update_col], 
            #         'issue_events_table':, 
            #         'pull_request_events_table',
            #         'issues_table',
            #         'issues_table'
            #     }
            for id in dupe_ids:

                alias_result = self.db.execute(self.contributors_aliases_table.update().where(
                    self.contributors_aliases_table.c.cntrb_a_id==id['cntrb_id']).values(alias_update_col))
                logging.info("Updated cntrb_a_id column for tuples in the contributors_aliases table with value: {} replaced with new cntrb id: {}".format(id['cntrb_id'], self.cntrb_id_inc))

                #temp
                alias_email_result = self.db.execute(self.contributors_aliases_table.update().where(
                    self.contributors_aliases_table.c.alias_email==commit_email).values(alias_update_col))
                logging.info("Updated cntrb_a_id column for tuples in the contributors_aliases table with value: {} replaced with new cntrb id: {}".format(commit_email, self.cntrb_id_inc))
                #tempend

                issue_events_result = self.db.execute(self.issue_events_table.update().where(
                    self.issue_events_table.c.cntrb_id==id['cntrb_id']).values(update_col))
                logging.info("Updated cntrb_id column for tuples in the issue_events table with value: {} replaced with new cntrb id: {}".format(id['cntrb_id'], self.cntrb_id_inc))

                pr_events_result = self.db.execute(self.pull_request_events_table.update().where(
                    self.pull_request_events_table.c.cntrb_id==id['cntrb_id']).values(update_col))
                logging.info("Updated cntrb_id column for tuples in the pull_request_events table with value: {} replaced with new cntrb id: {}".format(id['cntrb_id'], self.cntrb_id_inc))

                issues_cntrb_result = self.db.execute(self.issues_table.update().where(
                    self.issues_table.c.cntrb_id==id['cntrb_id']).values(update_col))
                logging.info("Updated cntrb_id column for tuples in the issues table with value: {} replaced with new cntrb id: {}".format(id['cntrb_id'], self.cntrb_id_inc))

                issues_reporter_result = self.db.execute(self.issues_table.update().where(
                    self.issues_table.c.reporter_id==id['cntrb_id']).values(reporter_col))
                logging.info("Updated reporter_id column in the issues table with value: {} replaced with new cntrb id: {}".format(id['cntrb_id'], self.cntrb_id_inc))

                issue_assignee_result = self.db.execute(self.issue_assignees_table.update().where(
                    self.issue_assignees_table.c.cntrb_id==id['cntrb_id']).values(update_col))
                logging.info("Updated cntrb_id column for tuple in the issue_assignees table with value: {} replaced with new cntrb id: {}".format(id['cntrb_id'], self.cntrb_id_inc))

                pr_assignee_result = self.db.execute(self.pull_request_assignees_table.update().where(
                    self.pull_request_assignees_table.c.contrib_id==id['cntrb_id']).values(pr_assignee_col))
                logging.info("Updated contrib_id column for tuple in the pull_request_assignees table with value: {} replaced with new cntrb id: {}".format(id['cntrb_id'], self.cntrb_id_inc))

                message_result = self.db.execute(self.message_table.update().where(
                    self.message_table.c.cntrb_id==id['cntrb_id']).values(update_col))
                logging.info("Updated cntrb_id column for tuple in the message table with value: {} replaced with new cntrb id: {}".format(id['cntrb_id'], self.cntrb_id_inc))

                pr_reviewers_result = self.db.execute(self.pull_request_reviewers_table.update().where(
                    self.pull_request_reviewers_table.c.cntrb_id==id['cntrb_id']).values(update_col))
                logging.info("Updated cntrb_id column for tuple in the pull_request_reviewers table with value: {} replaced with new cntrb id: {}".format(id['cntrb_id'], self.cntrb_id_inc))

                pr_meta_result = self.db.execute(self.pull_request_meta_table.update().where(
                    self.pull_request_meta_table.c.cntrb_id==id['cntrb_id']).values(update_col))
                logging.info("Updated cntrb_id column for tuple in the pull_request_meta table with value: {} replaced with new cntrb id: {}".format(id['cntrb_id'], self.cntrb_id_inc))

                pr_repo_result = self.db.execute(self.pull_request_repo_table.update().where(
                    self.pull_request_repo_table.c.pr_cntrb_id==id['cntrb_id']).values(pr_repo_col))
                logging.info("Updated cntrb_id column for tuple in the pull_request_repo table with value: {} replaced with new cntrb id: {}".format(id['cntrb_id'], self.cntrb_id_inc))

            deleteSQL = """
                DELETE 
                    FROM
                        contributors c 
                    USING 
                        contributors_aliases
                    WHERE
                        c.cntrb_email = '{0}'
                    AND
                        c.cntrb_id NOT IN (SELECT cntrb_id FROM contributors_aliases)
                    AND
                        c.cntrb_id <> {1};
            """.format(commit_email, self.cntrb_id_inc)
            
            try:
                # Delete all dupes 
                result = self.db.execute(deleteSQL)
                logging.info("Deleted all non-canonical contributors with the email: {}\n".format(commit_email))
            except Exception as e:
                logging.info("When trying to delete a duplicate contributor, worker ran into error: {}".format(e))
        
        else: #then there would be exactly 1 existing tuple, so that id is the one we want
            alias_id = existing_tuples[0]['cntrb_id']

        # Now check existing alias table tuple
        existing_tuples = retrieve_tuple(self, {'alias_email': commit_email}, ['contributors_aliases'])
        if len(existing_tuples) == 0:
            logging.info("Finding cntrb_id for canonical email: {}".format(cntrb_email))
            canonical_id_sql = s.sql.text("""
                SELECT cntrb_id as canonical_id 
                from contributors
                where cntrb_email = :email
            """)
            canonical_id_result = json.loads(pd.read_sql(canonical_id_sql, self.db, params={'email': cntrb_email}).to_json(
                orient="records"))
            if len(canonical_id_result) > 1:
                logging.info("MORE THAN ONE CANONICAL CONTRIBUTOR found for email: {}".format(cntrb_email))
            alias_tuple = {
                'cntrb_id': canonical_id_result[0]['canonical_id'],
                'cntrb_a_id': alias_id,
                'canonical_email': tuple['cntrb_canonical'],
                'alias_email': commit_email,
                'cntrb_active': 1,
                "tool_source": self.tool_source,
                "tool_version": self.tool_version,
                "data_source": self.data_source
            }
            result = self.db.execute(self.contributors_aliases_table.insert().values(alias_tuple))
            self.results_counter += 1
            logging.info("Inserted alias with email: {}\n".format(commit_email))
        if len(existing_tuples) > 1:
            logging.info("THERE IS A CASE FOR A DUPLICATE CONTRIBUTOR in the alias "
                "table AND NEED TO ADD DELETION LOGIC: {}\n".format(existing_tuples))

