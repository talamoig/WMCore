"""
ReqMgr request handling.

"""

import time
import cherrypy
from datetime import datetime, timedelta

import WMCore.Lexicon
from WMCore.REST.Error import InvalidParameter
from WMCore.Database.CMSCouch import CouchError
from WMCore.WMSpec.WMWorkload import WMWorkloadHelper
from WMCore.WMSpec.StdSpecs.StdBase import WMSpecFactoryException
from WMCore.WMSpec.WMWorkloadTools import loadSpecByType
from WMCore.Wrappers import JsonWrapper

from WMCore.REST.Server import RESTEntity, restcall, rows
from WMCore.REST.Auth import authz_match
from WMCore.REST.Tools import tools
from WMCore.REST.Validation import validate_str, validate_strlist

import WMCore.ReqMgr.Service.RegExp as rx
from WMCore.ReqMgr.Auth import getWritePermission
from WMCore.ReqMgr.DataStructs.Request import initialize_request_args, generateRequestName
from WMCore.ReqMgr.DataStructs.RequestStatus import REQUEST_STATE_LIST, check_allowed_transition
from WMCore.ReqMgr.DataStructs.RequestStatus import REQUEST_STATE_TRANSITION
from WMCore.ReqMgr.DataStructs.RequestType import REQUEST_TYPES
from WMCore.ReqMgr.DataStructs.RequestError import InvalidStateTransition

from WMCore.Services.RequestDB.RequestDBWriter import RequestDBWriter

def validate_request_update_args(request_args, config, reqmgr_db_service, param):
    """
    param and safe structure is RESTArgs structure: named tuple
    RESTArgs(args=[], kwargs={})
    
    validate post request
    1. read data from body
    2. validate the permission (authentication)
    3. validate state transition (against previous state from couchdb)
    2. validate using workload validation
    3. convert data from body to arguments (spec instance, argument with default setting)
    
    TODO: rasie right kind of error with clear message 
    """

    request_name = request_args["RequestName"]
    # this need to be deleted for validation
    del request_args["RequestName"]
    couchurl =  '%s/%s' % (config.couch_host, config.couch_reqmgr_db)
    workload = WMWorkloadHelper()
    # param structure is RESTArgs structure.
    workload.loadSpecFromCouch(couchurl, request_name)
    
    # first validate the permission by status and request type.
    # if the status is not set only ReqMgr Admin can change the the values
    # TODO for each step, assigned, approved, announce find out what other values
    # can be set
    request_args["RequestType"] = workload.requestType()
    permission = getWritePermission(request_args)
    authz_match(permission['role'], permission['group'])
    del request_args["RequestType"]
    
    
    #validate the status
    if request_args.has_key("RequestStatus"):
        validate_state_transition(reqmgr_db_service, request_name, request_args["RequestStatus"])
        # delete request_args since it is not part of spec argument sand validation
        args_without_status = {}
        args_without_status.update(request_args)
        del args_without_status["RequestStatus"]
    else:
        args_without_status = request_args
    # validate the arguments against the spec argumentSpecdefinition
    workload.validateArgument(args_without_status)

    return workload, request_args
        
def validate_request_create_args(request_args, config, *args, **kwargs):
    """
    *arg and **kwargs are only for the interface
    validate post request
    1. read data from body
    2. validate using spec validation
    3. convert data from body to arguments (spec instance, argument with default setting) 
    TODO: rasie right kind of error with clear message 
    """
    print request_args
    initialize_request_args(request_args, config)
    
    #check the permission for creating the request
    permission = getWritePermission(request_args)
    authz_match(permission['role'], permission['group'])
    
    # get the spec type and validate arguments
    spec = loadSpecByType(request_args["RequestType"])
    workload = spec.factoryWorkloadConstruction(request_args["RequestName"], 
                                                request_args)
    return workload, request_args
    
def validate_state_transition(reqmgr_db_service, request_name, new_state) :
    """
    validate state transition by getting the current data from
    couchdb
    """
    requests = reqmgr_db_service.getRequestByNames(request_name)
    # generator object can't be subscribed: need to loop.
    # only one row should be returned
    for request in requests.values():
        current_state = request["RequestStatus"]
    if not check_allowed_transition(current_state, new_state):
        raise InvalidStateTransition(current_state, new_state)
    return