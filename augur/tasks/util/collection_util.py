from __future__ import annotations
from typing import List
import time
import logging
import json
import os
from enum import Enum
import math
import numpy as np
import datetime
#from celery.result import AsyncResult
from celery import signature
from celery import group, chain, chord, signature
from sqlalchemy import or_, and_
from augur.application.logs import AugurLogger
from augur.tasks.init.celery_app import celery_app as celery
from augur.application.db.models import CollectionStatus, Repo
from augur.application.db.util import execute_session_query
from augur.application.config import AugurConfig
from augur.tasks.github.util.util import get_owner_repo
from augur.tasks.github.util.gh_graphql_entities import GitHubRepo as GitHubRepoGraphql
from augur.tasks.github.util.gh_graphql_entities import GraphQlPageCollection
from augur.tasks.github.util.github_task_session import GithubTaskManifest
from augur.application.db.session import DatabaseSession
from augur.tasks.git.util.facade_worker.facade_worker.facade02utilitymethods import get_repo_weight_by_commit

# class syntax
class CollectionState(Enum):
    SUCCESS = "Success"
    PENDING = "Pending"
    ERROR = "Error"
    COLLECTING = "Collecting"
    INITIALIZING = "Initializing"
    UPDATE = "Update"
    FAILED_CLONE = "Failed Clone"

def get_enabled_phase_names_from_config(logger, session):

    config = AugurConfig(logger, session)
    phase_options = config.get_section("Task_Routine")

    #Get list of enabled phases 
    enabled_phase_names = [name for name, phase in phase_options.items() if phase == 1]

    return enabled_phase_names

#Query db for CollectionStatus records that fit the desired condition.
#Used to get CollectionStatus for differant collection hooks
def get_collection_status_repo_git_from_filter(session,filter_condition,limit):
    repo_status_list = session.query(CollectionStatus).filter(filter_condition).limit(limit).all()

    return [status.repo.repo_git for status in repo_status_list]



@celery.task
def task_failed_util(request,exc,traceback):

    from augur.tasks.init.celery_app import engine

    logger = logging.getLogger(task_failed_util.__name__)

    # log traceback to error file
    logger.error(f"Task {request.id} raised exception: {exc}\n{traceback}")
    
    with DatabaseSession(logger,engine) as session:
        core_id_match = CollectionStatus.core_task_id == request.id
        secondary_id_match = CollectionStatus.secondary_task_id == request.id
        facade_id_match = CollectionStatus.facade_task_id == request.id

        query = session.query(CollectionStatus).filter(or_(core_id_match,secondary_id_match,facade_id_match))

        print(f"chain: {request.chain}")
        #Make sure any further execution of tasks dependent on this one stops.
        try:
            #Replace the tasks queued ahead of this one in a chain with None.
            request.chain = None
        except AttributeError:
            pass #Task is not part of a chain. Normal so don't log.
        except Exception as e:
            logger.error(f"Could not mutate request chain! \n Error: {e}")
        
        try:
            collectionRecord = execute_session_query(query,'one')
        except:
            #Exit if we can't find the record.
            return
        
        if collectionRecord.core_task_id == request.id:
            # set status to Error in db
            collectionRecord.core_status = CollectionStatus.ERROR.value
            collectionRecord.core_task_id = None
        

        if collectionRecord.secondary_task_id == request.id:
            # set status to Error in db
            collectionRecord.secondary_status = CollectionStatus.ERROR.value
            collectionRecord.secondary_task_id = None
            
        
        if collectionRecord.facade_task_id == request.id:
            #Failed clone is differant than an error in collection.
            if collectionRecord.facade_status != CollectionStatus.FAILED_CLONE.value or collectionRecord.facade_status != CollectionStatus.UPDATE.value:
                collectionRecord.facade_status = CollectionStatus.ERROR.value

            collectionRecord.facade_task_id = None
        
        session.commit()
    
    
@celery.task
def core_task_success_util(repo_git):

    from augur.tasks.init.celery_app import engine

    logger = logging.getLogger(core_task_success_util.__name__)

    logger.info(f"Repo '{repo_git}' succeeded through core collection")

    with DatabaseSession(logger, engine) as session:

        repo = Repo.get_by_repo_git(session, repo_git)
        if not repo:
            raise Exception(f"Task with repo_git of {repo_git} but could not be found in Repo table")

        collection_status = repo.collection_status[0]

        collection_status.core_status = CollectionState.SUCCESS.value
        collection_status.core_data_last_collected = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        collection_status.core_task_id = None

        session.commit()

def date_weight_factor(days_since_last_collection):
    return (days_since_last_collection ** 3) / 25


def get_repo_weight_by_issue(logger,repo_git,days_since_last_collection):


    owner,name = get_owner_repo(repo_git)

    with GithubTaskManifest(logger) as manifest:
        repo_graphql = GitHubRepoGraphql(logger, manifest.key_auth, owner, name)
        number_of_issues_and_prs = len(repo_graphql.get_issues_collection()) + len(repo_graphql.get_pull_requests_collection())
    
    return number_of_issues_and_prs - date_weight_factor(days_since_last_collection)


#Get the weight for each repo for the core collection hook
def get_repo_weight_core(logger,repo_git):
    from augur.tasks.init.celery_app import engine

    with DatabaseSession(logger,engine) as session:
        repo = Repo.get_by_repo_git(session, repo_git)
        if not repo:
            raise Exception(f"Task with repo_git of {repo_git} but could not be found in Repo table")

        status = repo.collection_status[0]

        last_collected = status.core_data_last_collected

        if last_collected:
            time_delta = datetime.datetime.now() - last_collected
            days = time_delta.days
        else:
            days = 0

        return get_repo_weight_by_issue(logger, repo_git, days)


@celery.task
def secondary_task_success_util(repo_git):

    from augur.tasks.init.celery_app import engine

    logger = logging.getLogger(secondary_task_success_util.__name__)

    logger.info(f"Repo '{repo_git}' succeeded through secondary collection")

    with DatabaseSession(logger, engine) as session:

        repo = Repo.get_by_repo_git(session, repo_git)
        if not repo:
            raise Exception(f"Task with repo_git of {repo_git} but could not be found in Repo table")

        collection_status = repo.collection_status[0]

        collection_status.secondary_status = CollectionState.SUCCESS.value
        collection_status.secondary_data_last_collected	 = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        collection_status.secondary_task_id = None

        session.commit()

#Get the weight for each repo for the secondary collection hook.
def get_repo_weight_secondary(logger,repo_git):
    from augur.tasks.init.celery_app import engine

    with DatabaseSession(logger,engine) as session:
        repo = Repo.get_by_repo_git(session, repo_git)
        if not repo:
            raise Exception(f"Task with repo_git of {repo_git} but could not be found in Repo table")

        status = repo.collection_status[0]

        last_collected = status.secondary_data_last_collected

        if last_collected:
            time_delta = datetime.datetime.now() - status.secondary_data_last_collected
            days = time_delta
        else:
            days = 0

        return get_repo_weight_by_issue(logger, repo_git, days)


@celery.task
def facade_task_success_util(repo_git):

    from augur.tasks.init.celery_app import engine

    logger = logging.getLogger(facade_task_success_util.__name__)

    logger.info(f"Repo '{repo_git}' succeeded through facade task collection")

    with DatabaseSession(logger, engine) as session:

        repo = Repo.get_by_repo_git(session, repo_git)
        if not repo:
            raise Exception(f"Task with repo_git of {repo_git} but could not be found in Repo table")

        collection_status = repo.collection_status[0]

        collection_status.facade_status = CollectionState.SUCCESS.value
        collection_status.facade_data_last_collected = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        collection_status.facade_task_id = None

        session.commit()

def get_repo_weight_facade(logger,repo_git):
    from augur.tasks.init.celery_app import engine

    with DatabaseSession(logger,engine) as session:
        repo = Repo.get_by_repo_git(session, repo_git)
        if not repo:
            raise Exception(f"Task with repo_git of {repo_git} but could not be found in Repo table")

        status = repo.collection_status[0]
        last_collected = status.facade_data_last_collected

        if last_collected:
            time_delta = datetime.datetime.now() - last_collected
            days = time_delta.days
        else:
            days = 0

        return get_repo_weight_by_commit(logger, repo_git, days)


@celery.task
def facade_clone_update_success_util(repo_git):

    from augur.tasks.init.celery_app import engine

    logger = logging.getLogger(facade_clone_update_success_util.__name__)

    logger.info(f"Repo '{repo_git}' succeeded through facade update/clone")

    with DatabaseSession(logger, engine) as session:

        repo = Repo.get_by_repo_git(session, repo_git)
        if not repo:
            raise Exception(f"Task with repo_git of {repo_git} but could not be found in Repo table")

        collection_status = repo.collection_status[0]

        collection_status.facade_status = CollectionState.UPDATE.value
        collection_status.facade_task_id = None

        session.commit()


class AugurCollectionTotalRepoWeight:
    """
        small class to encapsulate the weight calculation of each repo that is
        being scheduled. Intended to be used as a counter where while it is greater than
        one it is subtracted from until it reaches zero. The weight calculation starts
        from a default method for core repos and can be passed differant calculations accordingly
        as a function that takes a repo_git


    Attributes:
        logger (Logger): Get logger from AugurLogger
        value (int): current value of the collection weight
        value_weight_calculation (function): Function to use on repo to determine weight
    """
    def __init__(self,starting_value: int, weight_calculation=get_repo_weight_core):
        self.logger = AugurLogger("data_collection_jobs").get_logger()
        self.value = starting_value
        self.value_weight_calculation = weight_calculation
    
    #This class can have it's value subtracted using a Repo orm class
    #or a plain integer value.
    def __sub__(self, other):

        if isinstance(other, int):
            self.value -= other
        elif isinstance(other, AugurCollectionTotalRepoWeight):
            self.value -= other.value
        elif isinstance(other, Repo):
            repo_weight = self.value_weight_calculation(self.logger,other.repo_git)
            self.value -= repo_weight
        elif isinstance(other, str):
            repo_weight = self.value_weight_calculation(self.logger,other)
            self.value -= repo_weight
        else:
            raise TypeError(f"Could not subtract object of type {type(other)}")

        if self.value < 0:
            self.value = 0

        return self


class AugurTaskRoutine:
    """
        class to keep track of various groups of collection tasks for a group of repos.
        Simple version to just schedule a number of repos not worrying about repo weight.
        Used when scheduling repo clones/updates.


    Attributes:
        logger (Logger): Get logger from AugurLogger
        repos (List[str]): List of repo_ids to run collection on.
        collection_phases (List[str]): List of phases to run in augur collection.
        collection_hook (str): String determining the attributes to update when collection for a repo starts. e.g. core
        session: Database session to use
    """
    def __init__(self,session,repos: List[str]=[],collection_phases: List=[],collection_hook: str="core"):
        self.logger = AugurLogger("data_collection_jobs").get_logger()
        #self.session = TaskSession(self.logger)
        self.collection_phases = collection_phases
        #self.disabled_collection_tasks = disabled_collection_tasks
        self.repos = repos
        self.session = session
        self.collection_hook = collection_hook

        #Also have attribute to determine what to set repos' status as when they are run
        self.start_state = CollectionState.COLLECTING.value

    def update_status_and_id(self,repo_git, task_id):
        repo = self.session.query(Repo).filter(Repo.repo_git == repo_git).one()

        #Set status in database to collecting
        repoStatus = repo.collection_status[0]
        #
        setattr(repoStatus,f"{self.collection_hook}_task_id",task_id)
        setattr(repoStatus,f"{self.collection_hook}_status",self.start_state)
        self.session.commit()

    def start_data_collection(self):
        """Start all task items and return.

            The purpose is to encapsulate both preparing each message to the broker
            and starting the tasks for each repo in a general sense.
            This way all the specific stuff for each collection hook/ repo
            is generalized.
        """

        #Send messages starts each repo and yields its running info
        #to concurrently update the correct field in the database.
        for repo_git, task_id in self.send_messages():
            self.update_status_and_id(repo_git,task_id)
    
    def send_messages(self):
        augur_collection_list = []
        
        for repo_git in self.repos:

            repo = self.session.query(Repo).filter(Repo.repo_git == repo_git).one()
            repo_id = repo.repo_id

            augur_collection_sequence = []
            for job in self.collection_phases:
                #Add the phase to the sequence in order as a celery task.
                #The preliminary task creates the larger task chain 
                augur_collection_sequence.append(job(repo_git))

            #augur_collection_sequence.append(core_task_success_util.si(repo_git))
            #Link all phases in a chain and send to celery
            augur_collection_chain = chain(*augur_collection_sequence)
            task_id = augur_collection_chain.apply_async(link_error=task_failed_util.s()).task_id

            self.logger.info(f"Setting repo_id {repo_id} to collecting for repo: {repo_git}")

            #yield the value of the task_id to the calling method so that the proper collectionStatus field can be updated
            yield repo_git, task_id


class AugurWeightedTaskRoutine(AugurTaskRoutine):
    """
        class to keep track of various groups of collection tasks for a group of repos.
        Intermediate class that takes into account relative weights of repos and stops after
        a set limit of repos limited by their size.


    Attributes:
        logger (Logger): Get logger from AugurLogger
        repos (List[str]): List of repo_ids to run collection on.
        collection_phases (List[str]): List of phases to run in augur collection.
        collection_hook (str): String determining the attributes to update when collection for a repo starts. e.g. core
        session: Database session to use
        total_repo_weight (AugurCollectionTotalRepoWeight): object that allows repo objects and repo_git strings to be subtracted from it
    """
    def __init__(self,session,repos: List[str]=[],collection_phases: List[str]=[],collection_hook: str="core",total_repo_weight=10000):
        
        #Define superclass vars
        super().__init__(session,repos=repos,collection_phases=collection_phases,collection_hook=collection_hook)

        #Define Total repo weight
        if collection_hook == "core":
            #Core collection hook has a repo weight of 
            self.total_repo_weight = AugurCollectionTotalRepoWeight(total_repo_weight)
        elif collection_hook == "secondary":
            self.total_repo_weight = AugurCollectionTotalRepoWeight(total_repo_weight,weight_calculation=get_repo_weight_secondary)
        elif collection_hook == "facade":
            self.total_repo_weight = AugurCollectionTotalRepoWeight(total_repo_weight,weight_calculation=get_repo_weight_facade)
    

    #Overwrite super method
    #now returns resulting weight after either reaching zero or 
    #scheduling all repos assigned to the object.
    def start_data_collection(self):
        super().start_data_collection()

        return self.total_repo_weight.value

    def send_messages(self):
        augur_collection_list = []
        
        for repo_git in self.repos:
            #Check total repo weight
            if self.total_repo_weight.value == 0:
                break
            
            #Subtract repo's weight
            self.total_repo_weight = self.total_repo_weight - repo_git

            repo = self.session.query(Repo).filter(Repo.repo_git == repo_git).one()
            repo_id = repo.repo_id

            augur_collection_sequence = []
            for job in self.collection_phases:
                #Add the phase to the sequence in order as a celery task.
                #The preliminary task creates the larger task chain 
                augur_collection_sequence.append(job(repo_git))

            #augur_collection_sequence.append(core_task_success_util.si(repo_git))
            #Link all phases in a chain and send to celery
            augur_collection_chain = chain(*augur_collection_sequence)
            task_id = augur_collection_chain.apply_async(link_error=task_failed_util.s()).task_id

            self.logger.info(f"Setting repo_id {repo_id} to collecting for repo: {repo_git}")

            #yield the value of the task_id to the calling method so that the proper collectionStatus field can be updated
            yield repo_git, task_id

if __name__ == "__main__":
    #Examples of using AugurCollectionTotalRepoWeight
    weight = AugurCollectionTotalRepoWeight(10000)
    print(f"Weight value: {weight.value}")

    #Apply subtraction operation with string
    weight = weight - "https://github.com/chaoss/augur"
    print(f"Weight value: {weight.value}")

    #Apply subtraction operation with orm object
    with DatabaseSession(logging.getLogger()) as session:
        repo = Repo.get_by_repo_git(session, 'https://github.com/operate-first/blueprint')
        weight = weight - repo
    
    print(f"Weight value: {weight.value}")

    #Use commit count instead of issues and pr count
    commitWeight = AugurCollectionTotalRepoWeight(100000,weight_calculation=get_repo_weight_facade)
    print(f"commit weight value: {commitWeight.value}")
    #commitWeight = commitWeight - "https://github.com/eclipse/che-theia-activity-tracker"
    #print(f"commit weight value: {commitWeight.value}")