#!/usr/bin/env python
"""
_injectStoreResultsWorkflow_

"""

import os
import sys
import threading
import pdb
import pprint
import inspect

from WMCore.WMInit import connectToDB
from WMCore.Configuration import loadConfigurationFile

from WMCore.WMBS.File import File
from WMCore.WMBS.Fileset import Fileset
from WMCore.WMBS.Subscription import Subscription
from WMCore.WMBS.Workflow import Workflow
from WMCore.WorkQueue.WMBSHelper import WMBSHelper
from WMComponent.DBSBuffer.Database.Interface.DBSBufferFile import DBSBufferFile

from WMCore.DataStructs.Run import Run

from WMCore.WMSpec.StdSpecs.StoreResults import storeResultsWorkload, getTestArguments
from DBSAPI.dbsApi import DbsApi

from WMCore.WMSpec.Makers.TaskMaker import TaskMaker

# The default arguments are set in:
#   WMCORE/src/python/WMCore/WMSpec/StdSpecs/StoreResults.py
arguments = getTestArguments()
arguments["StdJobSplitAlgo"] = "ParentlessMergeBySize"
arguments["StdJobSplitArgs"] = {"files_per_job": 1}
arguments["SkimJobSplitAlgo"] = "TwoFileBased"
arguments["SkimJobSplitArgs"] = {"files_per_job": 1}
arguments["UnmergedLFNBase"] = "/store/temp/WMAgent/unmerged"
arguments["MergedLFNBase"] = "/store/temp/results"
arguments["MinMergeSize"] = 1*1024*1024*1024
arguments["MaxMergeSize"] = 3*1024*1024*1024
arguments["MaxMergeEvents"] = 100000
arguments["DataTier"] = 'USER'
arguments["AcquisitionEra"] = 'Blennies'

if len(sys.argv) != 2:
    print "Usage:"
    print "./injectStoreResults.py PROCESSING_VERSION"
    sys.exit(1)
else:
    arguments["ProcessingVersion"] = sys.argv[1]

connectToDB()

workloadName = "StoreResults-%s" % arguments["ProcessingVersion"]
workloadFile = "storeResults-%s.pkl" % arguments["ProcessingVersion"]
os.mkdir(workloadName)
workload = storeResultsWorkload(workloadName, arguments)

# Build a sandbox using TaskMaker
taskMaker = TaskMaker(workload, os.path.join(os.getcwd(), workloadName))
taskMaker.skipSubscription = True
taskMaker.processWorkload()

workload.save(os.path.join(workloadName, workloadFile))


def injectFilesFromDBS(inputFileset, datasetPath):
    """
    _injectFilesFromDBS_

    """
    print "injecting files from %s into %s, please wait..." % (datasetPath, inputFileset.name)
    args={}
    args["url"] = "http://cmsdbsprod.cern.ch/cms_dbs_prod_global/servlet/DBSServlet"
    args["mode"] = "GET"
    dbsApi = DbsApi(args)
    dbsResults = dbsApi.listFiles(path = datasetPath, retriveList = ["retrive_lumi", "retrive_run"])
    # Limiter on number of files
    dbsResults = dbsResults[0:20]
    print "  found %d files, inserting into wmbs..." % (len(dbsResults))

    for dbsResult in dbsResults:
        myFile = File(lfn = dbsResult["LogicalFileName"], size = dbsResult["FileSize"],
                      events = dbsResult["NumberOfEvents"], checksums = {"cksum": dbsResult["Checksum"]},
                      locations = "cmssrm.fnal.gov", merged = True)
        myRun = Run(runNumber = dbsResult["LumiList"][0]["RunNumber"])
        for lumi in dbsResult["LumiList"]:
            myRun.lumis.append(lumi["LumiSectionNumber"])
        myFile.addRun(myRun)
        myFile.create()
        inputFileset.addFile(myFile)

        dbsFile = DBSBufferFile(lfn = dbsResult["LogicalFileName"], size = dbsResult["FileSize"],
                                events = dbsResult["NumberOfEvents"], checksums = {"cksum": dbsResult["Checksum"]},
                                locations = "cmssrm.fnal.gov", status = "NOTUPLOADED")
        dbsFile.setDatasetPath(datasetPath)
        dbsFile.setAlgorithm(appName = "cmsRun", appVer = "Unknown", appFam = "Unknown",
                             psetHash = "Unknown", configContent = "Unknown")
        dbsFile.create()

    inputFileset.commit()
    inputFileset.markOpen(False)
    return

myThread = threading.currentThread()
myThread.transaction.begin()
for workloadTask in workload.taskIterator():
    inputFileset = Fileset(name = workloadTask.getPathName())
    inputFileset.create()

    inputDataset = workloadTask.inputDataset()
    inputDatasetPath = "/%s/%s/%s" % (inputDataset.primary,
                                      inputDataset.processed,
                                      inputDataset.tier)
    injectFilesFromDBS(inputFileset, inputDatasetPath)

    myWMBSHelper = WMBSHelper(workload)
    myWMBSHelper.createSubscription(workloadTask.getPathName())

myThread.transaction.commit()