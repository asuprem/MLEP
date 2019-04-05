
import os, shutil
import json, codecs
import time

import pdb

from utils import std_flush, ms_to_readable, time_to_id, adapt_array, convert_array, readable_time

import numpy as np

import sqlite3
from sqlite3 import Error


class MLEPLearningServer():
    def __init__(self,):
        # Converts np.array to TEXT when inserting
        sqlite3.register_adapter(np.ndarray, adapt_array)
        # Converts TEXT to np.array when selecting
        sqlite3.register_converter("array", convert_array)

        std_flush("Initializing")

        self.config = self.load_json('config/MLEPServer.json')
        self.MLEPConfig = self.config["config"]
        self.MLEPEncoders = self.config["encoders"]
        self.MLEPModels = self.config["models"]
        self.MLEPPipelines = self.config["pipelines"]

        """ This is the internal clock of the Server. Normally, this is time.time(). For this implementation, this is updated manually """
        self.overallTimer = None
        # Since model's are pushed by live time (instead of data time), we have a model timer as well
        self.MLEPModelTimer = time.time()
        """ This is the clock for scheduled Filter Generation. During this scheduled generation, existing filters are also updated. Not yet sure how, but this is in progress """
        self.scheduledFilterGenerateUpdateTimer = 0
        self.scheduledSchedule = 0
        if "update_schedule" in self.MLEPConfig:
            self.scheduledSchedule = self.MLEPConfig["update_schedule"]
        else:
            DAY_IN_MS = 86400000
            self.scheduledSchedule =  86400000 * 30
            del DAY_IN_MS

        std_flush("Finished up timers at ", readable_time())
        # For Drift based, models track their own 'drift'
        # ------------------------------------------------------------------------------------


        """ Set up storage directories """
        self.SOURCE_DIR = './.MLEPServer'
        self.setups = ['models', 'data', 'modelSerials', 'db']
        self.DB_FILE = './.MLEPServer/db/MLEP.db'
        self.SCHEDULED_DATA_FILE = './.MLEPServer/data/scheduledFile.json'
        std_flush("Finished up path variables at ", readable_time())
        
        """ create scheduled file """
        open(self.SCHEDULED_DATA_FILE, 'w').close()
        std_flush("Created data file at", readable_time())

        try:
            shutil.rmtree(self.SOURCE_DIR)
        except:
            pass
        os.makedirs(self.SOURCE_DIR)
        for directory in self.setups:
            os.makedirs(os.path.join(self.SOURCE_DIR, directory))

        std_flush("Finished setting up directory structure at", readable_time())

        """ create Database Connections and perform initial setup """
        self.DB_CONN = None
        self.openDBConnection()
        self.initializeDB()
        std_flush("Initialized DB at ", readable_time())

        # This would normally be a set of hosted encoders. For local implementation, we have the encoders as a dict of encoder objects (TODO)
        std_flush("Setting up built-in encoders", readable_time())
        self.ENCODERS = {}
        self.setUpEncoders()

        # Setting of 'hosted' models + data cetroids
        self.MODELS = {}
        self.CENTROIDS={}

        # These are models generated and updated in the prior update
        self.RECENT_MODELS=[]
        # Only generated models in prior update
        self.RECENT_NEW = []
        # Only update models in prior update
        self.RECENT_UPDATES = []
        # All models
        self.HISTORICAL = []
        # All generated models
        self.HISTORICAL_NEW = []
        # All update models
        self.HISTORICAL_UPDATES = []
        # Just the initial models
        self.TRAIN_MODELS = []



    def setUpEncoders(self):
        """ This sets up built-in encoders. For now, this is all there is. Specifically, we only have pretrained Google News w2v """

        # Load Encoder configurations
        for encoders in self.MLEPEncoders:
            # For each encoder, load it first
            currentEncoder = self.MLEPEncoders[encoders]

            std_flush("Setting up", currentEncoder["name"], "at", readable_time())
            
            encoderName = currentEncoder["scriptName"]
            encoderModule = __import__("config.DataEncoder.%s"%encoderName, fromlist=[encoderName])
            encoderClass = getattr(encoderModule,encoderName)

            # Set up encoder(s)
            self.ENCODERS[currentEncoder["name"]] = encoderClass()

        
    def openDBConnection(self,):
        """ create a database connection to a SQLite database """
        try:
            #self.DB_CONN = sqlite3.connect("file::memory:?cache=shared", detect_types=sqlite3.PARSE_DECLTYPES)
            self.DB_CONN = sqlite3.connect(self.DB_FILE, detect_types=sqlite3.PARSE_DECLTYPES)
            #print(sqlite3.version)
        except Error as e:
            print(e)

    def shutdown(self):
        # save models - because they are all heald in memory??
        # Access the save path
        # pick.dump models to that path
        pass

        
        self.closeDBConnection()

        

    def closeDBConnection(self,):
        try:
            self.DB_CONN.close()
        except:
            pass
    
    def initializeDB(self):
        """ Initialize tables in database """
        # Initialize Model table
        cursor = self.DB_CONN.cursor()
        cursor.execute("""Drop Table IF EXISTS Models;""")
        cursor.execute("""CREATE TABLE Models
                                (   modelid         text,
                                    parentmodel     text,
                                    pipelineName    text, 
                                    timestamp       real,
                                    data_centroid   array,
                                    trainingModel   text,
                                    trainingData    text,
                                    testData        text,
                                    precision       real,
                                    recall          real,
                                    fscore          real,
                                    type            text,
                                    active          integer,
                                    PRIMARY KEY(modelid),
                                    FOREIGN KEY(parentmodel) REFERENCES Models(trainingModel))""")
        self.DB_CONN.commit()
        cursor.close()
    
    def updateTime(self,timerVal):
        """ Manually updating time for experimental evaluation """

        self.overallTimer = timerVal
        
        # Check scheduled time difference if there need to be updates
        if abs(self.overallTimer - self.scheduledFilterGenerateUpdateTimer) > self.scheduledSchedule:
            if not os.path.exists(self.SCHEDULED_DATA_FILE):
                # Something is the issue
                std_flush("No data for update")
                self.scheduledFilterGenerateUpdateTimer = self.overallTimer
            else:    
                # perform scheduled update
                
                # show lines in file
                num_lines = sum(1 for line in open(self.SCHEDULED_DATA_FILE))
                if num_lines == 0:
                    std_flush("Attempted update at", ms_to_readable(self.overallTimer), ", but ", num_lines,"data samples." )
                    
                else:  
                    std_flush("Scheduled update at", ms_to_readable(self.overallTimer), "with", num_lines,"data samples." )
                    
                    # TODO This is also a simplification in this implementation
                    # Normally, MLEPServer will use specialized data access routines
                    # So a user should write how to access data, and specify data format. For example, our data exists as lines in a file.
                    # Other data may exist as images in folders (a la common kaggle cat-dog datasets, etc)
                    # Other data may need to be streamed from somewhere
                    # In those cases, MLEPServer needs methods for proper data access given domain, as well as proper augmeentation policies
                    # Here, though, we follow KISS - Keep It Simple, Silly, and assume single type of data. We also assume data format (big NO NO)
                    scheduledTrainingData = self.getScheduledTrainingData()
                    
                    # Scheduled Generate
                    self.train(scheduledTrainingData)
                    std_flush("Completed Scheduled Model generation at", readable_time())

                    # Scheduled update
                    self.update(scheduledTrainingData,models='all')
                    std_flush("Completed Scheduled Model Update at", readable_time())

                    std_flush("Generated the following models: ", self.RECENT_MODELS)

                # These are models generated and updated in the prior update
                #self.RECENT_MODELS=[] <-- This is set up internal
                # Only generated models in prior update
                self.RECENT_NEW = self.getNewModelsSince(self.MLEPModelTimer)
                # Only update models in prior update
                self.RECENT_UPDATES = self.getUpdateModelsSince(self.MLEPModelTimer)
                # All models in prior update
                self.RECENT_MODELS = self.getModelsSince(self.MLEPModelTimer)
                # All models
                self.HISTORICAL = self.getModelsSince()
                # All generated models
                self.HISTORICAL_NEW = self.getNewModelsSince()
                # All update models
                self.HISTORICAL_UPDATES = self.getUpdateModelsSince()

                if len(self.RECENT_UPDATES) == 0:
                    # No update models found. Fall back on Recent New
                    self.RECENT_UPDATES = [item for item in self.RECENT_NEW]
                if len(self.HISTORICAL_UPDATES) == 0:
                    # No update models found. Fall back on Historical New
                    self.HISTORICAL_UPDATES = [item for item in self.HISTORICAL_NEW]
                
                # Update Model Timer
                self.MLEPModelTimer = time.time()

                std_flush("New Models: ", self.RECENT_NEW)
                std_flush("Update Models: ", self.RECENT_UPDATES)

                
                self.scheduledFilterGenerateUpdateTimer = self.overallTimer
                
                # delete file
                '''
                try:
                    os.remove(self.SCHEDULED_DATA_FILE)
                except:
                    pass
                '''
                open(self.SCHEDULED_DATA_FILE, 'w').close()

    def getScheduledTrainingData(self):
        """ Get the data in self.SCHEDULED_DATA_FILE """
        import random
        scheduledTrainingData = []
        scheduledNegativeData = []
        
        with open(self.SCHEDULED_DATA_FILE,'r') as data_file:
            for line in data_file:
                try:
                    _json = json.loads(line.strip())
                    if _json["label"] == 0:
                        scheduledNegativeData.append(_json)
                    else:
                        scheduledTrainingData.append(_json)
                except:
                    # Sometimes there are buffer errors because this is not a real-server. Production implementation uses REDIS Pub/Sub 
                    # to deliver messages. Using File IO causes more headaches than is worth, so we just ignore errors
                    pass
                
        # Threshold for augmentation is above or below 20% - 0.8 -- 1.2
        trainDataLength = len(scheduledTrainingData)
        negDataLength = len(scheduledNegativeData)
        
        if negDataLength < 0.8*trainDataLength:
            std_flush("Too few negative results. Adding more")
            scheduledTrainingData+=scheduledNegativeData
            if len(self.negatives) < 0.2*trainDataLength:
                scheduledTrainingData+=self.negatives
            else:
                scheduledTrainingData+=random.sample(self.negatives, trainDataLength-negDataLength)
        elif negDataLength > 1.2 *trainDataLength:
            # Too many negative data; we'll prune some
            std_flush("Too many  negative samples. Pruning")
            scheduledTrainingData += random.sample(scheduledNegativeData, trainDataLength)
        else:
            # Just right
            std_flush("No augmentation necessary")
            scheduledTrainingData+=scheduledNegativeData
                
        return scheduledTrainingData

    def generatePipeline(self,data, pipeline):
        """ Generate a model using provided pipeline """
        
        # Simplified pipeline. First entry is Encoder; Second entry is the actual Model

        encoderName = pipeline["sequence"][0]
        pipelineModel = pipeline["sequence"][1]

        # Perform lookup
        pipelineModelName = self.MLEPModels[pipelineModel]["scriptName"]
        pipelineModelModule = __import__("config.LearningModel.%s"%pipelineModelName, fromlist=[pipelineModelName])
        pipelineModelClass = getattr(pipelineModelModule,pipelineModelName)

        model = pipelineModelClass()
        X_train = self.ENCODERS[encoderName].batchEncode([item['text'] for item in data])
        centroid = X_train.mean(axis=0)
        y_train = [item['label'] for item in data]

        precision, recall, score = model.fit_and_test(X_train, y_train)

        return precision, recall, score, model, centroid

    def updatePipelineModel(self,data, modelSaveName, pipeline):
        """ Update a pipeline model using provided data """
        
        # Simplified pipeline. First entry is Encoder; Second entry is the actual Model
        
        # Need to set up encoder and pipeline using parent modelSaveName...

        encoderName = pipeline["sequence"][0]
        pipelineModel = pipeline["sequence"][1]

        # Perform lookup
        pipelineModelName = self.MLEPModels[pipelineModel]["scriptName"]
        pipelineModelModule = __import__("config.LearningModel.%s"%pipelineModelName, fromlist=[pipelineModelName])
        pipelineModelClass = getattr(pipelineModelModule,pipelineModelName)

        model = pipelineModelClass()
        model.clone(self.MODELS[modelSaveName])

        X_train = self.ENCODERS[encoderName].batchEncode([item['text'] for item in data])
        centroid = X_train.mean(axis=0)
        y_train = [item['label'] for item in data]

        precision, recall, score = model.update_and_test(X_train, y_train)

        return precision, recall, score, model, centroid
    
    def update(self, traindata, models='all'):
        # for each model in self.MODELS
        # create a copy; rename details across everything
        # update copy
        # push details to DB
        # if copy's source is in SELF.RECENT, add it to self.RECENT as well

        # TODO updated this approach because original version was exponential (YIKES!!!)
        modelSaveNames = [modelSaveName for modelSaveName in self.RECENT_MODELS]
        modelDetails = self.getModelDetails(modelSaveNames) # Gets fscore, pipelineName, modelSaveName
        self.RECENT_UPDATES = []
        pipelineNameDict = self.getDetails(modelDetails, 'pipelineName', 'dict')
        for modelSaveName in modelSaveNames:
            # copy model
            # set up new model
            
            # Check if model can be updated (some models cannot be updated)
            if not self.MODELS[modelSaveName].isUpdatable():
                continue

            currentPipeline = self.MLEPPipelines[pipelineNameDict[modelSaveName]]
            precision, recall, score, pipelineTrained, data_centroid = self.updatePipelineModel(traindata, modelSaveName, currentPipeline)
            timestamp = time.time()
            modelIdentifier = time_to_id(timestamp)
            modelSavePath = "_".join([currentPipeline["name"], modelIdentifier])
            trainDataSavePath = ""
            testDataSavePath = ""
            # TODO add parent model for this model!!!!!

            # save the model (i.e. host it)
            self.MODELS[modelSavePath] = pipelineTrained
            # Because we are simplifying this implementation, we don't actually have pipeline families. Every pipelien is part of the w2v family
            # So we can actually just store data_centroids locally
            self.CENTROIDS[modelSavePath] = data_centroid
            del pipelineTrained
            # Now we save deets.
            # Some cleaning
            
            columns=",".join([  "modelid","parentmodel","pipelineName","timestamp","data_centroid",
                                "trainingModel","trainingData","testData",
                                "precision","recall","fscore",
                                "type","active"])
            
            sql = "INSERT INTO Models (%s) VALUES " % columns
            sql += "(?,?,?,?,?,?,?,?,?,?,?,?,?)"
            cursor = self.DB_CONN.cursor()

            
            cursor.execute(sql, (   modelIdentifier,
                                    str(modelSaveName),
                                    str(currentPipeline["name"]), 
                                    timestamp,
                                    data_centroid,
                                    str(modelSavePath),
                                    str(trainDataSavePath),
                                    str(testDataSavePath),
                                    precision,
                                    recall,
                                    score,
                                    str(currentPipeline["type"]),
                                    1))
            
            self.DB_CONN.commit()
            cursor.close()


    def initialTrain(self,traindata,models= "all"):

        self.train(traindata)
        self.TRAIN_MODELS = self.getModelsSince()


    def train(self,traindata, models = 'all'):
        # for each modelType in modelTypes
        #   for each encodingType (just 1)
        #       Create sklearn model using default details
        #       then train sklearn model using encoded data
        #       precision, recall, score, model = self.generate(encoder, traindata, model)
        #       push details to ModelDB
        #       save model to file using ID as filename.model -- serialized sklearn model
        
        

        # First load the Model configurations - identify what models exist
        
        for pipeline in self.MLEPPipelines:
            
            
            # We make the simplified assumption that all encoders are the same (pretrained w2v). 
            # So we don't have to handle pipeline families at this point for the distance function (if implemented)
            # Also, since our models are small-ish, we can make do by hosting models in memory
            # Production implementation (and going forward), models would be hosted as an API endpoint until "retirement"

            #std_flush("Setting up", currentEncoder["name"], "at", readable_time())
            
            # set up pipeline
            currentPipeline = self.MLEPPipelines[pipeline]
            precision, recall, score, pipelineTrained, data_centroid = self.generatePipeline(traindata, currentPipeline)
            timestamp = time.time()
            modelIdentifier = time_to_id(timestamp)
            modelSavePath = "_".join([currentPipeline["name"], modelIdentifier])
            trainDataSavePath = ""
            testDataSavePath = ""

            # save the model (i.e. host it)
            self.MODELS[modelSavePath] = pipelineTrained
            # Because we are simplifying this implementation, we don't actually have pipeline families. Every pipelien is part of the w2v family
            # So we can actually just store data_centroids locally
            self.CENTROIDS[modelSavePath] = data_centroid
            del pipelineTrained
            # Now we save deets.
            # Some cleaning
            
            columns=",".join([  "modelid","parentmodel","pipelineName","timestamp","data_centroid",
                                "trainingModel","trainingData","testData",
                                "precision","recall","fscore",
                                "type","active"])
            
            sql = "INSERT INTO Models (%s) VALUES " % columns
            sql += "(?,?,?,?,?,?,?,?,?,?,?,?,?)"
            cursor = self.DB_CONN.cursor()
            
            cursor.execute(sql, (   modelIdentifier,
                                    None,
                                    str(currentPipeline["name"]), 
                                    timestamp,
                                    data_centroid,
                                    str(modelSavePath),
                                    str(trainDataSavePath),
                                    str(testDataSavePath),
                                    precision,
                                    recall,
                                    score,
                                    str(currentPipeline["type"]),
                                    1))
            
            self.DB_CONN.commit()
            cursor.close()

        

    def load_json(self,json_):
        return json.load(codecs.open(json_, encoding='utf-8'))

    
    def addNegatives(self,negatives):
        self.negatives = negatives



    def getModelsSince(self, _time = None):
        cursor = self.DB_CONN.cursor()
        if _time is None:
            # We are getting ALL models
            sql = "select trainingModel from Models"
        else:
            # We are getting models since a time
            sql = "select trainingModel from Models where timestamp > %s" % _time
        
        cursor.execute(sql)
        tupleResults = cursor.fetchall()
        cursor.close()
        return [item[0] for item in tupleResults]

    def getNewModelsSince(self, _time = None):
        cursor = self.DB_CONN.cursor()
        if _time is None:
            # We are getting ALL models
            sql = "select trainingModel from Models where parentmodel IS NULL"
        else:
            # We are getting models since a time
            sql = "select trainingModel from Models where timestamp > %s and parentmodel IS NULL" % _time
        
        cursor.execute(sql)
        tupleResults = cursor.fetchall()
        cursor.close()
        return [item[0] for item in tupleResults]
        
        
    def getUpdateModelsSince(self, _time = None):
        cursor = self.DB_CONN.cursor()
        if _time is None:
            # We are getting ALL models
            sql = "select trainingModel from Models where parentmodel IS NOT NULL"
        else:
            # We are getting models since a time
            sql = "select trainingModel from Models where timestamp > %s and parentmodel IS NOT NULL" % _time
        
        cursor.execute(sql)
        tupleResults = cursor.fetchall()
        cursor.close()
        return [item[0] for item in tupleResults]

    def getModelDetails(self,ensembleModelNames, toGet = None):
        cursor = self.DB_CONN.cursor()
        if toGet is None:
            toGet = ["trainingModel","fscore","pipelineName"]
        sql = "select " + ",".join(toGet) + " from Models where trainingModel in ({seq})".format(seq=",".join(["?"]*len(ensembleModelNames)))
        cursor.execute(sql,ensembleModelNames)
        tupleResults = cursor.fetchall()
        cursor.close()
        dictResults = {}
        for entry in tupleResults:
            dictResults[entry[0]] = {}
            for idx,val in enumerate(toGet):
                dictResults[entry[0]][val] = entry[idx]
        return dictResults

    def getDetails(self,dataDict,keyVal,_format, order=None):
        if _format == "list":
            if order is None:
                # We need the order for lists
                assert(1==2)
            details = []
            details = [dataDict[item][keyVal] for item in order]
            return details
        if _format == "dict":
            details = {item:dataDict[item][keyVal] for item in dataDict}
            return details

    def getPipelineToModel(self,):
        cursor = self.DB_CONN.cursor()
        sql = "select pipelineName, trainingModel, fscore from Models"
        cursor.execute(sql)
        tupleResults = cursor.fetchall()
        cursor.close()
        dictResults = {}
        for entry in tupleResults:
            if entry[0] not in dictResults:
                dictResults[entry[0]] = []
            dictResults[entry[0]].append((entry[1], entry[2]))
        return dictResults

    def classify(self, data):
        if self.MLEPConfig["select_method"] == "recent":
            # Step one - get list of model ids
            ensembleModelNames = [item for item in self.RECENT_MODELS]
        elif self.MLEPConfig["select_method"] == "recent-new":
            ensembleModelNames = [item for item in self.RECENT_NEW]
        elif self.MLEPConfig["select_method"] == "recent-updates":
            ensembleModelNames = [item for item in self.RECENT_UPDATES]
        elif self.MLEPConfig["select_method"] == "train":
            ensembleModelNames = [item for item in self.TRAIN_MODELS]
        elif self.MLEPConfig["select_method"] == "nearest":
            k_val = self.MLEPConfig["nearest-k"]

            # Basic optimization:
            if k_val >= len(self.HISTORICAL):
                ensembleModelNames = [item for item in self.HISTORICAL]
            else:

                # We have the k_val
                # Normally, this part would use a DataModel construct (not implemented) to get the appropriate "distance" model for a specific data point
                # But we make the assumption that all data is encoded, etc, etc, and use the encoders to get distance.

                # 1. First, collect list of Encoders
                # 2. Then create mapping of encoders -- model_save_path
                # 3. Then for each encoder, find k-closest model_save_path
                # 4. Put them all together and sort on performance
                # 5. Return top-k (so two levels of k, finally returning k models)


                # 1. First, collect list of Encoders -- model mapping
                pipelineToModel = self.getPipelineToModel()
                
                # 2. Then create mapping of encoders -- model_save_path
                encoderToModel = {}
                for _pipeline in pipelineToModel:
                    # Multiple pipelines can have the same encoder
                    if self.MLEPPipelines[_pipeline]["sequence"][0] not in encoderToModel:
                        encoderToModel[self.MLEPPipelines[_pipeline]["sequence"][0]] = []
                    # encoderToModel[PIPELINE_NAME] = [(MODEL_NAME, PERF),(MODEL_NAME, PERF)]
                    encoderToModel[self.MLEPPipelines[_pipeline]["sequence"][0]] += pipelineToModel[_pipeline]
                
                # 3. Then for each encoder, find k-closest model_save_path
                kClosestPerEncoder = {}
                for _encoder in encoderToModel:
                    kClosestPerEncoder[_encoder] = []
                    _encodedData = self.ENCODERS[_encoder].encode(data["text"])
                    # Find distance to all appropriate models
                    # Then sort and take top-5
                    # This can probably be optimized to not perform unneeded Distance calculations (if, e.g. two models have the same training dataset - something to consider)
                    # kCPE[E] = [ (NORM(encoded - centroid(modelName), performance, modelName) ... ]
                    kClosestPerEncoder[_encoder]=[(np.linalg.norm(_encodedData-self.CENTROIDS[item[0]]), item[1], item[0]) for item in encoderToModel[_encoder]]
                    # Default sort on first param (norm); sort on distance - smallest to largest
                    kClosestPerEncoder[_encoder].sort(key=lambda tup:tup[0], )
                    # Truncate to top-k
                    kClosestPerEncoder[_encoder] = kClosestPerEncoder[_encoder][:k_val]

                # 4. Put them all together and sort on performance
                kClosest = []
                for _encoder in kClosestPerEncoder:
                    kClosest+=kClosestPerEncoder[_encoder]
                kClosest.sort(key=lambda tup:tup[1], reverse=True)

                # 5. Return top-k (so two levels of k, finally returning k models)
                kClosest = kClosest[:k_val]
                ensembleModelNames = [item[2] for item in kClosest]

        else:
            #recent-new
            ensembleModelNames = [item for item in self.RECENT_NEW]

        # Run the sqlite query to get model details
        modelDetails = self.getModelDetails(ensembleModelNames)
            
        if self.MLEPConfig["weight_method"] == "performancce":
            # request DB for performance (f-score)
            weights = self.getDetails(modelDetails, 'fscore', 'list', order=ensembleModelNames)
            sumWeights = sum(weights)
            weights = [item/sumWeights for item in weights]
        elif self.MLEPConfig["weight_method"] == "unweighted":
            weights = len(ensembleModelNames)*[1.0/len(ensembleModelNames)]
        else:
            weights = len(ensembleModelNames)*[1.0/len(ensembleModelNames)]
        

        # TODO; Another simplification for this implementation. Assume binary classifier, and have built in Ensemble weighting
        # Yet another simplification - single encoder.
        # Production environment - MLEP will use the 'type' field of pipeline
        # Binary - between 0 and 1. Trivial. If weighted average  is >0.5, it's 1, else, it's 0
        # Multiclass - Weighted average. if greater than [INT].5, round up. else round down
        # Regression - weighted average
        # All members of modellist MUST be of the same type. You can't mix binary and multiclass

        # Get encoder types in ensembleModelNames                       
        # build local dictionary of data --> encodedVersion             
        pipelineNameDict = self.getDetails(modelDetails, 'pipelineName', 'dict')
        localEncoder = {}
        for modelName in pipelineNameDict:
            pipelineName = pipelineNameDict[modelName]
            localEncoder[self.MLEPPipelines[pipelineName]["sequence"][0]] = 0
        
        for encoder in localEncoder:
            localEncoder[encoder] = self.ENCODERS[encoder].encode(data['text'])


        classification = 0
        for idx,_name in enumerate(ensembleModelNames):
            # use the prescribed enc; ensembleModelNames are the modelSaveFile
            # We need the pipeline each is associated with (so that we can extract front-loaded encoder)
            
            # So we get the model name, access the pipeline name from pipelineNameDict
            # Then get the encodername from sequence[0]
            # Then get the locally encoded thingamajig of the data
            # And pass it into predict()
            cls_=self.MODELS[_name].predict(localEncoder[self.MLEPPipelines[pipelineNameDict[_name]]["sequence"][0]])
            classification+= weights[idx]*cls_

        return 0 if classification < 0.5 else 1





class MLEPPredictionServer():
    def __init__(self,):
        # Initialize Prediction Server
        # Set up storage directories
        self.SOURCE_DIR = './.MLEPServer'
        self.setups = ['models', 'data', 'modelSerials', 'db']
        
        self.SCHEDULED_DATA_FILE = './.MLEPServer/data/scheduledFile.json'
        self.SCHEDULED_DATA_FILE_OPERATOR = open(self.SCHEDULED_DATA_FILE, 'a')

    def classify(self,data, MLEPLearner):
        # sve data item to scheduledDataFile
        try:
            self.SCHEDULED_DATA_FILE_OPERATOR.write(json.dumps(data)+'\n')
        except:
            self.SCHEDULED_DATA_FILE_OPERATOR = open(self.SCHEDULED_DATA_FILE, 'a')
            self.SCHEDULED_DATA_FILE_OPERATOR.write(json.dumps(data)+'\n')

        return MLEPLearner.classify(data)
            

