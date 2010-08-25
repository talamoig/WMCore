#!/usr/bin/env python
#pylint: disable-msg=W0102, W6501, C0103, W0621, W0105, W0703
"""
_ProcessPool_

"""

__revision__ = "$Id: ProcessPool.py,v 1.12 2010/04/28 16:22:26 mnorman Exp $"
__version__ = "$Revision: 1.12 $"

import subprocess
import sys
import logging
import os
import threading
import traceback

from logging.handlers import RotatingFileHandler

from WMCore.WMFactory import WMFactory
from WMCore.WMInit    import WMInit
from WMCore           import WMLogging

from WMCore.Services.Requests import JSONRequests

class ProcessPool:
    def __init__(self, slaveClassName, totalSlaves, componentDir,
                 config, slaveInit = None):
        """
        __init__

        Constructor for the process pool.  The slave class name must be based
        inside the WMComponent namespace.  For examples, the JobAccountant would
        pass in 'JobAccountant.AccountantWorker' to run the AccountantWorker
        class.  All log files will be stored in the component directory that is
        passed in.  Each slave will have its own log file.

        Note that the config is only used to determine database connection
        parameters.  It is not passed to the slave class.  The slaveInit
        parameter will be serialized and passed to the slave class's
        constructor.
        """
        self.enqueueIndex = 0
        self.dequeueIndex = 0

        #Use the Services.Requests JSONizer, which handles __to_json__ calls
        self.jsonHandler = JSONRequests()


        #Grab the python version from the current version
        #Assume naming convention pythonA.B, i.e., python2.4 for v2.4.X
        majorVersion = sys.version_info[0]
        minorVersion = sys.version_info[1]

        if majorVersion and minorVersion:
            versionString = "python%i.%i" % (majorVersion, minorVersion)
        else:
            versionString = "python2.4"

        self.workers = []
        slaveArgs = [versionString, __file__, slaveClassName]
        if hasattr(config.CoreDatabase, "socket"):
            socket = config.CoreDatabase.socket
        else:
            socket = None

        (connectDialect, junk) = config.CoreDatabase.connectUrl.split(":", 1)
        if connectDialect.lower() == "mysql":
            dialect = "MySQL"
        elif connectDialect.lower() == "oracle":
            dialect = "Oracle"
        elif connectDialect.lower() == "sqlite":
            dialect = "SQLite"

        dbConfig = {"dialect": dialect,
                    "connectUrl": config.CoreDatabase.connectUrl,
                    "socket": socket,
                    "componentDir": componentDir}
        encodedDBConfig = self.jsonHandler.encode(dbConfig)

        if slaveInit == None:
            encodedSlaveInit = None
        else:
            encodedSlaveInit = self.jsonHandler.encode(slaveInit)
            
        while totalSlaves > 0:
            #For each worker you want create a slave process
            #That process calls this code (WMCore.ProcessPool) and opens
            #A process pool that loads the designated class
            slaveProcess = subprocess.Popen(slaveArgs, stdin = subprocess.PIPE,
                                            stdout = subprocess.PIPE)
            slaveProcess.stdin.write("%s\n" % encodedDBConfig)

            if encodedSlaveInit == None:
                slaveProcess.stdin.write("\n")
            else:
                slaveProcess.stdin.write("%s\n" % encodedSlaveInit)
                
            slaveProcess.stdin.flush()
            self.workers.append(slaveProcess)
            totalSlaves -= 1
            
        return

    def __del__(self):
        """
        __del__

        Kill all the workers processes by sending them an invalid JSON object.
        This will cause them to shut down.
        """
        for worker in self.workers:
            try:
                worker.stdin.write("\n")
                worker.stdin.flush()
            except Exception, ex:
                pass

        return

    def enqueue(self, work):
        """
        __enqeue__

        Assign work to the workers processes.  The work parameters must be a
        list where each item in the list can be serialized into JSON.
        """

        workPerWorker = len(work) / len(self.workers)

        if workPerWorker == 0:
            workPerWorker = 1

        workIndex = 0
        while(len(work) > workIndex):
            workForWorker = work[workIndex : workIndex + workPerWorker]
            workIndex += workPerWorker

            encodedWork = self.jsonHandler.encode(workForWorker)

            worker = self.workers[self.enqueueIndex]
            self.enqueueIndex = (self.enqueueIndex + 1) % len(self.workers)
            worker.stdin.write("%s\n" % encodedWork)
            worker.stdin.flush()

        if len(work) > workIndex:
            encodedWork = self.jsonHandler.encode(work[workIndex:])

            worker = self.workers[self.enqueueIndex]
            self.enqueueIndex = (self.enqueueIndex + 1) % len(self.workers)
            worker.stdin.write("%s\n" % encodedWork)
            worker.stdin.flush()
            
        return

    def dequeue(self, totalItems = 1):
        """
        __dequeue__

        Retrieve completed work from the slave workers.  This method will block
        until enough work has been completed.
        """
        completedWork = []

        while totalItems > 0:
            worker = self.workers[self.dequeueIndex]
            self.dequeueIndex = (self.dequeueIndex + 1) % len(self.workers)

            try:
                output = worker.stdout.readline()
                
                if output == None:
                    logging.info("No output from worker node line in ProcessPool")
                    continue


                completedWork.append(self.jsonHandler.decode(output))
                totalItems -= 1
            except Exception, e:
                logging.error("Exception while getting slave output: %s" % e)
                break

        return completedWork

def setupLogging(componentDir):
    """
    _setupLogging_

    Setup logging for the slave process.  Each slave process will have its own
    log file.
    """
    logFile = "%s/ComponentLog.%s" % (componentDir, os.getpid())

    logHandler = RotatingFileHandler(logFile, "a", 1000000000, 3)
    logFormatter = logging.Formatter("%(asctime)s:%(levelname)s:%(module)s:%(message)s")
    logHandler.setFormatter(logFormatter)
    logging.getLogger().addHandler(logHandler)
    logging.getLogger().setLevel(logging.INFO)
    #This is left in as a reminder for debugging purposes
    #SQLDEBUG turns your log files into horrible messes
    #logging.getLogger().setLevel(logging.SQLDEBUG)

    myThread = threading.currentThread()
    myThread.logger = logging.getLogger()
    return

def setupDB(config, wmInit):
    """
    _setupDB_

    Create the database connections.
    """
    if config.has_key("socket"):
        socket = config["socket"]
    else:
        socket = None
        
    connectUrl = config["connectUrl"]
    dialect = config["dialect"]
        
    wmInit.setDatabaseConnection(dbConfig = connectUrl,
                                 dialect = dialect,
                                 socketLoc = socket)
    return
    
if __name__ == "__main__":
    """
    __main__

    Entry point for the slave process.  The slave's classname will be passed in
    on the command line.  The database connection parameters as well as the
    name of the directory that the log files will be stored in will be passed
    in through stdin as a JSON object.
    """
    
    slaveClassName = sys.argv[1]

    jsonHandler = JSONRequests()

    encodedConfig = sys.stdin.readline()
    config = jsonHandler.decode(encodedConfig)

    encodedSlaveInit = sys.stdin.readline()
    if encodedSlaveInit != "\n":
        unicodeSlaveInit = jsonHandler.decode(encodedSlaveInit)
        slaveInit = {}
        for key in unicodeSlaveInit.keys():
            slaveInit[str(key)] = unicodeSlaveInit[key]
    else:
        slaveInit = None

    wmInit = WMInit()
    setupLogging(config["componentDir"])
    setupDB(config, wmInit)

    wmFactory = WMFactory(name = "slaveFactory", namespace = "WMComponent")
    slaveClass = wmFactory.loadObject(classname = slaveClassName, args = slaveInit)

    logging.error("Have slave class")

    while(True):
        #Parameters for each job passed in from ProcessPool.enqueue()
        #Decoded by WMCore.Services.Requests class JSONRequests
        encodedInput = sys.stdin.readline()

        try:
            input = jsonHandler.decode(encodedInput)
        except Exception, ex:
            logging.error("Error decoding: %s" % str(ex))
            break

        try:
            output = slaveClass(parameters = input)
        except Exception, ex:
            crashMessage = "Slave process crashed with exception: " + str(ex)
            crashMessage += "\nStacktrace:\n"

            stackTrace = traceback.format_tb(sys.exc_info()[2], None)
            for stackFrame in stackTrace:
                crashMessage += stackFrame
                
            logging.error(crashMessage)
            sys.exit(1)
            
        if output != None:
            if type(output) == list:
                for item in output:
                    encodedOutput = jsonHandler.encode(item)
                    sys.stdout.write("%s\n" % encodedOutput)
                    sys.stdout.flush()
            else:
                encodedOutput = jsonHandler.encode(output)
                sys.stdout.write("%s\n" % encodedOutput)
                sys.stdout.flush()                

    logging.info("Process with PID %s finished" %(os.getpid()))
    sys.exit(0)
